"""Merge a WBR Truth Board payload into a metrics store (stdlib only).

    python3 -m updater.wbr_merge <payload.sqlite> [--store PATH] [--republish]

The payload is produced by updater/wbr_extract.py on a machine that reaches
both warehouses. This module runs anywhere (the box included), so it must not
import psycopg2/trino. Per (metric, grain, source) present in the payload it
drops the store's rows from the payload's first period on and inserts the
payload's rows — a payload that carries only one source never touches the
other source's rows. Older history is kept. With --republish the snapshot is
rebuilt and published locally (same as nightly_update.sh).

Exit codes: 0 ok · 1 fatal.
"""

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import settings                                     # noqa: E402
from updater import store                                       # noqa: E402
from updater.search_merge import republish                      # noqa: E402,F401
from updater.wbr_common import SOURCE_DIM, WBR_META_NAMES, WBR_PREFIX  # noqa: E402

log = logging.getLogger("updater.wbr_merge")


def merge(payload_path, store_path):
    conn = store.open_store(store_path)
    try:
        with conn:
            conn.execute("ATTACH DATABASE ? AS payload", (str(payload_path),))
            n_new = conn.execute(
                "SELECT COUNT(*) FROM payload.metrics WHERE metric LIKE ?",
                (WBR_PREFIX + "%",)).fetchone()[0]
            if n_new == 0:
                raise RuntimeError("payload has no wbr_ rows — refusing to merge")
            scopes = conn.execute(
                "SELECT metric, grain, dim_value, MIN(period) FROM payload.metrics "
                "WHERE metric LIKE ? AND dim_name = ? GROUP BY 1, 2, 3",
                (WBR_PREFIX + "%", SOURCE_DIM)).fetchall()
            for metric, grain, source, min_period in scopes:
                conn.execute(
                    "DELETE FROM metrics WHERE metric = ? AND grain = ? AND dim_name = ? "
                    "AND dim_value = ? AND period >= ?",
                    (metric, grain, SOURCE_DIM, source, min_period))
            conn.execute(
                "INSERT OR REPLACE INTO metrics SELECT * FROM payload.metrics WHERE metric LIKE ?",
                (WBR_PREFIX + "%",))
            qmarks = ",".join("?" * len(WBR_META_NAMES))
            conn.execute("INSERT OR REPLACE INTO meta SELECT * FROM payload.meta "
                         "WHERE metric IN (%s)" % qmarks, WBR_META_NAMES)
            conn.execute("INSERT OR REPLACE INTO snapshot_info SELECT key, value "
                         "FROM payload.snapshot_info WHERE key LIKE 'wbr_%'")
            log.info("merged %d wbr rows into %s (%d metric/source scopes)",
                     n_new, store_path, len(scopes))
        conn.execute("DETACH DATABASE payload")
    finally:
        conn.close()


def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("payload")
    ap.add_argument("--store", default=str(settings.STORE_PATH))
    ap.add_argument("--republish", action="store_true")
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
