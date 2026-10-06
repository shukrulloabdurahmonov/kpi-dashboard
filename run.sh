#!/bin/bash
# Supervisor for the OLX UZ KPI dashboard (gunicorn on :5055, fronted by Caddy).
cd /opt/sites/olx_uz_kpi_dashboard
export $(grep -v '^#' kpi.env | xargs -d '\n')
export PYTHONPATH=/opt/sites/olx_uz_kpi_dashboard/.venv/lib/python3.12/site-packages:/opt/sites/olx_uz_kpi_dashboard

while true; do
  python3 -m gunicorn \
    --bind 127.0.0.1:5055 \
    --workers 2 --threads 4 \
    --timeout 120 --graceful-timeout 30 \
    --access-logfile - --error-logfile - \
    'webapp.app:create_app()' >> logs/dashboard.log 2>&1
  echo "[$(date -u +%FT%TZ)] gunicorn exited, respawning in 5s" >> logs/dashboard.log
  sleep 5
done
