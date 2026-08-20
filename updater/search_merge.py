"""Merge a search-metrics payload into a metrics store (stdlib only).

    python3 -m updater.search_merge <payload.sqlite> [--store PATH] [--republish]

The payload is a small SQLite file produced by updater/search_extract.py on
the Mac (the only machine with Trino + VPN access). This module runs on the
Mac (local store) and on the box (its own store), so it must not import
psycopg2/trino. It:

  * replaces every `search_*` row in `metrics` with the payload's rows,
  * replaces the `search_keywords` table + its snapshot_info window keys,
  * upserts the `search_trino` / `search_hydra` meta rows,
  * prunes daily-grain search rows beyond SEARCH_DAILY_RETENTION_DAYS
    (weekly/monthly search history is kept forever — the registry's
    retention pruning exempts the search_ prefix),
  * with --republish, rebuilds the snapshot and publishes it to
    <project>/data/snapshot.sqlite the same way nightly_update.sh does.

Exit codes: 0 ok · 1 fatal.
"""

import argparse
import logging
import shutil
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import settings                                     # noqa: E402
from updater import store                                       # noqa: E402
from updater.search_common import (                             # noqa: E402
    SEARCH_DAILY_RETENTION_DAYS, SEARCH_META_NAMES, SEARCH_PREFIX,
)

log = logging.getLogger("updater.search_merge")


def merge(payload_path, store_path):
    conn = store.open_store(store_path)  # runs DDL: adds search_keywords if missing
    try:
        with conn:
            conn.execute("ATTACH DATABASE ? AS payload", (str(payload_path),))
            n_new = conn.execute("SELECT COUNT(*) FROM payload.metrics").fetchone()[0]
            if n_new == 0:
                raise RuntimeError("payload has no metrics rows — refusing to merge")
            # Window-scoped replace: per (metric, grain), drop only periods the
            # payload re-delivers (>= its min period) and keep older history.
            # A full-history payload therefore fully replaces; a rolling one
            # replaces just its window. Metrics absent from the payload are
            # untouched (a run that skipped one source keeps the other's rows).
            scopes = conn.execute(
                "SELECT metric, grain, MIN(period) FROM payload.metrics "
                "WHERE metric LIKE ? GROUP BY metric, grain",
                (SEARCH_PREFIX + "%",)).fetchall()
            for metric, grain, min_period in scopes:
                conn.execute(
                    "DELETE FROM metrics WHERE metric = ? AND grain = ? "
                    "AND period >= ?", (metric, grain, min_period))
            conn.execute("INSERT OR REPLACE INTO metrics "
                         "SELECT * FROM payload.metrics WHERE metric LIKE ?",
                         (SEARCH_PREFIX + "%",))

            # an extract run that skipped hydra ships no keywords — keep ours
            n_kw = conn.execute(
                "SELECT COUNT(*) FROM payload.search_keywords").fetchone()[0]
            if n_kw:
                conn.execute("DELETE FROM search_keywords")
                conn.execute("INSERT INTO search_keywords "
                             "SELECT * FROM payload.search_keywords")

            qmarks = ",".join("?" * len(SEARCH_META_NAMES))
            conn.execute(
                "INSERT OR REPLACE INTO meta SELECT * FROM payload.meta "
                "WHERE metric IN (%s)" % qmarks, SEARCH_META_NAMES)
            conn.execute(
                "INSERT OR REPLACE INTO snapshot_info "
                "SELECT key, value FROM payload.snapshot_info "
                "WHERE key LIKE 'search_%'")

            cutoff = (date.today()
                      - timedelta(days=SEARCH_DAILY_RETENTION_DAYS - 1)).isoformat()
            cur = conn.execute(
                "DELETE FROM metrics WHERE metric LIKE ? AND grain = 'daily' "
                "AND period < ?", (SEARCH_PREFIX + "%", cutoff))
            log.info("merged %d search rows into %s (pruned %d old daily rows)",
                     n_new, store_path, cur.rowcount)
        conn.execute("DETACH DATABASE payload")
    finally:
        conn.close()


def republish():
    """Rebuild the snapshot from the store and publish it locally, exactly
    like nightly_update.sh: cp to .tmp next to the served file, atomic mv."""
    from updater import snapshot
    snap_path = Path(snapshot.build_snapshot())
    data_dir = settings.PROJECT_DIR / "data"
    data_dir.mkdir(exist_ok=True)
    tmp = data_dir / "snapshot.sqlite.tmp"
    final = data_dir / "snapshot.sqlite"
    shutil.copyfile(snap_path, tmp)
    tmp.replace(final)
    log.info("published snapshot to %s", final)


def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("payload", help="path to the payload sqlite")
    ap.add_argument("--store", default=str(settings.STORE_PATH),
                    help="metrics store to merge into (default: settings.STORE_PATH)")
    ap.add_argument("--republish", action="store_true",
                    help="rebuild + publish the snapshot after merging")
    args = ap.parse_args()

    if not Path(args.payload).exists():
        log.error("payload not found: %s", args.payload)
        sys.exit(1)
    try:
        merge(args.payload, args.store)
        if args.republish:
            republish()
    except Exception:
        log.exception("merge failed")
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
