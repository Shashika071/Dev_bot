#!/usr/bin/env bash
# Create Cloudflare Origin Certificate via API → ./certs/ + reload edge.
#
# Add to .env.prod:
#   CLOUDFLARE_API_TOKEN=...
#   CLOUDFLARE_ZONE_NAME=crexline.com
#
# Token needs: Zone.SSL and Certificates Edit + Zone.Zone Read
#
#   chmod +x deploy/install-cf-origin-cert.sh
#   ./deploy/install-cf-origin-cert.sh

set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ -f .env.prod ]]; then
  # load only Cloudflare / domain keys
  while IFS= read -r line; do
    case "$line" in
      CLOUDFLARE_*|DOMAIN_UI=*|DOMAIN_API=*)
        export "$line"
        ;;
    esac
  done < <(grep -E '^(CLOUDFLARE_|DOMAIN_UI=|DOMAIN_API=)' .env.prod | sed 's/\r$//' || true)
fi

TOKEN="${CLOUDFLARE_API_TOKEN:-}"
ZONE_ID="${CLOUDFLARE_ZONE_ID:-}"
ZONE_NAME="${CLOUDFLARE_ZONE_NAME:-crexline.com}"
DOMAIN_UI="${DOMAIN_UI:-devbot.crexline.com}"
DOMAIN_API="${DOMAIN_API:-backdev.crexline.com}"

if [[ -z "$TOKEN" ]]; then
  cat <<EOF
Missing CLOUDFLARE_API_TOKEN in .env.prod

1) Cloudflare → My Profile → API Tokens → Create Token → Custom
   Permissions:
     Zone → SSL and Certificates → Edit
     Zone → Zone → Read
   Zone Resources: Include → Specific zone → ${ZONE_NAME}

2) Add to .env.prod:
   CLOUDFLARE_API_TOKEN=your_token_here
   CLOUDFLARE_ZONE_NAME=${ZONE_NAME}

3) Re-run: ./deploy/install-cf-origin-cert.sh
EOF
  exit 1
fi

if [[ -z "$ZONE_ID" ]]; then
  echo "Looking up zone id for ${ZONE_NAME}..."
  ZONE_ID="$(
    curl -sS -G "https://api.cloudflare.com/client/v4/zones" \
      --data-urlencode "name=${ZONE_NAME}" \
      -H "Authorization: Bearer ${TOKEN}" \
      -H "Content-Type: application/json" \
    | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d["result"][0]["id"] if d.get("success") and d.get("result") else "")'
  )"
  if [[ -z "$ZONE_ID" ]]; then
    echo "Could not resolve zone id. Check token + CLOUDFLARE_ZONE_NAME=${ZONE_NAME}"
    exit 1
  fi
  echo "Zone ID: $ZONE_ID"
fi

echo "Requesting Origin Certificate..."
TMP="$(mktemp)"
curl -sS -X POST "https://api.cloudflare.com/client/v4/certificates" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  --data @- >"$TMP" <<EOF
{
  "hostnames": ["${DOMAIN_UI}", "${DOMAIN_API}"],
  "requested_validity": 5475,
  "request_type": "origin-rsa",
  "csr": null
}
EOF

python3 - "$TMP" <<'PY'
import json, sys
from pathlib import Path
data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if not data.get("success"):
    print("Cloudflare API error:")
    print(json.dumps(data.get("errors") or data, indent=2))
    raise SystemExit(1)
result = data["result"]
certs = Path("certs")
certs.mkdir(exist_ok=True)
(certs / "origin.pem").write_text(result["certificate"].rstrip() + "\n", encoding="utf-8")
(certs / "origin.key").write_text(result["private_key"].rstrip() + "\n", encoding="utf-8")
print("Wrote certs/origin.pem and certs/origin.key")
print("Expires on:", result.get("expires_on"))
PY
rm -f "$TMP"
chmod 600 certs/origin.key

# Ensure SSL mode note + restart edge with new certs
if docker ps --format '{{.Names}}' | grep -qx 'deriv-edge-1'; then
  echo "Recreating edge with new cert..."
  docker compose -p deriv \
    -f docker-compose.prod.yml \
    -f docker-compose.ssl-ports.yml \
    --env-file .env.prod \
    up -d --force-recreate edge
else
  echo "Starting full SSL stack..."
  bash "$ROOT/deploy/auto-ssl.sh"
fi

echo
echo "DONE."
echo "  1) Cloudflare DNS: orange cloud ON for ${DOMAIN_UI%%.*} and ${DOMAIN_API%%.*}"
echo "  2) SSL/TLS mode: Full (Strict)"
echo "  3) Open https://${DOMAIN_UI}:8443"
echo
echo "If browser still shows Not secure, hard-refresh (Ctrl+Shift+R)."
