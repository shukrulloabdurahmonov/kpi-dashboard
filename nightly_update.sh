#!/bin/bash
# Nightly rolling refresh from yamato (via the reverse-SSH tunnel on :15432),
# then atomic local publish of the snapshot the webapp serves.
# The tunnel flaps, so retry a few times before giving up until tomorrow.
cd /opt/sites/olx_uz_kpi_dashboard
export PYTHONPATH=/opt/sites/olx_uz_kpi_dashboard

# Search dashboard data arrives via git as payloads/search_payload.sqlite
# (the Mac extracts it from Trino + hydra, which this box can't reach).
# Merge is idempotent — re-merging the same payload is a cheap no-op. The
# --republish matters: it publishes a snapshot right away, so fresh search
# data reaches the app even on nights the warehouse refresh below fails
# (e.g. tunnel down); a successful refresh then publishes again on top.
if [ -f payloads/search_payload.sqlite ]; then
  echo "[$(date -u +%FT%TZ)] merging search payload" >> logs/updater.log
  python3 -m updater.search_merge payloads/search_payload.sqlite --republish >> logs/updater.log 2>&1 \
    || echo "[$(date -u +%FT%TZ)] search merge FAILED (non-fatal)" >> logs/updater.log
fi

for attempt in 1 2 3; do
  echo "[$(date -u +%FT%TZ)] nightly refresh attempt $attempt" >> logs/updater.log
  python3 -m updater.main --local-only >> logs/updater.log 2>&1
  rc=$?
  if [ $rc -eq 0 ] || [ $rc -eq 2 ]; then
    cp updater/store/snapshot.sqlite.building data/snapshot.sqlite.tmp
    mv data/snapshot.sqlite.tmp data/snapshot.sqlite
    echo "[$(date -u +%FT%TZ)] published snapshot (updater rc=$rc)" >> logs/updater.log
    exit 0
  fi
  echo "[$(date -u +%FT%TZ)] updater rc=$rc (tunnel down?), retry in 30m" >> logs/updater.log
  sleep 1800
done
echo "[$(date -u +%FT%TZ)] giving up until tomorrow" >> logs/updater.log
exit 1
