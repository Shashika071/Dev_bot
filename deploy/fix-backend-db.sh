#!/usr/bin/env bash
# Fix backend crash: "Name or service not known" / bad DATABASE_URL host.
# Run on VPS from project root:
#   chmod +x deploy/fix-backend-db.sh
#   ./deploy/fix-backend-db.sh

set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ ! -f .env.prod ]]; then
  echo "Missing .env.prod"
  exit 1
fi

# Load values without executing the file as shell beyond KEY=VALUE
POSTGRES_DB="$(grep -E '^POSTGRES_DB=' .env.prod | tail -1 | cut -d= -f2-)"
POSTGRES_USER="$(grep -E '^POSTGRES_USER=' .env.prod | tail -1 | cut -d= -f2-)"
POSTGRES_PASSWORD="$(grep -E '^POSTGRES_PASSWORD=' .env.prod | tail -1 | cut -d= -f2-)"
POSTGRES_DB="${POSTGRES_DB:-deriv_bot}"
POSTGRES_USER="${POSTGRES_USER:-deriv}"

if [[ -z "${POSTGRES_PASSWORD}" || "${POSTGRES_PASSWORD}" == "change_me_strong_password" ]]; then
  echo "Set a real POSTGRES_PASSWORD in .env.prod first."
  exit 1
fi

# URL-encode password (handles @ # : / etc.)
ENC_PASS="$(
  POSTGRES_PASSWORD="$POSTGRES_PASSWORD" python3 - <<'PY'
import os, urllib.parse
print(urllib.parse.quote(os.environ["POSTGRES_PASSWORD"], safe=""))
PY
)"

NEW_URL="postgresql+asyncpg://${POSTGRES_USER}:${ENC_PASS}@postgres:5432/${POSTGRES_DB}"

# Prefer a simple alphanumeric password to avoid .env / URL parse bugs
if [[ "${POSTGRES_PASSWORD}" =~ [^A-Za-z0-9_-] ]]; then
  echo "WARNING: POSTGRES_PASSWORD has special characters."
  echo "If DB was already initialized with that password, keep it (URL-encoded below)."
  echo "For a fresh deploy, prefer letters/numbers only, then wipe ./pgdata carefully."
fi

# Rewrite DATABASE_URL line (host MUST be postgres — docker service name)
NEW_URL="$NEW_URL" python3 - <<'PY'
import os
from pathlib import Path
path = Path(".env.prod")
text = path.read_text(encoding="utf-8")
new_url = os.environ["NEW_URL"]
lines = []
found = False
for line in text.splitlines():
    if line.startswith("DATABASE_URL="):
        lines.append(f"DATABASE_URL={new_url}")
        found = True
    else:
        lines.append(line)
if not found:
    lines.append(f"DATABASE_URL={new_url}")
path.write_text("\n".join(lines) + "\n", encoding="utf-8")
print("DATABASE_URL host set to postgres (password URL-encoded)")
PY

echo "Recreating backend + worker..."
docker compose -p deriv -f docker-compose.prod.yml --env-file .env.prod up -d --force-recreate backend worker

sleep 4
echo
echo "=== env check inside backend (password redacted) ==="
docker exec deriv-backend-1 sh -c 'echo "$DATABASE_URL" | sed -E "s#://([^:]+):([^@]+)@#://\1:***@#"' || true
echo
echo "=== DNS check: can backend resolve postgres? ==="
docker exec deriv-backend-1 getent hosts postgres || docker exec deriv-backend-1 python -c "import socket; print(socket.getaddrinfo('postgres', 5432))" || true
echo
echo "=== backend status ==="
docker ps -a --filter name=deriv-backend-1 --format 'table {{.Names}}\t{{.Status}}'
echo
echo "=== backend logs (tail) ==="
docker logs deriv-backend-1 --tail 40 || true
echo
echo "If still failing, paste the env check + DNS check + logs above."
