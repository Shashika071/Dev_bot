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

# Rewrite DATABASE_URL line (host MUST be postgres — docker service name)
if grep -qE '^DATABASE_URL=' .env.prod; then
  sed -i.bak "s|^DATABASE_URL=.*|DATABASE_URL=${NEW_URL}|" .env.prod
else
  echo "DATABASE_URL=${NEW_URL}" >> .env.prod
fi

echo "Updated DATABASE_URL host to: postgres"
echo "Recreating backend + worker..."

docker compose -p deriv -f docker-compose.prod.yml --env-file .env.prod up -d --force-recreate backend worker

sleep 3
echo
echo "=== backend status ==="
docker ps -a --filter name=deriv-backend-1 --format 'table {{.Names}}\t{{.Status}}'
echo
echo "=== backend logs (tail) ==="
docker logs deriv-backend-1 --tail 30 || true
echo
echo "If still failing, paste: docker logs deriv-backend-1 --tail 80"
