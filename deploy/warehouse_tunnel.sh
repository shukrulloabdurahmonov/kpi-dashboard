#!/usr/bin/env bash
# Reverse-SSH tunnel: exposes the yamato warehouse to the box on its
# localhost:15432 so the box's nightly_update.sh can refresh the snapshot.
# The box never connects out — this script dials INTO the box and holds the
# tunnel open for a fixed window bracketing the box's 07:00 (Tashkent)
# refresh and its 3x30-min retries.
#
# Launched by ~/Library/LaunchAgents/com.shukrullo.kpi-tunnel.plist at 06:30
# Tashkent (see deploy/com.shukrullo.kpi-tunnel.plist); self-terminates after
# WINDOW_HOURS. Requires:
#   * config/deploy.env with DROPLET_HOST / DROPLET_USER / DROPLET_SSH_KEY
#   * this Mac's key installed on the box for that user
#   * warehouse creds at ~/Automations/credentials/s_abdurahmonov.json
#   * the Mac awake and on VPN for the window
set -euo pipefail
cd "$(dirname "$0")/.."

WINDOW_END="${WINDOW_END:-09:30}"   # local (Tashkent) time the tunnel closes
REMOTE_PORT=15432

# Seconds until WINDOW_END today — restart-safe: a relaunch after a dropped
# connection inherits the same wall-clock end instead of a fresh window.
remaining() {
    /usr/bin/python3 - "$WINDOW_END" <<'PY'
import sys
from datetime import datetime
h, m = map(int, sys.argv[1].split(":"))
now = datetime.now()
end = now.replace(hour=h, minute=m, second=0, microsecond=0)
print(max(0, int((end - now).total_seconds())))
PY
}

LIMIT="$(remaining)"
if [ "$LIMIT" -le 0 ]; then
    echo "[$(date -u +%FT%TZ)] past $WINDOW_END — not opening the tunnel"
    exit 0
fi

if [ ! -f config/deploy.env ]; then
    echo "config/deploy.env missing — copy config/deploy.env.example and fill it in" >&2
    exit 1
fi
# shellcheck disable=SC1091
source config/deploy.env

KEY="${DROPLET_SSH_KEY/#\~/$HOME}"
TARGET="$DROPLET_USER@$DROPLET_HOST"

read -r WH_HOST WH_PORT < <(/usr/bin/python3 - <<'PY'
import json, os
creds = json.load(open(os.path.expanduser(
    "~/Automations/credentials/s_abdurahmonov.json")))["yamato"]
print(creds["host"], creds["port"])
PY
)

echo "[$(date -u +%FT%TZ)] opening tunnel $TARGET:$REMOTE_PORT -> $WH_HOST:$WH_PORT until $WINDOW_END local"

# Self-terminating window guard: kill the ssh process at WINDOW_END.
ssh -N \
    -o ExitOnForwardFailure=yes \
    -o ServerAliveInterval=30 \
    -o ServerAliveCountMax=3 \
    -o BatchMode=yes \
    -o ConnectTimeout=15 \
    -i "$KEY" \
    -R "127.0.0.1:$REMOTE_PORT:$WH_HOST:$WH_PORT" \
    "$TARGET" &
SSH_PID=$!
trap 'kill "$SSH_PID" 2>/dev/null || true' EXIT

SECONDS=0
while kill -0 "$SSH_PID" 2>/dev/null; do
    if [ "$SECONDS" -ge "$LIMIT" ]; then
        echo "[$(date -u +%FT%TZ)] window over — closing tunnel"
        kill "$SSH_PID" 2>/dev/null || true
        exit 0
    fi
    sleep 30
done

# ssh exited on its own (dropped connection / forward failure): report it so
# launchd's KeepAlive (SuccessfulExit=false) restarts us within the window.
echo "[$(date -u +%FT%TZ)] tunnel dropped after ${SECONDS}s" >&2
exit 1
