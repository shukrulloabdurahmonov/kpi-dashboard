#!/usr/bin/env bash
# Deploy the webapp to the droplet: rsync code + compose files, rebuild, restart.
# Needs config/deploy.env (copy from deploy.env.example) and a droplet already
# prepared with deploy/setup_droplet.sh.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -f config/deploy.env ]; then
    echo "config/deploy.env missing — copy config/deploy.env.example and fill it in" >&2
    exit 1
fi
# shellcheck disable=SC1091
source config/deploy.env

KEY="${DROPLET_SSH_KEY/#\~/$HOME}"
TARGET="$DROPLET_USER@$DROPLET_HOST"
SSH_OPTS=(-i "$KEY" -o BatchMode=yes -o ConnectTimeout=15)

echo "== rsync code to $TARGET:$REMOTE_DIR"
rsync -az --delete \
    -e "ssh ${SSH_OPTS[*]}" \
    --exclude '__pycache__' \
    webapp docker-compose.yml "$TARGET:$REMOTE_DIR/"
rsync -az -e "ssh ${SSH_OPTS[*]}" deploy/Caddyfile "$TARGET:$REMOTE_DIR/Caddyfile"

echo "== build + restart containers"
ssh "${SSH_OPTS[@]}" "$TARGET" "cd $REMOTE_DIR && docker compose up -d --build"

echo "== health check"
sleep 3
if [ -n "${DUCKDNS_SUBDOMAIN:-}" ]; then
    curl -fsS "https://$DUCKDNS_SUBDOMAIN.duckdns.org/health" && echo
else
    ssh "${SSH_OPTS[@]}" "$TARGET" "curl -fsS http://127.0.0.1:80/health || true"
fi
echo "Deploy done."
