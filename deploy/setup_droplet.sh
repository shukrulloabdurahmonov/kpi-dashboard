#!/usr/bin/env bash
# One-time droplet setup. Run ON the droplet as root (or a sudoer):
#   DOMAIN=mykpi.duckdns.org DUCKDNS_SUBDOMAIN=mykpi DUCKDNS_TOKEN=... \
#   KPI_PASSWORD_HASH='pbkdf2:sha256:...' SECRET_KEY=... ./setup_droplet.sh
#
# Creates /srv/kpi, installs Docker if missing, writes .env/kpi.env,
# registers the DuckDNS refresh cron. Then deploy from the Mac with deploy.sh.
set -euo pipefail

: "${DOMAIN:?set DOMAIN, e.g. mykpi.duckdns.org}"
: "${DUCKDNS_SUBDOMAIN:?set DUCKDNS_SUBDOMAIN (without .duckdns.org)}"
: "${DUCKDNS_TOKEN:?set DUCKDNS_TOKEN}"
: "${KPI_PASSWORD_HASH:?set KPI_PASSWORD_HASH (make one with deploy/make_password_hash.py)}"
: "${SECRET_KEY:=$(head -c 32 /dev/urandom | base64)}"

REMOTE_DIR="${REMOTE_DIR:-/srv/kpi}"

# --- docker ---------------------------------------------------------------
if ! command -v docker >/dev/null 2>&1; then
    echo "Installing Docker..."
    curl -fsSL https://get.docker.com | sh
fi

# --- layout ----------------------------------------------------------------
mkdir -p "$REMOTE_DIR/data"

cat > "$REMOTE_DIR/.env" <<EOF
DOMAIN=$DOMAIN
EOF

cat > "$REMOTE_DIR/kpi.env" <<EOF
SECRET_KEY=$SECRET_KEY
KPI_PASSWORD_HASH=$KPI_PASSWORD_HASH
EOF
chmod 600 "$REMOTE_DIR/kpi.env"

# --- DuckDNS refresh cron (every 5 min; survives droplet IP changes) --------
cat > /etc/cron.d/duckdns <<EOF
*/5 * * * * root curl -fsS "https://www.duckdns.org/update?domains=$DUCKDNS_SUBDOMAIN&token=$DUCKDNS_TOKEN&ip=" >/var/log/duckdns.log 2>&1
EOF
curl -fsS "https://www.duckdns.org/update?domains=$DUCKDNS_SUBDOMAIN&token=$DUCKDNS_TOKEN&ip=" && echo " <- duckdns updated"

echo
echo "Setup done. $REMOTE_DIR ready:"
ls -la "$REMOTE_DIR"
echo
echo "Next: from the Mac run deploy/deploy.sh, then ship a snapshot with"
echo "  /usr/bin/python3 -m updater.main --ship-only"
