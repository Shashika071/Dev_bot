#!/usr/bin/env bash
# Deploy Deriv Touch Bot on VPS beside Fiyola (does NOT bind host :80/:443).
# Usage:
#   chmod +x deploy/deploy.sh
#   ./deploy/deploy.sh            # build + start
#   ./deploy/deploy.sh up
#   ./deploy/deploy.sh down
#   ./deploy/deploy.sh status
#   ./deploy/deploy.sh logs
#   ./deploy/deploy.sh connect-nginx   # attach fiyola nginx to edge network

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

COMPOSE=(docker compose -p deriv -f docker-compose.prod.yml --env-file .env.prod)
EDGE_NETWORK="${EDGE_NETWORK:-edge}"
FIYOLA_NGINX="${FIYOLA_NGINX:-fiyola-prod-nginx}"

need() {
  command -v "$1" >/dev/null 2>&1 || { echo "Missing command: $1"; exit 1; }
}

ensure_env() {
  if [[ ! -f .env.prod ]]; then
    if [[ -f .env.prod.example ]]; then
      cp .env.prod.example .env.prod
      echo "Created .env.prod from .env.prod.example — edit secrets, then re-run."
      echo "  nano $ROOT/.env.prod"
      exit 1
    fi
    echo "Missing .env.prod (and no .env.prod.example)."
    exit 1
  fi
  if grep -q 'change_me_strong_password\|generate_a_long_random_secret\|generate_another_long_random_secret' .env.prod; then
    echo "WARNING: .env.prod still has placeholder secrets. Edit before production use."
  fi
}

ensure_edge_network() {
  if ! docker network inspect "$EDGE_NETWORK" >/dev/null 2>&1; then
    echo "Creating docker network: $EDGE_NETWORK"
    docker network create "$EDGE_NETWORK"
  else
    echo "Network exists: $EDGE_NETWORK"
  fi
}

connect_nginx() {
  ensure_edge_network
  if ! docker ps --format '{{.Names}}' | grep -qx "$FIYOLA_NGINX"; then
    echo "Container not running: $FIYOLA_NGINX"
    echo "Set FIYOLA_NGINX=your-nginx-name if the name differs."
    docker ps --format 'table {{.Names}}\t{{.Ports}}' | head -40
    exit 1
  fi
  if docker inspect -f '{{json .NetworkSettings.Networks}}' "$FIYOLA_NGINX" | grep -q "\"$EDGE_NETWORK\""; then
    echo "$FIYOLA_NGINX already on network $EDGE_NETWORK"
  else
    echo "Connecting $FIYOLA_NGINX to $EDGE_NETWORK"
    docker network connect "$EDGE_NETWORK" "$FIYOLA_NGINX"
  fi
  echo
  echo "Next: add server blocks from deploy/fiyola-nginx-devbot.conf into Fiyola nginx,"
  echo "then: docker exec $FIYOLA_NGINX nginx -t && docker exec $FIYOLA_NGINX nginx -s reload"
}

cmd_up() {
  need docker
  ensure_env
  ensure_edge_network
  connect_nginx || true
  echo "Building and starting deriv stack..."
  "${COMPOSE[@]}" up -d --build
  echo
  "${COMPOSE[@]}" ps
  echo
  echo "UI  (after nginx): https://devbot.crexline.com"
  echo "API (after nginx): https://backdev.crexline.com/health"
  echo
  echo "If nginx blocks not added yet, see: deploy/fiyola-nginx-devbot.conf"
}

cmd_down() {
  need docker
  echo "Stopping deriv stack (Fiyola untouched)..."
  "${COMPOSE[@]}" down
}

cmd_status() {
  need docker
  "${COMPOSE[@]}" ps
  echo
  docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}' | grep -E 'NAME|deriv|'"$FIYOLA_NGINX" || true
  echo
  echo "Host ports 80/443 should still belong only to Fiyola nginx:"
  docker ps --format '{{.Names}} {{.Ports}}' | grep -E ':80->|:443->' || true
}

cmd_logs() {
  need docker
  "${COMPOSE[@]}" logs -f --tail=100
}

usage() {
  cat <<EOF
Usage: $0 [up|down|status|logs|connect-nginx]

  up             Create edge network, connect Fiyola nginx, build+start bot
  down           Stop only the deriv stack
  status         Show deriv + nginx containers / port binds
  logs           Follow deriv compose logs
  connect-nginx  Only attach $FIYOLA_NGINX to network $EDGE_NETWORK
  fix-db         Rewrite DATABASE_URL host to postgres and recreate backend/worker
  auto-ssl|ssl   Auto HTTPS on :8443 (own certs, Fiyola untouched)
  origin-cert    Install Cloudflare Origin Certificate via API

Env overrides:
  EDGE_NETWORK=$EDGE_NETWORK
  FIYOLA_NGINX=$FIYOLA_NGINX
EOF
}

ACTION="${1:-up}"
case "$ACTION" in
  up|start|deploy) cmd_up ;;
  down|stop)       cmd_down ;;
  status|ps)       cmd_status ;;
  logs)            cmd_logs ;;
  connect-nginx)   need docker; connect_nginx ;;
  fix-db)          need docker; bash "$ROOT/deploy/fix-backend-db.sh" ;;
  auto-ssl|ssl)    need docker; bash "$ROOT/deploy/auto-ssl.sh" ;;
  origin-cert)     need docker; bash "$ROOT/deploy/install-cf-origin-cert.sh" ;;
  -h|--help|help)  usage ;;
  *) echo "Unknown command: $ACTION"; usage; exit 1 ;;
esac
