#!/usr/bin/env bash
# Fully auto HTTPS on :8443 (does NOT touch Fiyola :80/:443).
#
#   chmod +x deploy/auto-ssl.sh
#   ./deploy/auto-ssl.sh
#
# Optional:
#   CERT_MODE=origin   # require Cloudflare origin certs (no self-signed)
#   DERIV_HTTPS_PORT=8443
#   DOMAIN_UI=devbot.crexline.com
#   DOMAIN_API=backdev.crexline.com

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PORT="${DERIV_HTTPS_PORT:-8443}"
DOMAIN_UI="${DOMAIN_UI:-devbot.crexline.com}"
DOMAIN_API="${DOMAIN_API:-backdev.crexline.com}"
CERT_MODE="${CERT_MODE:-auto}"   # auto | origin | selfsigned
CERT_DIR="$ROOT/certs"
PEM="$CERT_DIR/origin.pem"
KEY="$CERT_DIR/origin.key"

echo "==> Deriv auto HTTPS setup (port ${PORT})"
echo "    UI:  https://${DOMAIN_UI}:${PORT}"
echo "    API: https://${DOMAIN_API}:${PORT}"
echo "    Fiyola 80/443 will NOT be changed."
echo

# --- .env.prod ---
if [[ ! -f .env.prod ]]; then
  if [[ -f .env.prod.example ]]; then
    cp .env.prod.example .env.prod
    echo "Created .env.prod from example — set POSTGRES_PASSWORD if still placeholder."
  else
    echo "Missing .env.prod"
    exit 1
  fi
fi

# Ensure DB password is not placeholder
if grep -qE '^POSTGRES_PASSWORD=(change_me_strong_password)?$' .env.prod; then
  NEW_PW="DerivBot$(date +%Y%m%d)X"
  python3 - <<PY
from pathlib import Path
p = Path(".env.prod")
lines = []
pw = "${NEW_PW}"
for line in p.read_text(encoding="utf-8").splitlines():
    if line.startswith("POSTGRES_PASSWORD="):
        lines.append(f"POSTGRES_PASSWORD={pw}")
    elif line.startswith("DATABASE_URL="):
        lines.append(f"DATABASE_URL=postgresql+asyncpg://deriv:{pw}@postgres:5432/deriv_bot")
    else:
        lines.append(line)
# ensure both exist
text = "\n".join(lines)
if "POSTGRES_PASSWORD=" not in text:
    text += f"\nPOSTGRES_PASSWORD={pw}\n"
if "DATABASE_URL=" not in text:
    text += f"\nDATABASE_URL=postgresql+asyncpg://deriv:{pw}@postgres:5432/deriv_bot\n"
p.write_text(text + "\n", encoding="utf-8")
print(f"Set POSTGRES_PASSWORD to {pw}")
PY
fi

# Patch HTTPS browser URLs into .env.prod
python3 - <<PY
from pathlib import Path
p = Path(".env.prod")
wanted = {
    "DERIV_HTTPS_PORT": "${PORT}",
    "VITE_API_URL": "https://${DOMAIN_API}:${PORT}",
    "VITE_WS_URL": "wss://${DOMAIN_API}:${PORT}",
    "CORS_ORIGINS": "https://${DOMAIN_UI}:${PORT},https://${DOMAIN_API}:${PORT}",
}
keys = set(wanted)
lines = []
seen = set()
for line in p.read_text(encoding="utf-8").splitlines():
    if "=" in line and not line.strip().startswith("#"):
        k = line.split("=", 1)[0].strip()
        if k in wanted:
            lines.append(f"{k}={wanted[k]}")
            seen.add(k)
            continue
    lines.append(line)
for k, v in wanted.items():
    if k not in seen:
        lines.append(f"{k}={v}")
p.write_text("\n".join(lines) + "\n", encoding="utf-8")
print("Updated .env.prod HTTPS URLs for port ${PORT}")
PY

# Randomize placeholder secrets if still default
python3 - <<'PY'
import secrets
from pathlib import Path
p = Path(".env.prod")
text = p.read_text(encoding="utf-8")
repl = {
    "SECRET_KEY=generate_a_long_random_secret": f"SECRET_KEY={secrets.token_hex(32)}",
    "INTERNAL_API_SECRET=generate_another_long_random_secret": f"INTERNAL_API_SECRET={secrets.token_hex(32)}",
}
for a, b in repl.items():
    if a in text:
        text = text.replace(a, b)
        print("Rotated", a.split("=", 1)[0])
p.write_text(text, encoding="utf-8")
PY

# --- certificates ---
mkdir -p "$CERT_DIR"

have_certs=false
if [[ -s "$PEM" && -s "$KEY" ]]; then
  have_certs=true
  echo "Using existing certs: $PEM"
fi

if [[ "$have_certs" == false ]]; then
  if [[ "$CERT_MODE" == "origin" ]]; then
    cat <<EOF
ERROR: Cloudflare Origin certs required but missing:
  $PEM
  $KEY

Create in Cloudflare → SSL/TLS → Origin Server → Create Certificate
Hostnames: ${DOMAIN_UI}, ${DOMAIN_API}
Paste files, then re-run.
EOF
    exit 1
  fi

  echo "No origin cert found — generating self-signed cert (OK with Cloudflare SSL mode: Full)."
  openssl req -x509 -nodes -newkey rsa:2048 -days 825 \
    -keyout "$KEY" \
    -out "$PEM" \
    -subj "/CN=${DOMAIN_UI}" \
    -addext "subjectAltName=DNS:${DOMAIN_UI},DNS:${DOMAIN_API}"
  chmod 600 "$KEY"
  echo "Created self-signed certs in certs/"
  echo "For browser-trusted origin later: replace with Cloudflare Origin Certificate."
fi

# --- firewall ---
if command -v ufw >/dev/null 2>&1; then
  if ufw status 2>/dev/null | grep -qi "Status: active"; then
    ufw allow "${PORT}/tcp" comment "deriv-bot-https" || true
    echo "Opened ufw ${PORT}/tcp"
  fi
fi

# --- ensure edge nginx config exists ---
if [[ ! -f deploy/edge/nginx.conf ]]; then
  echo "Missing deploy/edge/nginx.conf"
  exit 1
fi

# Patch nginx.conf hostnames if custom domains were provided
python3 - <<PY
from pathlib import Path
path = Path("deploy/edge/nginx.conf")
text = path.read_text(encoding="utf-8")
text = text.replace("devbot.crexline.com", "${DOMAIN_UI}")
text = text.replace("backdev.crexline.com", "${DOMAIN_API}")
# if already replaced, fine; write listen port
import re
text = re.sub(r"listen\s+\d+\s+ssl", "listen ${PORT} ssl", text)
path.write_text(text, encoding="utf-8")
print("edge nginx.conf hostnames/port ready")
PY

# Also patch compose published port mapping uses env DERIV_HTTPS_PORT
# container still listens on 8443 in image config — keep container listen 8443,
# map host PORT -> 8443. Restore nginx listen to 8443 inside container.
python3 - <<'PY'
from pathlib import Path
import re
path = Path("deploy/edge/nginx.conf")
text = path.read_text(encoding="utf-8")
text = re.sub(r"listen\s+\d+\s+ssl", "listen 8443 ssl", text)
path.write_text(text, encoding="utf-8")
PY

echo
echo "==> Starting stack..."
docker compose -p deriv \
  -f docker-compose.prod.yml \
  -f docker-compose.ssl-ports.yml \
  --env-file .env.prod \
  up -d --build

sleep 6
echo
docker compose -p deriv \
  -f docker-compose.prod.yml \
  -f docker-compose.ssl-ports.yml \
  --env-file .env.prod ps

echo
echo "==> Health check"
set +e
curl -sk --max-time 10 "https://127.0.0.1:${PORT}/health" -H "Host: ${DOMAIN_API}" || \
  docker exec deriv-backend-1 curl -s --max-time 5 http://127.0.0.1:8000/health
set -e

echo
echo "DONE."
echo "  UI:  https://${DOMAIN_UI}:${PORT}"
echo "  API: https://${DOMAIN_API}:${PORT}/health"
echo
echo "Cloudflare:"
echo "  - A records ${DOMAIN_UI%%.*} / ${DOMAIN_API%%.*} → this VPS IP"
echo "  - Proxy can be ON (orange). Port ${PORT} is supported."
echo "  - SSL/TLS mode: Full  (use Full Strict only with Cloudflare Origin cert)"
echo "Fiyola was not modified."
