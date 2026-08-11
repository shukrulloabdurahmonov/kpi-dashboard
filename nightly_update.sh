#!/bin/bash
# Nightly rolling refresh from yamato (via the reverse-SSH tunnel on :15432),
# then atomic local publish of the snapshot the webapp serves.
# The tunnel flaps, so retry a few times before giving up until tomorrow.
cd /home/claude/olx_uz_kpi_dashboard
export PYTHONPATH=/home/claude/olx_uz_kpi_dashboard

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
