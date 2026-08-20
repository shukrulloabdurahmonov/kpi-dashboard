"""Read-only access to the shipped SQLite snapshot.

A fresh read-only connection is opened per request/call, so the atomic
`mv` that replaces the snapshot file is picked up immediately and safely.
All rows are display-ready pre-aggregates — never summed across periods
or dimension values here (each level was deduplicated in Redshift).
"""

import os
import sqlite3
from datetime import date, datetime, timedelta, timezone

SNAPSHOT_PATH = os.environ.get("KPI_SNAPSHOT", "/data/snapshot.sqlite")

TOTAL = "- Total -"

# In-process memo for expensive full-table DISTINCT scans, keyed by the
# snapshot file's mtime — a freshly shipped snapshot invalidates everything.
_memo = {}


def _snapshot_mtime():
    try:
        return os.path.getmtime(SNAPSHOT_PATH)
    except OSError:
        return 0


def _memoized(builder, *key_parts):
    mt = _snapshot_mtime()
    key = key_parts
    hit = _memo.get(key)
    if hit is not None and hit[0] == mt:
        return hit[1]
    val = builder()
    _memo[key] = (mt, val)
    return val


def _conn():
    conn = sqlite3.connect("file:%s?mode=ro" % SNAPSHOT_PATH, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def available():
    try:
        conn = _conn()
        conn.execute("SELECT 1 FROM snapshot_info LIMIT 1")
        conn.close()
        return True
    except sqlite3.Error:
        return False


def snapshot_info():
    with _conn() as conn:
        return {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM snapshot_info")}


def meta():
    with _conn() as conn:
        return {
            r["metric"]: dict(r)
            for r in conn.execute("SELECT * FROM meta ORDER BY metric")
        }


def freshness():
    """(built_at_iso, age_hours, level) — level in green/amber/red/missing."""
    info = snapshot_info()
    built = info.get("built_at_utc")
    if not built:
        return None, None, "missing"
    dt = datetime.strptime(built, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    age_h = (datetime.now(timezone.utc) - dt).total_seconds() / 3600.0
    level = "green" if age_h < 36 else ("amber" if age_h < 72 else "red")
    return built, round(age_h, 1), level


def series(metric, grain, dim_name="total", dim_value=TOTAL):
    """[[period, value], ...] ascending by period."""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT period, value FROM metrics WHERE metric=? AND grain=? "
            "AND dim_name=? AND dim_value=? ORDER BY period",
            (metric, grain, dim_name, dim_value),
        ).fetchall()
    return [[r["period"], r["value"]] for r in rows]


def current_period_key(grain, today=None):
    """Key of the current (partial) period at this grain."""
    t = today or date.today()
    if grain == "monthly":
        return t.strftime("%Y-%m")
    if grain == "weekly":
        return (t - timedelta(days=t.weekday())).isoformat()  # Monday
    return t.isoformat()


def shift_period(period, grain, n):
    """Move a period key by n periods of its grain."""
    if grain == "monthly":
        return _shift_month(period, n)
    d = date.fromisoformat(period)
    step = 7 if grain == "weekly" else 1
    return (d + timedelta(days=n * step)).isoformat()


def series_at(metric, grain, dim_name="total", dim_value=TOTAL, include_partial=False):
    """Series at a grain; the current partial month/week is dropped by
    default so trend lines don't plunge at the end (daily extraction is
    already end-exclusive of today, so day grain needs no drop)."""
    pts = series(metric, grain, dim_name, dim_value)
    if not include_partial and grain in ("monthly", "weekly"):
        cutoff = current_period_key(grain)
        pts = [p for p in pts if p[0] < cutoff]
    return pts


def monthly(metric, dim_name="total", dim_value=TOTAL, include_partial=False):
    return series_at(metric, "monthly", dim_name, dim_value, include_partial)


def daily(metric, dim_name="total", dim_value=TOTAL):
    return series_at(metric, "daily", dim_name, dim_value)


def breakdown(metric, dim_name, period=None, top_n=12):
    """[(dim_value, value)] for one period (default: latest full month),
    sorted descending. Returns (period, rows)."""
    if period is None:
        period = latest_full_period(metric, "monthly", dim_name)
        if period is None:
            return None, []
    with _conn() as conn:
        rows = conn.execute(
            "SELECT dim_value, value FROM metrics WHERE metric=? AND grain='monthly' "
            "AND dim_name=? AND period=? AND dim_value != ? "
            "ORDER BY value DESC LIMIT ?",
            (metric, dim_name, period, TOTAL, top_n),
        ).fetchall()
    return period, [[r["dim_value"], r["value"]] for r in rows]


def dim_values(metric, dim_name, top_n=4):
    """Top dim values by latest-full-month value (stable identity ordering)."""
    period, rows = breakdown(metric, dim_name, top_n=top_n)
    return [r[0] for r in rows]


def multi_series(metric, dim_name, top_n=4, grain="monthly"):
    """[{label, points}] for the top dim values (ordered by the latest full
    MONTH for stable identity), series at the requested grain."""
    return [
        {"label": dv, "points": series_at(metric, grain, dim_name, dv)}
        for dv in dim_values(metric, dim_name, top_n)
    ]


def current_month_key(today=None):
    return (today or date.today()).strftime("%Y-%m")


def latest_full_period(metric, grain="monthly", dim_name="total"):
    """Latest period strictly before the current partial one."""
    cutoff = current_period_key(grain)
    with _conn() as conn:
        row = conn.execute(
            "SELECT MAX(period) AS p FROM metrics WHERE metric=? AND grain=? "
            "AND dim_name=? AND period < ?",
            (metric, grain, dim_name, cutoff),
        ).fetchone()
    return row["p"]


def _value_at(metric, grain, period):
    if period is None:
        return None
    with _conn() as conn:
        row = conn.execute(
            "SELECT value FROM metrics WHERE metric=? AND grain=? AND dim_name='total' "
            "AND dim_value=? AND period=?",
            (metric, grain, TOTAL, period),
        ).fetchone()
    return row["value"] if row else None


def _shift_month(period, n):
    y, m = int(period[:4]), int(period[5:7])
    y2, m2 = divmod(y * 12 + (m - 1) + n, 12)
    return "%04d-%02d" % (y2, m2 + 1)


# per-grain delta definitions: (label, shift) x2. A 26-week window has no
# year-ago week, so week/day compare against nearby periods instead.
GRAIN_DELTAS = {
    "monthly": (("MoM", -1), ("YoY", -12)),
    "weekly": (("WoW", -1), ("vs 4w", -4)),
    "daily": (("DoD", -1), ("vs 7d", -7)),
}


def _pct(new, old):
    if old in (None, 0) or new is None:
        return None
    return round((new - old) / old * 100.0, 1)


def kpi(metric, grain="monthly", spark_metric=None, spark_grain="daily"):
    """Stat-tile payload: latest full period at the grain + two
    grain-appropriate deltas + sparkline."""
    p0 = latest_full_period(metric, grain)
    if p0 is None:
        return None
    v0 = _value_at(metric, grain, p0)
    (l1, s1), (l2, s2) = GRAIN_DELTAS[grain]
    v1 = _value_at(metric, grain, shift_period(p0, grain, s1))
    v2 = _value_at(metric, grain, shift_period(p0, grain, s2))

    if grain != "monthly":
        spark = series_at(spark_metric or metric, grain)[-16:]
        spark_grain = grain
    elif spark_grain == "monthly":
        spark = monthly(spark_metric or metric)[-13:]
    else:
        spark = series(spark_metric or metric, spark_grain)
    return {
        "period": p0,
        "value": v0,
        "mom": _pct(v0, v1),
        "yoy": _pct(v0, v2),
        "labels": [l1, l2],
        "spark": spark,
        "spark_grain": spark_grain,
    }


PAIR_SEP = "|"

# Distinct counts over users/sessions: summing slices can double-count
# (one user/session can appear in several categories or regions). Listing- and
# event-based counts are additive because each listing/event belongs to
# exactly one slice value.
NON_ADDITIVE = {
    "unique_listers", "mau", "wau", "dau", "pmul", "unique_repliers",
    "active_listers", "cash_flow_payers", "visits", "bounces",
    "bounces_per_category", "entering_visits",
    "ftl_success_listers_14d_3r",
} | {"liquid_listers_%s_%s" % (w, r)
     for w in ("7d", "14d", "28d") for r in ("1r", "3r")} \
  | {  # search rollups: avg-daily user counts and ratio metrics (never sum)
    "search_users", "search_users_adview", "search_users_lead",
    "search_volume", "search_volume_adview", "search_volume_lead",
    "search_ssu_adview", "search_ssu_lead",
    "search_avg_adview_su", "search_avg_lead_su", "search_share_on_platform",
    "search_filter_avg_results", "search_filter_use",  # avg / overlapping counts
    "search_queries", "search_queries_clicked",        # distinct counts
    "search_sessions", "search_sessions_liked",
}


def filter_options():
    return _memoized(_filter_options, "filter_options")


def _filter_options():
    """{dim_name: [dim_value, ...]} available in the snapshot (monthly grain),
    for the filter UI. Pair slices ('a|b') are internal — excluded here."""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT dim_name, dim_value FROM metrics "
            "WHERE grain = 'monthly' AND dim_name != 'total' "
            "AND metric NOT LIKE 'search_%' "   # search dims are page-local
            "ORDER BY dim_name, dim_value"
        ).fetchall()
    out = {}
    for r in rows:
        if PAIR_SEP not in r["dim_name"]:
            out.setdefault(r["dim_name"], []).append(r["dim_value"])
    return out


PLAYER_INNER_DIM = {"finance_l2": "finance_l1", "category_l2": "category_l1"}


def _dim_frame(metric, grain, dim_name, periods):
    """{dim_value: [v or None aligned to periods]} for one metric+dim."""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT period, dim_value, value FROM metrics WHERE metric = ? "
            "AND grain = ? AND dim_name = ? AND dim_value != ?",
            (metric, grain, dim_name, TOTAL),
        ).fetchall()
    idx = {p: i for i, p in enumerate(periods)}
    out = {}
    for r in rows:
        i = idx.get(r["period"])
        if i is None:
            continue
        out.setdefault(r["dim_value"], [None] * len(periods))[i] = r["value"]
    return out


def player_payload(metric, grain, dim_name):
    """Timeline-player payload: per-period composition for metric × dim,
    plus the exact parent-level (L1) frame when dim is an L2 level — the
    inner sunburst ring is never a client-side sum (distinct counts)."""
    cutoff = current_period_key(grain) if grain in ("monthly", "weekly") else None
    with _conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT period FROM metrics WHERE metric = ? AND grain = ? "
            "AND dim_name = ? ORDER BY period",
            (metric, grain, dim_name),
        ).fetchall()
    periods = [r["period"] for r in rows if cutoff is None or r["period"] < cutoff]
    if not periods:
        return None
    payload = {
        "periods": periods,
        "dim": dim_name,
        "series": _dim_frame(metric, grain, dim_name, periods),
        "inner": None,
        "parents": {},
    }
    inner_dim = PLAYER_INNER_DIM.get(dim_name)
    if inner_dim:
        payload["inner"] = {
            "dim": inner_dim,
            "series": _dim_frame(metric, grain, inner_dim, periods),
        }
        pcol, ccol = (("finance_l1", "finance_l2")
                      if dim_name == "finance_l2" else ("category_l1", "category_l2"))
        try:
            with _conn() as conn:
                pairs = conn.execute(
                    "SELECT DISTINCT %s, %s FROM category_tree" % (pcol, ccol)).fetchall()
            payload["parents"] = {r[1]: r[0] for r in pairs}
        except sqlite3.Error:
            payload["parents"] = {}  # snapshot predates category_tree
    return payload


def dims_for_metrics(metrics):
    metrics = sorted(m for m in metrics if m)
    return _memoized(lambda: _dims_for_metrics(metrics),
                     "dims_for_metrics", tuple(metrics))


def _dims_for_metrics(metrics):
    """Set of single dims (no pairs, no total) any of these metrics carries —
    drives which filter rows a page shows."""
    if not metrics:
        return set()
    qmarks = ",".join("?" * len(metrics))
    with _conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT dim_name FROM metrics WHERE metric IN (%s) "
            "AND dim_name != 'total'" % qmarks,   # any grain: wau/dau have no monthly rows
            metrics,
        ).fetchall()
    return {r["dim_name"] for r in rows if PAIR_SEP not in r["dim_name"]}


def category_tree():
    return _memoized(_category_tree, "category_tree")


def _category_tree():
    """[[finance_l1, finance_l2, category_l1..l4], ...] hierarchy paths for
    cascading filter value lists. Empty if the snapshot predates the table."""
    try:
        with _conn() as conn:
            return [list(r) for r in conn.execute(
                "SELECT finance_l1, finance_l2, category_l1, category_l2, "
                "category_l3, category_l4 FROM category_tree")]
    except sqlite3.Error:
        return []


def _sum_points(list_of_series):
    """Sum several [[period, value]] series period-wise."""
    acc = {}
    for pts in list_of_series:
        for p, v in pts:
            acc[p] = acc.get(p, 0.0) + v
    return [[p, acc[p]] for p in sorted(acc)]


def _metric_has_dim(metric, dim_name, grain="monthly"):
    with _conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM metrics WHERE metric = ? AND grain = ? "
            "AND dim_name = ? LIMIT 1", (metric, grain, dim_name),
        ).fetchone()
    return row is not None


def _slices(metric, dim_name, values, grain="monthly"):
    """Series per requested slice value (empty slices dropped)."""
    out = []
    for v in values:
        pts = series_at(metric, grain, dim_name, v)
        if pts:
            out.append(pts)
    return out


def resolve_series(metric, constraints, grain="monthly"):
    """Series at a grain for a metric under N filter constraints.

    constraints: list of (dim_name, [values]) in priority order (the caller
    collapses each category/finance family to its deepest selected level).

    Returns (points, status) where status is a dict:
      applied: [dim, ...]   constraints actually applied
      ignored: [dim, ...]   constraints this metric couldn't honor
      approx:  bool         slices of a distinct-count metric were summed
      total:   bool         nothing applied — points are the site total
    """
    constraints = [(d, vs) for d, vs in constraints if vs]

    def st(applied, ignored, approx=False, total=False):
        return {"applied": applied, "ignored": ignored,
                "approx": approx, "total": total}

    if not constraints:
        return series_at(metric, grain), st([], [])

    def approx_for(n_slices):
        return n_slices > 1 and metric in NON_ADDITIVE

    dims = [c[0] for c in constraints]

    if len(constraints) >= 2:
        # exact two-dim combination via a precomputed pair slice
        for i in range(len(constraints)):
            for j in range(len(constraints)):
                if i == j:
                    continue
                da, va = constraints[i]
                db, vb = constraints[j]
                pair_name = da + PAIR_SEP + db
                if not _metric_has_dim(metric, pair_name, grain):
                    continue
                combos = [a + PAIR_SEP + b for a in va for b in vb]
                parts = _slices(metric, pair_name, combos, grain)
                ignored = [d for d in dims if d not in (da, db)]
                if parts:
                    return _sum_points(parts), st([da, db], ignored, approx_for(len(parts)))
                return [], st([da, db], ignored)  # slice exists but is empty → zero

    # single-dim application, in priority order
    for dim, vals in constraints:
        parts = _slices(metric, dim, vals, grain)
        if parts:
            ignored = [d for d in dims if d != dim]
            return _sum_points(parts), st([dim], ignored, approx_for(len(parts)))

    return series_at(metric, grain), st([], dims, total=True)


def kpi_from_points(points, grain="monthly", spark=None):
    """Stat-tile payload from an already-computed [[period, value]] series
    (used for derived series like the liquidity rate)."""
    cutoff = current_period_key(grain)
    full = [p for p in points if p[0] < cutoff] if grain != "daily" else points
    if not full:
        return None
    vals = dict(points)
    p0, v0 = full[-1]
    (l1, s1), (l2, s2) = GRAIN_DELTAS[grain]
    return {
        "period": p0,
        "value": v0,
        "mom": _pct(v0, vals.get(shift_period(p0, grain, s1))),
        "yoy": _pct(v0, vals.get(shift_period(p0, grain, s2))),
        "labels": [l1, l2],
        "spark": (spark if spark is not None else full[-13:]),
        "spark_grain": grain,
    }


def search_keywords():
    """Top-keywords rows for the Search dashboard, with their 28d window.
    Empty result when the snapshot predates the search_keywords table."""
    try:
        with _conn() as conn:
            rows = [list(r) for r in conn.execute(
                "SELECT platform, keyword, filtered, searches, zsr, low, "
                "avg_results FROM search_keywords ORDER BY searches DESC")]
    except sqlite3.Error:
        return {"window": [None, None], "rows": []}
    info = snapshot_info()
    return {"window": [info.get("search_kw_start"), info.get("search_kw_end")],
            "rows": rows}


def ratio_at(numerator, denominator, grain="monthly", as_pct=True):
    """Per-period numerator/denominator over shared periods (both totals)."""
    num = dict(series_at(numerator, grain))
    den = dict(series_at(denominator, grain))
    out = []
    for p in sorted(set(num) & set(den)):
        if den[p]:
            v = num[p] / den[p]
            out.append([p, round(v * 100.0, 2) if as_pct else round(v, 4)])
    return out
