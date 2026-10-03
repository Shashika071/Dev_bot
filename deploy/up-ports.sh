#!/usr/bin/env bash
# Start bot on :8080 (UI) and :8001 (API). Does not touch Fiyola :80/:443.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ ! -f .env.prod ]]; then
  echo "Missing .env.prod"
  exit 1
fi

echo "Starting deriv on custom ports (default UI:8080 API:8001)..."
docker compose -p deriv \
  -f docker-compose.prod.yml \
  -f docker-compose.ports.yml \
  --env-file .env.prod \
  up -d --build

echo
docker compose -p deriv -f docker-compose.prod.yml -f docker-compose.ports.yml --env-file .env.prod ps
echo
echo "Open UI:  http://YOUR_VPS_IP:8080"
echo "API:      http://YOUR_VPS_IP:8001/health"
echo "Or:       http://devbot.crexline.com:8080  (Cloudflare DNS-only / grey cloud)"
echo
echo "Fiyola ports 80/443 were not changed."
