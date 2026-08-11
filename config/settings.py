"""Central configuration for the OLX UZ KPI dashboard (updater + webapp).

The updater runs on the Mac (VPN access to the yamato Redshift warehouse);
the webapp runs on the DigitalOcean droplet in Docker and only ever reads
the SQLite snapshot the updater ships to it.
"""

import os
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent

# --- Scope -----------------------------------------------------------------
SITE_SK = "olx|eu|uz"

# --- Extraction windows ----------------------------------------------------
MONTHLY_WINDOW_MONTHS = 24   # full history kept for monthly metrics
WEEKLY_WINDOW_WEEKS = 26     # full history kept for weekly metrics
DAILY_WINDOW_DAYS = 90       # full history kept for daily metrics

# Nightly rolling refresh: closed periods older than this are never re-queried.
ROLLING_MONTHLY_REFRESH_MONTHS = 3   # current month + previous 2
ROLLING_WEEKLY_REFRESH_WEEKS = 9     # 28d maturation (~4.2w) + ~4w outage slack
ROLLING_DAILY_REFRESH_DAYS = 35      # covers 28d liquidity maturation + slack

# --- Warehouse -------------------------------------------------------------
DB_CREDS_PATH = os.path.expanduser(
    "~/Automations/credentials/s_abdurahmonov.json"
)
DB_NAME = "yamato"
CONNECT_TIMEOUT = 30      # seconds; VPN hop can be slow
VPN_PROBE_TIMEOUT = 10    # fail-fast TCP probe before anything else
RETRY_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = [15, 60, 180]

# --- Local store / snapshot ------------------------------------------------
STORE_PATH = PROJECT_DIR / "updater" / "store" / "metrics_store.sqlite"
SNAPSHOT_BUILD_DIR = PROJECT_DIR / "updater" / "store"
SCHEMA_VERSION = "1"

# --- Droplet shipping (values come from config/deploy.env, not committed) --
DEPLOY_ENV_PATH = PROJECT_DIR / "config" / "deploy.env"


def load_deploy_env():
    """Parse config/deploy.env (KEY=VALUE lines) into a dict."""
    env = {}
    if DEPLOY_ENV_PATH.exists():
        for line in DEPLOY_ENV_PATH.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env
