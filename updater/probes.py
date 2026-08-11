"""Cheap pre-flight probes against the warehouse. Run these BEFORE the
first backfill — they catch schema drift and access problems while they
cost seconds, not hours.

Usage (from the project dir, over VPN):
    /usr/bin/python3 -m updater.probes --schemas   # verify expected columns
    /usr/bin/python3 -m updater.probes --counts    # run each spec for 1 recent chunk
    /usr/bin/python3 -m updater.probes --triton    # informational triton view check
    /usr/bin/python3 -m updater.probes             # all of the above
"""

import argparse
import logging
import sys
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import settings                       # noqa: E402
from updater.db import Warehouse, warehouse_reachable  # noqa: E402
from updater.extract import chunk_windows, render_sql, spec_dims, spec_window  # noqa: E402
from updater.registry import GRAINS, METRICS      # noqa: E402

log = logging.getLogger("updater.probes")

# Columns our sql/*.sql files actually reference, per table.
EXPECTED = {
    ("eu_bi", "fact_listings"): [
        "listing_sk", "user_sk", "category_sk", "site_sk", "listing_net_sk",
        "first_active_date_nk", "first_active_date_local", "date_posted_nk",
        "geography_sk",
    ],
    ("eu_bi", "map_categories_finance"): ["category_sk", "finance_category_l2_name_en"],
    ("eu_bi", "dim_categories"): [
        "category_sk", "finance_category_l1_name_en", "finance_category_l2_name_en",
        "category_l1_name_en", "category_l2_name_en",
        "category_l3_name_en", "category_l4_name_en",
    ],
    ("eu_bi", "dim_geographies"): ["geography_sk", "geography_l1_name_en"],
    ("eu_bi", "dim_users"): ["user_sk", "site_sk", "seller_type", "user_status", "time_created"],
    ("eu_bi", "fact_active_listings"): ["listing_sk", "date_nk", "is_active"],
    ("eu_bi", "fact_listings_insertions"): [
        "listing_sk", "user_sk", "site_sk", "event_date_nk",
        "is_renewal", "is_free", "is_removed",
    ],
    ("eu_bi", "fact_audience_categories"): [
        "session_long_sk", "site_sk", "date_event_local", "category_sk",
        "applicable_to_active_users", "is_outlier", "crawler_type",
        "num_pageviews", "session_eq1_pvs_sess", "session_mt1_pvs_cat",
        "session_eq1_pvs_cat",
    ],
    ("eu_bi", "fact_listings_traffic_agg"): [
        "site_sk", "date_event_local", "num_impressions", "num_ad_page",
    ],
    ("eu_bi", "fact_listings_liquidity_success"): [
        "listing_sk", "first_active_date_nk", "num_replies_wk", "num_replies_2wk",
        "num_replies_4wk",
    ],
    ("eu_bi", "fact_listings_user_segments"): [
        "listing_sk", "first_active_date_nk", "user_first_time_returning_nk",
    ],
    ("eu_bi", "fact_replies_success_legacy"): [
        "user_sk", "site_sk", "date_sent_nk", "category_sk", "geography_sk",
    ],
    ("eu_bi", "fact_payments"): [
        "user_sk", "site_sk", "payment_date", "transaction_date",
        "trans_value_gross", "trans_value_net", "trans_value_service_fee",
        "trans_value_tax", "bonus_value_gross", "refund_value_gross",
        "cash_value_gross", "transaction_sk", "payment_sk", "product_sk",
        "category_sk", "geography_sk",
    ],
    ("eu_bi", "dim_products"): ["product_sk", "revenue_stream"],
}


def probe_schemas(wh):
    ok = True
    for (schema, table), expected_cols in sorted(EXPECTED.items()):
        _, rows = wh.query(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = %(s)s AND table_name = %(t)s",
            {"s": schema, "t": table},
        )
        actual = {r[0] for r in rows}
        if not actual:
            print("MISSING TABLE  %s.%s" % (schema, table), flush=True)
            ok = False
            continue
        missing = [c for c in expected_cols if c not in actual]
        if missing:
            print("MISSING COLS   %s.%s: %s" % (schema, table, missing), flush=True)
            ok = False
        else:
            print("ok             %s.%s (%d cols)" % (schema, table, len(actual)), flush=True)
    return ok


def probe_counts(wh, grains=GRAINS):
    """Run every spec's real SQL for its most recent single chunk at each
    grain and report row count + duration. Confirms syntax, access, sizes."""
    today = date.today()
    ok = True
    for spec in METRICS:
        for grain in grains:
            if grain not in spec.grains:
                continue
            tag = "%s@%s" % (spec.name, grain)
            start, end = spec_window(spec, grain, today, "rolling")
            if start >= end:
                print("skip           %-30s window empty (maturity guard)" % tag, flush=True)
                continue
            c_start, c_end = chunk_windows(spec, grain, start, end)[-1]
            params = {"site": settings.SITE_SK, "start": str(c_start), "end": str(c_end)}
            t0 = time.time()
            try:
                n = 0
                dims = spec_dims(spec, grain)
                for dim in dims:
                    _, rows = wh.query(render_sql(spec, grain, dim), params)
                    n += len(rows)
                print("ok             %-30s %s..%s  %6d rows (%d dims)  %5.1fs"
                      % (tag, c_start, c_end, n, len(dims), time.time() - t0), flush=True)
            except Exception as exc:
                print("FAIL           %-30s %s" % (tag, str(exc).strip()[:200]), flush=True)
                ok = False
    return ok


# varchar _nk date columns the weekly CAST(... AS DATE) must survive
NK_DATE_COLUMNS = [
    ("eu_bi.fact_listings", "first_active_date_nk"),
    ("eu_bi.fact_listings", "date_posted_nk"),
    ("eu_bi.fact_listings_insertions", "event_date_nk"),
    ("eu_bi.fact_active_listings", "date_nk"),
    ("eu_bi.fact_replies_success_legacy", "date_sent_nk"),
    ("eu_bi.fact_listings_liquidity_success", "first_active_date_nk"),
]


def probe_dates(wh):
    """Verify the weekly CAST({date_expr} AS DATE) is safe: date/timestamp
    columns trivially are; genuine character columns get a malformed-value
    scan over the weekly window (POSIX class — Redshift has no \\d)."""
    from datetime import timedelta
    start = str(date.today() - timedelta(weeks=settings.WEEKLY_WINDOW_WEEKS + 1))
    ok = True
    for table, col in NK_DATE_COLUMNS:
        schema, tname = table.split(".")
        try:
            _, rows = wh.query(
                "SELECT data_type FROM information_schema.columns "
                "WHERE table_schema = %(s)s AND table_name = %(t)s "
                "AND column_name = %(c)s",
                {"s": schema, "t": tname, "c": col},
            )
            dtype = rows[0][0] if rows else "missing"
            if dtype.startswith(("date", "timestamp")):
                print("ok             %s.%s: %s (CAST is a no-op)" % (table, col, dtype),
                      flush=True)
                continue
            _, rows = wh.query(
                "SELECT COUNT(*) FROM %s WHERE %s >= %%(start)s "
                "AND %s !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}'" % (table, col, col),
                {"start": start},
            )
            bad = rows[0][0]
            print("%-14s %s.%s: %s, %d malformed"
                  % ("ok" if bad == 0 else "FAIL", table, col, dtype, bad), flush=True)
            ok = ok and bad == 0
        except Exception as exc:
            print("FAIL           %s.%s: %s" % (table, col, str(exc).strip()[:160]), flush=True)
            ok = False
    return ok


def probe_triton(wh):
    try:
        _, rows = wh.query(
            "SELECT country_sk, time_display_value FROM "
            "global_reporting.fact_triton_cube_tableau_livequery_view "
            "WHERE country_sk = %(site)s LIMIT 1",
            {"site": settings.SITE_SK},
        )
        print("triton view accessible; sample: %s" % (rows[0] if rows else "no uz rows"), flush=True)
    except Exception as exc:
        print("triton view NOT accessible (informational only): %s" % str(exc).strip()[:200], flush=True)


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--schemas", action="store_true")
    ap.add_argument("--counts", action="store_true")
    ap.add_argument("--weekly", action="store_true",
                    help="count-probe the weekly grain only")
    ap.add_argument("--dates", action="store_true",
                    help="check varchar _nk date columns for malformed values")
    ap.add_argument("--triton", action="store_true")
    args = ap.parse_args()
    run_all = not (args.schemas or args.counts or args.triton or args.dates or args.weekly)

    if not warehouse_reachable():
        sys.exit(1)
    wh = Warehouse()
    ok = True
    try:
        if run_all or args.schemas:
            print("=== schema probe ===", flush=True)
            ok = probe_schemas(wh) and ok
        if args.dates or run_all:
            print("=== varchar date-column probe (weekly CAST safety) ===", flush=True)
            ok = probe_dates(wh) and ok
        if args.weekly:
            print("=== weekly count/timing probe ===", flush=True)
            ok = probe_counts(wh, grains=("weekly",)) and ok
        if run_all or args.counts:
            print("=== count/timing probe (one chunk per spec x grain) ===", flush=True)
            ok = probe_counts(wh) and ok
        if run_all or args.triton:
            print("=== triton probe (informational) ===", flush=True)
            probe_triton(wh)
    finally:
        wh.close()
    sys.exit(0 if ok else 2)


if __name__ == "__main__":
    main()
