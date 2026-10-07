"""WBR Truth Board extractor — runs wherever both warehouses are reachable.

    python3 -m updater.wbr_extract                 # both sides, 2026-01-01 .. yesterday
    python3 -m updater.wbr_extract --republish     # ...and publish the snapshot here
    python3 -m updater.wbr_extract --no-trino      # Yamato side only
    python3 -m updater.wbr_extract --no-yamato     # Trino side only
    python3 -m updater.wbr_extract --start 2026-08-01

Pulls daily site totals for the WBR metrics from two warehouses:

  * tteam Trino (olam.data-main.tteampro.tech, iceberg.gold.olxuz_* marts).
    Needs NetBird. Auth: OAuth2 by default (a browser login the first time;
    the token is cached in the system keyring). Set TRINO_JWT to use a token
    instead, e.g. on a headless box.
  * Yamato Redshift (eu_bi / cubes), through updater.db.Warehouse — the same
    connection the nightly refresh uses (direct or via the :15432 tunnel).

Rows go into a small payload sqlite at payloads/wbr_payload.sqlite (the git
channel to the box, like the search payload), are merged into the local
store, and with --republish the snapshot is rebuilt and published locally.
SQL lives in sql/wbr/. Metric keys and row shape: updater/wbr_common.py.

Exit codes: 0 ok · 1 fatal · 2 partial (one side failed, payload still
built with the other).
"""

import argparse
import logging
import os
import re
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import settings                                     # noqa: E402
from updater import store, wbr_merge                            # noqa: E402
from updater.wbr_common import (                                # noqa: E402
    DAILY_KEYS, SOURCE_DIM, WBR_META_NAMES, WBR_START, metric_name,
)

log = logging.getLogger("updater.wbr_extract")

SQL_DIR = settings.PROJECT_DIR / "sql" / "wbr"
PAYLOAD_PATH = settings.PROJECT_DIR / "payloads" / "wbr_payload.sqlite"

TRINO_HOST = os.environ.get("WBR_TRINO_HOST", "olam.data-main.tteampro.tech")
TRINO_OAUTH_ATTEMPTS = 300   # ~10 min to finish a browser login if one is needed


# --- Trino -------------------------------------------------------------------
def _trino_conn():
    import trino
    from trino.auth import JWTAuthentication, OAuth2Authentication, _OAuth2TokenBearer
    if os.environ.get("TRINO_JWT"):
        auth = JWTAuthentication(os.environ["TRINO_JWT"])
    else:
        _OAuth2TokenBearer.MAX_OAUTH_ATTEMPTS = TRINO_OAUTH_ATTEMPTS
        auth = OAuth2Authentication()
    return trino.dbapi.connect(host=TRINO_HOST, port=443, http_scheme="https",
                               catalog="iceberg", schema="gold", auth=auth)


def extract_trino(start, end):
    """[(metric, grain, period, value)] for the Trino side."""
    conn = _trino_conn()
    cur = conn.cursor()
    rows = []
    cur.execute((SQL_DIR / "trino_daily.sql").read_text().format(start=start, end=end))
    for d, key, value in cur.fetchall():
        if key in DAILY_KEYS and value is not None:
            rows.append((metric_name(key), "daily", d, float(value)))
    cur.execute((SQL_DIR / "trino_repliers_weekly.sql").read_text().format(start=start, end=end))
    for wk, value in cur.fetchall():
        rows.append((metric_name("repwk"), "weekly", wk, float(value)))
    log.info("[trino] %d rows", len(rows))
    return rows


# --- Yamato ------------------------------------------------------------------
def _yamato_statements():
    """Split sql/wbr/yamato_daily.sql into (keys, sql) pairs."""
    out = []
    for chunk in (SQL_DIR / "yamato_daily.sql").read_text().split(";;"):
        m = re.search(r"--\s*metric:\s*([a-z_ ]+)", chunk)
        if m and chunk.strip():
            out.append((m.group(1).split(), chunk))
    return out


def extract_yamato(start, end):
    from updater.db import Warehouse, warehouse_reachable
    if not warehouse_reachable():
        raise RuntimeError("Yamato not reachable (VPN / tunnel down?)")
    wh = Warehouse()
    params = {"site": settings.SITE_SK, "start": start, "end": end}
    rows = []
    try:
        for keys, sql in _yamato_statements():
            cols, data = wh.query(sql, params)
            for r in data:
                rec = dict(zip(cols, r))
                for k in keys:
                    if rec.get(k) is not None:
                        rows.append((metric_name(k), "daily", rec["d"], float(rec[k])))
            log.info("[yamato] %s: %d days", "/".join(keys), len(data))
        cols, data = wh.query((SQL_DIR / "yamato_repliers_weekly.sql").read_text(), params)
        for wk, value in data:
            rows.append((metric_name("repwk"), "weekly", wk, float(value)))
    finally:
        wh.close()
    log.info("[yamato] %d rows", len(rows))
    return rows


# --- payload -----------------------------------------------------------------
def write_payload(path, by_source, meta):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(str(path))
    try:
        conn.executescript(store.DDL)
        with conn:
            for source, rows in by_source.items():
                conn.executemany(
                    "INSERT OR REPLACE INTO metrics VALUES (?, ?, ?, ?, ?, ?)",
                    [(m, g, p, SOURCE_DIM, source, v) for m, g, p, v in rows])
            conn.executemany("INSERT OR REPLACE INTO meta VALUES (?, ?, ?, ?, ?, ?)", meta)
            conn.execute("INSERT OR REPLACE INTO snapshot_info VALUES ('wbr_extracted_at_utc', ?)",
                         (store.utcnow(),))
    finally:
        conn.close()


def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", default=WBR_START, help="first day (default %(default)s)")
    ap.add_argument("--end", default=None, help="last day inclusive (default: yesterday)")
    ap.add_argument("--no-trino", action="store_true")
    ap.add_argument("--no-yamato", action="store_true")
    ap.add_argument("--payload", default=str(PAYLOAD_PATH))
    ap.add_argument("--republish", action="store_true",
                    help="rebuild + publish the snapshot on this machine after merging")
    args = ap.parse_args()

    end_incl = date.fromisoformat(args.end) if args.end else date.today() - timedelta(days=1)
    end = (end_incl + timedelta(days=1)).isoformat()
    jobs = [("trino", extract_trino, args.no_trino), ("yamato", extract_yamato, args.no_yamato)]

    by_source, meta, failed = {}, [], []
    for source, fn, skip in jobs:
        if skip:
            continue
        name = "wbr_" + source
        try:
            rows = fn(args.start, end)
            if not rows:
                raise RuntimeError("no rows returned")
            by_source[source] = rows
            latest = max(p for _, g, p, _ in rows if g == "daily")
            meta.append((name, "ok", store.utcnow(), latest, len(rows), None))
        except Exception as exc:
            log.exception("[%s] extract failed", source)
            failed.append(source)
            meta.append((name, "failed", store.utcnow(), None, 0, str(exc)[:500]))

    if not by_source:
        log.error("both sides failed — nothing written")
        sys.exit(1)
    # meta for sources we did not even try stays untouched in the store
    meta = [m for m in meta if m[0] in WBR_META_NAMES]
    write_payload(Path(args.payload), by_source, meta)
    log.info("payload written: %s (%s)", args.payload, ", ".join(by_source))

    try:
        wbr_merge.merge(args.payload, settings.STORE_PATH)
        if args.republish:
            wbr_merge.republish()
    except Exception:
        log.exception("merge/republish failed")
        sys.exit(1)
    sys.exit(2 if failed else 0)


if __name__ == "__main__":
    main()
