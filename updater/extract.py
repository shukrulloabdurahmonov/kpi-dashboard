"""Chunked extraction of one MetricSpec (at each of its grains) into the
local store.

Every query is pre-aggregated in Redshift and returns
(period, dim_name, dim_value, value...) rows — tens to low thousands per
chunk, never anywhere near the 5M-row WLM abort. Chunks are written
atomically (DELETE range + INSERT in one transaction), so an interrupted
run never leaves a half-written period and a re-run is always safe.

Weekly correctness invariant: chunk boundaries are Monday-aligned, so a
week's distinct count is never computed over partial days (except the
current partial week, which the rolling refresh re-extracts nightly).
"""

import logging
from datetime import date, timedelta

from config import settings
from updater import store
from updater.periods import (
    add_months, add_weeks, day_key, month_key, month_start, month_range,
    day_range, week_key, week_range, week_start,
)

log = logging.getLogger("updater.extract")

SQL_DIR = settings.PROJECT_DIR / "sql"

# {period_expr} per grain, built around the spec's date expression.
# monthly/daily keep LEFT() — byte-identical to the historical queries;
# weekly needs a real date to truncate: CAST handles varchar 'YYYY-MM-DD'
# _nk columns, DATE, TIMESTAMP, and COALESCE mixes alike. Redshift
# DATE_TRUNC('week') returns the MONDAY, matching periods.week_start.
PERIOD_EXPRS = {
    "monthly": "LEFT({e}, 7)",
    "weekly": "TO_CHAR(DATE_TRUNC('week', CAST({e} AS DATE)), 'YYYY-MM-DD')",
    "daily": "LEFT({e}, 10)",
}


def spec_dims(spec, grain):
    """Dims to run for this spec at this grain, total first (unless another
    spec owns the total rows)."""
    from updater.registry import TOTAL_DIM
    plan = spec.plan(grain)
    dims = [TOTAL_DIM] if plan.include_total else []
    return dims + list(plan.dims)


def render_sql(spec, grain, dim):
    """Fill the SQL template's placeholders. psycopg2's %(site)s-style
    params are untouched by str.format."""
    tpl = (SQL_DIR / spec.file_for(grain)).read_text()
    group_by = "1" if dim.name == "total" else "1, 3"
    return tpl.format(
        period_expr=PERIOD_EXPRS[grain].format(e=spec.date_expr),
        date_expr=spec.date_expr,
        dim_name=dim.name, dim_value=dim.value_expr,
        dim_join=dim.join, group_by=group_by,
        value_col=spec.cols(grain)[0],
    )


def _align(grain, d):
    if grain == "monthly":
        return month_start(d)
    if grain == "weekly":
        return week_start(d)
    return d


def _add(grain, d, n):
    if grain == "monthly":
        return add_months(d, n)
    if grain == "weekly":
        return add_weeks(d, n)
    return d + timedelta(days=n)


def _key(grain, d):
    if grain == "monthly":
        return month_key(d)
    if grain == "weekly":
        return week_key(d)
    return day_key(d)


def _range(grain, start, end_exclusive):
    if grain == "monthly":
        return month_range(start, end_exclusive)
    if grain == "weekly":
        return week_range(start, end_exclusive)
    return day_range(start, end_exclusive)


def spec_window(spec, grain, today, mode):
    """[start, end) date range to extract for this spec at this grain.

    mode 'rolling'  — nightly refresh: only recent periods.
    mode 'backfill' — the full retention window.
    """
    plan = spec.plan(grain)
    rolling = {
        "monthly": settings.ROLLING_MONTHLY_REFRESH_MONTHS,
        "weekly": settings.ROLLING_WEEKLY_REFRESH_WEEKS,
        "daily": settings.ROLLING_DAILY_REFRESH_DAYS,
    }[grain]

    window_start = _add(grain, _align(grain, today), -(plan.window - 1))
    start = window_start
    if mode == "rolling":
        start = max(window_start, _add(grain, _align(grain, today), -(rolling - 1)))

    end = today  # exclusive — up to yesterday; today is still loading
    maturity = spec.maturity_for(grain)
    if maturity:
        mature_end = today - timedelta(days=maturity - 1)
        if grain == "daily":
            end = min(end, mature_end)
        else:
            # only periods whose LAST day is mature
            end = min(end, _align(grain, mature_end))
    return start, end


def chunk_windows(spec, grain, start, end):
    """Split [start, end) into query-sized [c_start, c_end) chunks, aligned
    to whole periods of the grain (Monday-aligned for weekly — required so
    a distinct count never covers partial days)."""
    plan = spec.plan(grain)
    chunks = []
    cur = _align(grain, start)
    while cur < end:
        nxt = _add(grain, cur, plan.chunk)
        chunks.append((max(cur, start), min(nxt, end)))
        cur = nxt
    return chunks


def delete_range_keys(spec, grain, c_start, c_end):
    """Period-key range [start_key, end_key) covering the chunk's dates."""
    if grain == "daily":
        return day_key(c_start), day_key(c_end)
    last_day = c_end - timedelta(days=1)
    if grain == "monthly":
        return month_key(c_start), month_key(add_months(month_start(last_day), 1))
    return week_key(c_start), day_key(week_start(last_day) + timedelta(weeks=1))


def expected_periods(spec, grain, c_start, c_end):
    return {_key(grain, d) for d in _range(grain, c_start, c_end)}


def chunk_already_done(store_conn, spec, grain, c_start, c_end, today):
    """Backfill resume: skip chunks that are fully closed and fully present
    for every dim this spec extracts at this grain (chunk writes are atomic,
    so presence of the last dim implies the whole chunk landed)."""
    closed = c_end <= _align(grain, today) if grain != "daily" else c_end <= today
    if not closed:
        return False
    start_key, end_key = delete_range_keys(spec, grain, c_start, c_end)
    expected = expected_periods(spec, grain, c_start, c_end)
    probe_col = spec.cols(grain)[0]
    for dim in spec_dims(spec, grain):
        present = store.present_periods(
            store_conn, probe_col, grain, dim.name, start_key, end_key,
        )
        if not expected <= present:
            return False
    return True


def rows_from_result(spec, grain, cols, raw_rows):
    """Map SQL rows to tidy (metric, grain, period, dim_name, dim_value, value)
    tuples. NULL values are skipped (a column not applicable at that level)."""
    value_cols = cols[3:]
    unknown = set(value_cols) - set(spec.cols(grain))
    if unknown:
        raise ValueError(
            "%s@%s returned unregistered value columns: %s"
            % (spec.name, grain, sorted(unknown))
        )
    out = []
    for r in raw_rows:
        period, dim_name, dim_value = str(r[0]), r[1], r[2]
        for i, col in enumerate(value_cols):
            v = r[3 + i]
            if v is not None:
                out.append((col, grain, period, dim_name, dim_value, float(v)))
    return out


def extract_grain(wh, store_conn, spec, grain, today, mode, force=False):
    """Extract one spec at one grain. Returns number of tidy rows written."""
    start, end = spec_window(spec, grain, today, mode)
    if start >= end:
        log.info("[%s@%s] nothing to extract (window empty after maturity guard)",
                 spec.name, grain)
        return 0
    dims = spec_dims(spec, grain)
    written = 0
    chunks = chunk_windows(spec, grain, start, end)
    for i, (c_start, c_end) in enumerate(chunks, 1):
        if (mode == "backfill" and not force
                and chunk_already_done(store_conn, spec, grain, c_start, c_end, today)):
            log.info("[%s@%s] chunk %d/%d %s..%s already present, skipping",
                     spec.name, grain, i, len(chunks), c_start, c_end)
            continue
        params = {"site": settings.SITE_SK, "start": str(c_start), "end": str(c_end)}
        rows = []
        for dim in dims:
            cols, raw = wh.query(render_sql(spec, grain, dim), params)
            rows.extend(rows_from_result(spec, grain, cols, raw))
        start_key, end_key = delete_range_keys(spec, grain, c_start, c_end)
        store.replace_range(store_conn, spec.cols(grain), grain,
                            [d.name for d in dims], start_key, end_key, rows)
        written += len(rows)
        log.info("[%s@%s] chunk %d/%d %s..%s: %d rows across %d dims",
                 spec.name, grain, i, len(chunks), c_start, c_end, len(rows), len(dims))
    return written


def extract_spec(wh, store_conn, spec, today, mode, force=False, grains=None):
    """Extract a spec at all (or the given) grains. A grain failure raises —
    main.py isolates failures per spec and records the grain in the error."""
    written = 0
    for grain in (grains or spec.grains.keys()):
        written += extract_grain(wh, store_conn, spec, grain, today, mode, force=force)
    return written
