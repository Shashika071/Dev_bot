#!/bin/sh
# Runs inside certbot/certbot container. Prints + waits until Google DNS shows the TXT.
SHORT=$(printf '%s' "${CERTBOT_DOMAIN:-}" | sed 's/\.crexline\.com$//')
FQDN="_acme-challenge.${CERTBOT_DOMAIN}"
VALUE="${CERTBOT_VALIDATION}"

# Host-readable copy: certs/letsencrypt/PENDING_TXT.txt
PENDING="/etc/letsencrypt/PENDING_TXT.txt"
{
  echo "Host: _acme-challenge.${SHORT}"
  echo "Value: ${VALUE}"
  echo "FQDN: ${FQDN}"
} > "$PENDING" 2>/dev/null || true

echo ""
echo "============================================================"
echo "ADD / UPDATE THIS TXT IN SPACESHIP — then just wait"
echo "============================================================"
echo "  Type:  TXT"
echo "  Host:  _acme-challenge.${SHORT}"
echo "  Value: ${VALUE}"
echo "  TTL:   30 min"
echo ""
echo "If you do not see Value above, open a SECOND SSH tab:"
echo "  cat /home/cert/Dev_bot/certs/letsencrypt/PENDING_TXT.txt"
echo "============================================================"
echo ""

i=0
while [ "$i" -lt 120 ]; do
  if python3 -c "
import json, os, urllib.request
name = '_acme-challenge.' + os.environ['CERTBOT_DOMAIN']
want = os.environ['CERTBOT_VALIDATION']
url = 'https://dns.google/resolve?name=%s&type=TXT' % name
try:
    data = json.load(urllib.request.urlopen(url, timeout=15))
except Exception:
    raise SystemExit(1)
for ans in data.get('Answer') or []:
    if ans.get('type') == 16 and str(ans.get('data','')).strip().strip('\"') == want:
        raise SystemExit(0)
raise SystemExit(1)
" ; then
    echo "DNS OK — found TXT for ${FQDN}"
    rm -f "$PENDING" 2>/dev/null || true
    exit 0
  fi
  i=$((i + 1))
  echo "  waiting for DNS... $((i * 5))s  (update Host=_acme-challenge.${SHORT})"
  sleep 5
done

echo "Timed out: TXT for ${FQDN} never appeared with the expected value."
exit 1
