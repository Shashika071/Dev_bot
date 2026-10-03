#!/bin/sh
# Runs inside certbot/certbot container. Waits until Google DNS shows the TXT.
set -e

SHORT=$(printf '%s' "$CERTBOT_DOMAIN" | sed 's/\.crexline\.com$//')
FQDN="_acme-challenge.${CERTBOT_DOMAIN}"

cat <<EOF

============================================================
ADD / UPDATE THIS TXT IN SPACESHIP — then just wait
============================================================
  Type:  TXT
  Host:  _acme-challenge.${SHORT}
         (Spaceship will show grey .crexline.com after it)
  Value: ${CERTBOT_VALIDATION}
  TTL:   30 min

Do NOT press Ctrl+C. This script polls DNS every 5s (up to ~10 min).
When Google DNS sees the value, Certbot continues automatically.
============================================================

EOF

i=0
while [ "$i" -lt 120 ]; do
  if python3 - <<'PY'
import json, os, urllib.request

name = "_acme-challenge." + os.environ["CERTBOT_DOMAIN"]
want = os.environ["CERTBOT_VALIDATION"]
url = "https://dns.google/resolve?name=%s&type=TXT" % name
try:
    with urllib.request.urlopen(url, timeout=15) as resp:
        data = json.load(resp)
except Exception:
    raise SystemExit(1)

for ans in data.get("Answer") or []:
    if ans.get("type") != 16:
        continue
    txt = str(ans.get("data", "")).strip().strip('"')
    if txt == want:
        raise SystemExit(0)
raise SystemExit(1)
PY
  then
    echo "DNS OK — found TXT for ${FQDN}"
    exit 0
  fi
  i=$((i + 1))
  echo "  waiting for DNS... $((i * 5))s  (host=_acme-challenge.${SHORT})"
  sleep 5
done

echo "Timed out: TXT for ${FQDN} never appeared with the expected value."
exit 1
