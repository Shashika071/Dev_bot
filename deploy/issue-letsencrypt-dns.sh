#!/usr/bin/env bash
# Option 2: Real HTTPS on :8443 using Let's Encrypt DNS challenge.
# - Domain DNS can stay on Spaceship
# - Does NOT use host :80/:443 (Fiyola untouched)
#
# You will add 1–2 TXT records in Spaceship when Certbot asks.
#
#   chmod +x deploy/issue-letsencrypt-dns.sh
#   ./deploy/issue-letsencrypt-dns.sh
#
# Optional:
#   CERTBOT_EMAIL=you@email.com ./deploy/issue-letsencrypt-dns.sh

set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

DOMAIN_UI="${DOMAIN_UI:-devbot.crexline.com}"
DOMAIN_API="${DOMAIN_API:-backdev.crexline.com}"
EMAIL="${CERTBOT_EMAIL:-}"
LE_DIR="$ROOT/certs/letsencrypt"
WORK_DIR="$ROOT/certs/certbot-work"
LOGS_DIR="$ROOT/certs/certbot-logs"

mkdir -p "$LE_DIR" "$WORK_DIR" "$LOGS_DIR" "$ROOT/certs"

if [[ -z "$EMAIL" ]]; then
  if grep -qE '^CERTBOT_EMAIL=.' .env.prod 2>/dev/null; then
    EMAIL="$(grep -E '^CERTBOT_EMAIL=' .env.prod | tail -1 | cut -d= -f2-)"
  else
    read -r -p "Email for Let's Encrypt notices: " EMAIL
  fi
fi

if [[ -z "$EMAIL" ]]; then
  echo "Email required."
  exit 1
fi

HOOKS="$ROOT/deploy/certbot-hooks"
chmod +x "$HOOKS/auth.sh" "$HOOKS/cleanup.sh"

cat <<EOF

============================================================
Let's Encrypt DNS challenge (Spaceship)
============================================================
Domains:
  - ${DOMAIN_UI}
  - ${DOMAIN_API}

Script will PRINT the TXT host + value, then WAIT until Google DNS
sees it (no Enter needed). Keep Spaceship open and:

  1) Add/update TXT
       Host:  _acme-challenge.devbot   or   _acme-challenge.backdev
       Value: (exact string printed)
  2) Leave the terminal alone until it says "DNS OK"

You may get one or two prompts (one per domain).
============================================================

EOF

read -r -p "Press Enter to start Certbot..." _

# Manual DNS with auth-hook that polls Google DNS before continuing
docker run --rm -it \
  -v "$LE_DIR:/etc/letsencrypt" \
  -v "$WORK_DIR:/var/lib/letsencrypt" \
  -v "$LOGS_DIR:/var/log/letsencrypt" \
  -v "$HOOKS:/hooks:ro" \
  certbot/certbot certonly \
  --manual \
  --preferred-challenges dns \
  --manual-auth-hook /hooks/auth.sh \
  --manual-cleanup-hook /hooks/cleanup.sh \
  --agree-tos \
  --no-eff-email \
  --email "$EMAIL" \
  -d "$DOMAIN_UI" \
  -d "$DOMAIN_API" \
  --cert-name crexline-devbot

LIVE="$LE_DIR/live/crexline-devbot"
if [[ ! -f "$LIVE/fullchain.pem" || ! -f "$LIVE/privkey.pem" ]]; then
  # docker volume path from host
  if [[ ! -f "$LIVE/fullchain.pem" ]]; then
    echo "Cert files not found at $LIVE"
    echo "Listing:"
    find "$LE_DIR" -maxdepth 3 -type f -name '*.pem' | head -50 || true
    exit 1
  fi
fi

# Our edge nginx expects these names
cp -f "$LIVE/fullchain.pem" "$ROOT/certs/origin.pem"
cp -f "$LIVE/privkey.pem" "$ROOT/certs/origin.key"
chmod 600 "$ROOT/certs/origin.key"
echo "Installed certs → certs/origin.pem + certs/origin.key"

# Ensure app URLs use https :8443
python3 - <<PY
from pathlib import Path
p = Path(".env.prod")
if not p.exists():
    raise SystemExit("Missing .env.prod")
wanted = {
    "DERIV_HTTPS_PORT": "8443",
    "VITE_API_URL": "https://${DOMAIN_API}:8443",
    "VITE_WS_URL": "wss://${DOMAIN_API}:8443",
    "CORS_ORIGINS": "https://${DOMAIN_UI}:8443,https://${DOMAIN_API}:8443",
    "CERTBOT_EMAIL": "${EMAIL}",
}
lines, seen = [], set()
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
print("Updated .env.prod HTTPS URLs")
PY

echo "Starting / recreating HTTPS edge on :8443..."
docker compose -p deriv \
  -f docker-compose.prod.yml \
  -f docker-compose.ssl-ports.yml \
  --env-file .env.prod \
  up -d --build

sleep 4
docker compose -p deriv \
  -f docker-compose.prod.yml \
  -f docker-compose.ssl-ports.yml \
  --env-file .env.prod ps

echo
echo "DONE — trusted Let's Encrypt cert installed."
echo "  UI:  https://${DOMAIN_UI}:8443"
echo "  API: https://${DOMAIN_API}:8443/health"
echo
echo "Open in browser (hard refresh). No Cloudflare orange cloud needed."
echo "Renew before 90 days: run this script again (new TXT records)."
