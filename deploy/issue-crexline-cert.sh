#!/usr/bin/env bash
# Issue Let's Encrypt cert for devbot + backdev, then enable HTTPS.
#
# Cloudflare: set both records to DNS-only (grey cloud) while issuing,
# then turn orange cloud back on.
#
#   chmod +x deploy/issue-crexline-cert.sh
#   ./deploy/issue-crexline-cert.sh

set -euo pipefail

WWW="/home/fiyola-production/fiyola/certbot/www"
CONF="/home/fiyola-production/fiyola/certbot/conf"
NGINX_CONF="/home/fiyola-production/fiyola/nginx/conf.d/production.conf"
DIR="$(cd "$(dirname "$0")" && pwd)"
HTTP_SNIPPET="$DIR/crexline-devbot-http.conf"
HTTPS_SNIPPET="$DIR/crexline-devbot-https.conf"
EMAIL="${CERTBOT_EMAIL:-admin@crexline.com}"

if [[ ! -d "$WWW" || ! -d "$CONF" ]]; then
  echo "Certbot dirs not found under /home/fiyola-production/fiyola/certbot"
  exit 1
fi

# Remove broken previous crexline HTTPS block that references missing cert
if grep -q "crexline-devbot/fullchain.pem" "$NGINX_CONF" 2>/dev/null; then
  echo "Removing incomplete crexline SSL blocks that reference missing cert..."
  python3 - <<'PY'
from pathlib import Path
path = Path("/home/fiyola-production/fiyola/nginx/conf.d/production.conf")
text = path.read_text(encoding="utf-8")
# Drop any server blocks that mention crexline-devbot cert or our markers
import re
# Remove from first crexline marker / server_name with both hosts through EOF if appended at end
patterns = [
    r"\n# --- HTTP: ACME.*",
    r"\n# Phase 1 — HTTP only.*",
    r"\n# Append to:.*",
    r"\n# UI — https://devbot\.crexline\.com.*",
    r"\n# HTTP: ACME.*",
]
# Simpler: strip every server { ... } that contains crexline.com
out = []
i = 0
lines = text.splitlines(keepends=True)
while i < len(lines):
    line = lines[i]
    if line.lstrip().startswith("server {") or line.lstrip().startswith("server{"):
        block = [line]
        i += 1
        depth = line.count("{") - line.count("}")
        while i < len(lines) and depth > 0:
            block.append(lines[i])
            depth += lines[i].count("{") - lines[i].count("}")
            i += 1
        blob = "".join(block)
        if "crexline.com" in blob or "crexline-devbot" in blob:
            continue
        out.extend(block)
    else:
        # drop orphaned crexline comments left at end
        if "crexline" in line.lower() and line.lstrip().startswith("#"):
            i += 1
            continue
        out.append(line)
        i += 1
path.write_text("".join(out), encoding="utf-8")
print("Cleaned previous crexline server blocks")
PY
fi

# Ensure HTTP-only block for ACME
if ! grep -q "server_name devbot.crexline.com backdev.crexline.com" "$NGINX_CONF"; then
  echo "Appending HTTP ACME block..."
  {
    echo ""
    echo "# --- crexline HTTP (ACME) ---"
    cat "$HTTP_SNIPPET"
  } >> "$NGINX_CONF"
fi

docker exec fiyola-prod-nginx nginx -t
docker exec fiyola-prod-nginx nginx -s reload
echo "Nginx reloaded with HTTP ACME block."

echo "Requesting certificate (set Cloudflare DNS-only / grey cloud first)..."
docker run --rm \
  -v "$WWW:/var/www/certbot" \
  -v "$CONF:/etc/letsencrypt" \
  certbot/certbot certonly \
  --webroot -w /var/www/certbot \
  --cert-name crexline-devbot \
  -d devbot.crexline.com \
  -d backdev.crexline.com \
  --email "$EMAIL" \
  --agree-tos \
  --no-eff-email \
  --non-interactive

# Append HTTPS if not already present
if ! grep -q "ssl_certificate     /etc/letsencrypt/live/crexline-devbot/fullchain.pem" "$NGINX_CONF"; then
  echo "Appending HTTPS proxy blocks..."
  {
    echo ""
    echo "# --- crexline HTTPS ---"
    cat "$HTTPS_SNIPPET"
  } >> "$NGINX_CONF"
fi

docker exec fiyola-prod-nginx nginx -t
docker exec fiyola-prod-nginx nginx -s reload

echo
echo "Done."
echo "  curl -s https://backdev.crexline.com/health"
echo "  curl -I https://devbot.crexline.com"
echo "Then turn Cloudflare orange cloud back on."
