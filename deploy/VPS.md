# Host Deriv Touch Bot beside Fiyola (no port 80/443 fight)

Your VPS already has `fiyola-prod-nginx` on **:80 and :443**.  
This bot must **not** publish those ports. Instead:

```
Cloudflare (orange cloud)
  devbot.crexline.com  ──► fiyola nginx :443 ──► deriv-frontend:80
  backdev.crexline.com ──► fiyola nginx :443 ──► deriv-backend:8000
```

Fiyola containers stay untouched except: connect nginx to a shared `edge` network and add two `server_name` blocks.

## 1) On VPS

```bash
cd /home   # or wherever you keep apps
git clone <your-repo> deriv-touch-bot   # or scp/rsync the project
cd deriv-touch-bot

cp .env.prod.example .env.prod
nano .env.prod   # set strong passwords + secrets

chmod +x deploy/deploy.sh
./deploy/deploy.sh up
```

Other commands:

```bash
./deploy/deploy.sh status
./deploy/deploy.sh logs
./deploy/deploy.sh connect-nginx
./deploy/deploy.sh down          # stops only deriv — Fiyola untouched
```

Confirm **no** new bind on 80/443:

```bash
docker ps --format 'table {{.Names}}\t{{.Ports}}' | grep -E 'deriv|fiyola-prod-nginx'
```

## 2) Wire nginx (Fiyola only reload)

1. Copy `deploy/fiyola-nginx-devbot.conf` into the Fiyola nginx config mount (same place other `server {}` blocks live).
2. Match SSL cert lines to whatever Fiyola already uses for `*.crexline.com` (or Cloudflare Origin cert).
3. Fix upstream names if needed:

```bash
docker ps --format '{{.Names}}' | grep deriv
# usually: deriv-frontend-1  deriv-backend-1
```

4. Test + reload **only** nginx:

```bash
docker exec fiyola-prod-nginx nginx -t
docker exec fiyola-prod-nginx nginx -s reload
```

## 3) DNS (you already have this)

| Host | Type | Content | Proxy |
|------|------|---------|-------|
| `devbot` | A | `173.249.50.120` | Proxied |
| `backdev` | A | `173.249.50.120` | Proxied |

Cloudflare SSL mode: **Full** (or Full Strict if origin certs are valid).

## 4) RAM note

VPS has ~11 GiB, Fiyola uses a lot already. This stack caps roughly:
- postgres 768 MB
- backend 1.5 GB
- worker 1.5 GB
- frontend 128 MB  

Watch with `free -h` and `docker stats` after deploy.

## Rollback (does not touch Fiyola data)

```bash
cd /home/deriv-touch-bot
docker compose -p deriv -f docker-compose.prod.yml --env-file .env.prod down
# optional: remove server blocks from fiyola nginx + reload
# docker network disconnect edge fiyola-prod-nginx
```

Do **not** run `docker compose down -v` on Fiyola.  
Do **not** delete Fiyola volumes.
