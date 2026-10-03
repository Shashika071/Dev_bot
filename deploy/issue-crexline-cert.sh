#!/usr/bin/env bash
# Issue Let's Encrypt cert for devbot + backdev (crexline.com).
# Uses the same certbot dirs already mounted into fiyola-prod-nginx.
#
# On VPS:
#   chmod +x deploy/issue-crexline-cert.sh
#   ./deploy/issue-crexline-cert.sh
#
# Cloudflare tip: if HTTP challenge fails with orange-cloud proxy,
# temporarily set DNS records to "DNS only" (grey cloud), issue cert,
# then turn proxy back on.

set -euo pipefail

WWW="/home/fiyola-production/fiyola/certbot/www"
CONF="/home/fiyola-production/fiyola/certbot/conf"
NGINX_CONF="/home/fiyola-production/fiyola/nginx/conf.d/production.conf"
SNIPPET_SRC="$(cd "$(dirname "$0")" && pwd)/crexline-devbot.conf"

if [[ ! -d "$WWW" || ! -d "$CONF" ]]; then
  echo "Certbot dirs not found under /home/fiyola-production/fiyola/certbot"
  exit 1
fi

# Ensure HTTP server block exists for ACME (idempotent append marker)
if ! grep -q "server_name devbot.crexline.com backdev.crexline.com" "$NGINX_CONF"; then
  echo "Appending crexline HTTP/HTTPS blocks to production.conf ..."
  cat "$SNIPPET_SRC" >> "$NGINX_CONF"
  docker exec fiyola-prod-nginx nginx -t
  docker exec fiyola-prod-nginx nginx -s reload
else
  echo "crexline server_name already present in production.conf"
fi

echo "Requesting certificate..."
docker run --rm \
  -v "$WWW:/var/www/certbot" \
  -v "$CONF:/etc/letsencrypt" \
  certbot/certbot certonly \
  --webroot -w /var/www/certbot \
  --cert-name crexline-devbot \
  -d devbot.crexline.com \
  -d backdev.crexline.com \
  --email admin@crexline.com \
  --agree-tos \
  --no-eff-email \
  --non-interactive

echo
echo "Cert issued. Reloading nginx..."
docker exec fiyola-prod-nginx nginx -t
docker exec fiyola-prod-nginx nginx -s reload

echo
echo "Test:"
echo "  curl -s https://backdev.crexline.com/health"
echo "  curl -I https://devbot.crexline.com"
