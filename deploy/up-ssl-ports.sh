#!/usr/bin/env bash
# Start bot with its own HTTPS on :8443 (Fiyola :80/:443 untouched).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ ! -f .env.prod ]]; then
  echo "Missing .env.prod"
  exit 1
fi

if [[ ! -f certs/origin.pem || ! -f certs/origin.key ]]; then
  cat <<EOF
Missing Cloudflare Origin cert files:
  $ROOT/certs/origin.pem
  $ROOT/certs/origin.key

Create them in Cloudflare:
  1) Cloudflare → crexline.com → SSL/TLS → Origin Server → Create Certificate
  2) Hostnames: devbot.crexline.com, backdev.crexline.com  (or *.crexline.com)
  3) Save Origin Certificate → certs/origin.pem
  4) Save Private Key       → certs/origin.key
  5) SSL/TLS mode → Full (or Full Strict)

Then re-run: ./deploy/up-ssl-ports.sh
EOF
  mkdir -p certs
  exit 1
fi

# Ensure .env.prod points browser at :8443 HTTPS
if ! grep -q 'VITE_API_URL=https://backdev.crexline.com:8443' .env.prod 2>/dev/null; then
  echo "NOTE: set these in .env.prod before build for correct API URL:"
  echo "  VITE_API_URL=https://backdev.crexline.com:8443"
  echo "  VITE_WS_URL=wss://backdev.crexline.com:8443"
  echo "  CORS_ORIGINS=https://devbot.crexline.com:8443,https://backdev.crexline.com:8443"
fi

docker compose -p deriv \
  -f docker-compose.prod.yml \
  -f docker-compose.ssl-ports.yml \
  --env-file .env.prod \
  up -d --build

echo
docker compose -p deriv \
  -f docker-compose.prod.yml \
  -f docker-compose.ssl-ports.yml \
  --env-file .env.prod ps

echo
echo "UI:  https://devbot.crexline.com:8443"
echo "API: https://backdev.crexline.com:8443/health"
echo "Fiyola :80/:443 were not changed."
