"""Nightly updater orchestrator (the cron entry point).

    /usr/bin/python3 -m updater.main                # rolling refresh + build + ship
    /usr/bin/python3 -m updater.main --backfill     # full 24m/26w/90d history (resumable)
    /usr/bin/python3 -m updater.main --local-only   # extract + build, don't ship
    /usr/bin/python3 -m updater.main --ship-only    # build from store + ship, no queries
    /usr/bin/python3 -m updater.main --only nnl,active_users@weekly
                                                    # spec, or spec@grain

Exit codes: 0 all ok · 1 fatal (VPN down, ship failed, nothing extracted)
· 2 partial (some metrics failed, snapshot still built/shipped with stale flags).
"""

import argparse
import logging
import sys
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import settings                        # noqa: E402
from updater import extract, snapshot, store       # noqa: E402
from updater.db import Warehouse, warehouse_reachable  # noqa: E402
from updater.periods import (  # noqa: E402
    add_months, add_weeks, day_key, month_key, month_start, week_key,
)
from updater.registry import GRAINS, METRICS, SPEC_BY_NAME  # noqa: E402
from updater.search_common import SEARCH_META_NAMES  # noqa: E402
from updater.wbr_common import WBR_META_NAMES  # noqa: E402

log = logging.getLogger("updater.main")


def refresh_category_tree(wh, store_conn):
    """Small hierarchy lookup that powers the webapp's cascading filters."""
    sql = (settings.PROJECT_DIR / "sql" / "category_tree.sql").read_text()
    _, rows = wh.query(sql, {"site": settings.SITE_SK})
    store.replace_category_tree(store_conn, rows)
    log.info("[category_tree] refreshed: %d hierarchy paths", len(rows))


def prune_retention(store_conn, today):
    """Drop rows older than each grain's retention window."""
    from datetime import timedelta
    cutoffs = {
        "monthly": month_key(add_months(month_start(today),
                                        -(settings.MONTHLY_WINDOW_MONTHS - 1))),
        "weekly": week_key(add_weeks(today, -(settings.WEEKLY_WINDOW_WEEKS - 1))),
        "daily": day_key(today - timedelta(days=settings.DAILY_WINDOW_DAYS - 1)),
    }
    for grain, min_key in cutoffs.items():
        n = store.prune_old_periods(store_conn, grain, min_key)
        if n:
            log.info("[retention] pruned %d %s rows older than %s", n, grain, min_key)


def spec_metric_names(spec):
    names = list(spec.value_columns)
    for plan in spec.grains.values():
        for c in plan.value_columns:
            if c not in names:
                names.append(c)
    return names


def spec_dim_names(spec):
    """Dim slices this spec owns (metrics like 'pageviews' are written by two
    specs at DIFFERENT dims — stats must not leak across the split)."""
    names = set()
    for grain in spec.grains:
        for dim in extract.spec_dims(spec, grain):
            names.add(dim.name)
    return sorted(names)


def run_extraction(targets, mode, force=False):
    """Extract all (spec, grains) targets, isolating failures per spec.
    Returns (n_ok, n_failed)."""
    wh = Warehouse()
    store_conn = store.open_store(settings.STORE_PATH)
    today = date.today()
    n_ok = n_failed = 0
    try:
        # search_* / wbr_* meta rows are owned by their merge modules, not the registry
        store.prune_meta(store_conn, [s.name for s in METRICS] + SEARCH_META_NAMES
                         + WBR_META_NAMES)
        try:
            refresh_category_tree(wh, store_conn)
        except Exception:
            log.exception("[category_tree] refresh failed — cascading filter "
                          "lists may be stale; continuing")
        for spec, grains in targets:
            t0 = time.time()
            current_grain = {"g": "?"}
            try:
                for grain in grains:
                    current_grain["g"] = grain
                    extract.extract_grain(wh, store_conn, spec, grain, today,
                                          mode, force=force)
                total_rows, latest = store.spec_stats(
                    store_conn, spec_metric_names(spec), spec_dim_names(spec))
                store.set_meta_ok(store_conn, spec.name, latest, total_rows)
                log.info("[%s] OK — %d rows in store, latest %s (%.0fs)",
                         spec.name, total_rows, latest, time.time() - t0)
                n_ok += 1
            except Exception as exc:
                log.exception("[%s@%s] FAILED after retries — marked stale, "
                              "continuing", spec.name, current_grain["g"])
                store.set_meta_failed(
                    store_conn, spec.name,
                    "grain %s: %s" % (current_grain["g"], exc))
                n_failed += 1
        prune_retention(store_conn, today)
    finally:
        wh.close()
        store_conn.close()
    return n_ok, n_failed


def parse_only(only_arg):
    """'nnl,active_users@weekly' → [(spec, [grains...]), ...]"""
    targets = []
    for token in (t.strip() for t in only_arg.split(",") if t.strip()):
        name, _, grain = token.partition("@")
        if name not in SPEC_BY_NAME:
            log.error("Unknown spec name %r (known: %s)", name, sorted(SPEC_BY_NAME))
            sys.exit(1)
        spec = SPEC_BY_NAME[name]
        if grain:
            if grain not in spec.grains:
                log.error("Spec %r has no grain %r (has: %s)",
                          name, grain, sorted(spec.grains))
                sys.exit(1)
            grains = [grain]
        else:
            grains = [g for g in GRAINS if g in spec.grains]
        targets.append((spec, grains))
    return targets


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--backfill", action="store_true",
                    help="extract the full retention window (resumable)")
    ap.add_argument("--force", action="store_true",
                    help="with --backfill: re-extract chunks even if present")
    ap.add_argument("--local-only", action="store_true",
                    help="extract and build the snapshot but do not ship it")
    ap.add_argument("--ship-only", action="store_true",
                    help="skip extraction; build from the store and ship")
    ap.add_argument("--no-snapshot", action="store_true",
                    help="extract only; skip snapshot build and ship")
    ap.add_argument("--only", default="",
                    help="comma-separated spec[@grain] targets (default: all)")
    args = ap.parse_args()

    if args.only:
        targets = parse_only(args.only)
    else:
        targets = [(s, [g for g in GRAINS if g in s.grains]) for s in METRICS]

    n_failed = 0
    if not args.ship_only:
        if not warehouse_reachable():
            log.error("WAREHOUSE UNREACHABLE (VPN down?) — aborting; "
                      "droplet keeps the previous snapshot")
            sys.exit(1)
        mode = "backfill" if args.backfill else "rolling"
        log.info("Starting %s extraction of %d specs", mode, len(targets))
        n_ok, n_failed = run_extraction(targets, mode, force=args.force)
        log.info("Extraction done: %d ok, %d failed", n_ok, n_failed)
        if n_ok == 0:
            log.error("Every extraction failed — not building a snapshot")
            sys.exit(1)

    if not args.no_snapshot:
        snap_path = snapshot.build_snapshot()
        if not args.local_only:
            snapshot.ship(snap_path)
        else:
            log.info("--local-only: snapshot left at %s", snap_path)

    sys.exit(2 if n_failed else 0)


if __name__ == "__main__":
    main()
