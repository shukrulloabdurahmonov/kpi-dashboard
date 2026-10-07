"""WBR Truth Board: weekly tteam Trino vs Yamato grid built from the snapshot.

Inputs are the `wbr_*` rows that updater/wbr_extract.py + updater/wbr_merge.py
put in the metrics table (daily site totals per source, plus week-distinct
repliers). This module turns them into the weekly board served at
/api/wbr-truth-board and drawn by webapp/docs/wbr_truth_board.html.

Comparison rules (keep in sync with the board's footer text):
  * Both sides are aggregated over the SAME days: a day is dropped from both
    when Trino is missing/zero, Yamato is missing/zero, or Trino < 70% of
    Yamato (a partial load). Reactivated keeps every loaded day — its gap is
    a definition difference, not a load gap.
  * KNOWN_PARTIAL_LOADS lists date ranges to drop on top of that, per metric,
    for loads that are short but above the 70% line. Update it when a
    reload lands or a new gap appears.
  * Additive metrics sum their days; stock/audience metrics (active listings,
    DAU) average them. Ratios are computed from the weekly components only
    when both cover the same days.

Status, notes and source tables per row live in BOARD_ROWS below — they are
the team's current reading of each gap; edit them when the reading changes.
Stdlib only.
"""

from datetime import date, timedelta

from webapp import data

# Same values as updater/wbr_common.py (the Docker image ships webapp/ only,
# so they are repeated here; keep the two in sync).
WBR_PREFIX = "wbr_"
WBR_META_NAMES = ["wbr_trino", "wbr_yamato"]
SOURCE_DIM = "source"
WBR_START = "2026-01-01"

GAP_RATIO = 0.7
ADDITIVE = ["nnl", "se", "rd", "rv", "tr", "py", "st", "re", "vw"]
AVERAGED = ["al", "dau"]
HYDRA = ["se", "rd", "vw", "dau", "repwk"]

# Extra exclusions (inclusive ISO dates, None = open-ended).
KNOWN_PARTIAL_LOADS = [
    {"metrics": HYDRA, "from": "2026-09-07", "to": None,
     "why": "tteam Hydra partly loaded since 7 Sep 2026 (SE/views/DAU about 3-10% short)"},
]

BOARD_ROWS = [
 {"key": "nnl", "name": "NNL", "kind": "int", "status": "bug", "group_start": True,
  "trino": "gold.olxuz_nnl_daily ← gold.fact_olxuz_listings.nnl_date ← silver.stg_olxuz_ads_first_net", "yamato": "eu_bi.fact_listings: count(distinct listing_sk) where listing_net_sk = 'net', by first_active_date_nk",
  "note": "Within ±1% until July. From August, 1–2.6% low: listings that existed before CDC and first went net later are dated by created_at_first (pre-CDC branch of stg_olxuz_ads_first_net)."},
 {"key": "st", "name": "Started", "kind": "int", "status": "bug",
  "trino": "gold.olxuz_started_react_daily.n_started (= NNL listings by nnl_date)", "yamato": "cubes.fact_listings_insertions_cube.new_insertions",
  "note": "Since 2 Oct Started counts listings, so it is NNL. It carries the same pre-CDC gap from August."},
 {"key": "re", "name": "Reactivated", "kind": "int", "status": "gap",
  "trino": "gold.olxuz_started_react_daily.n_react (CDC rows flipping net_ad_counted to 1)", "yamato": "cubes.fact_listings_insertions_cube.renewal_insertions (eu_bi.fact_listings_insertions.is_renewal)",
  "note": "Different events. Trino counts net-flag flips, which happen once per listing and include every new listing. Yamato counts renewals. About −70% every week Jan–Jun (and −62% from September); mid-July to August runs +5% to +86% while listings that existed before CDC get their first CDC update and count as flips."},
 {"key": "al", "name": "Active listings", "kind": "int", "status": "match",
  "trino": "gold.olxuz_active_listings_daily ← gold.dim_olxuz_listing_active_history (weekly average)", "yamato": "eu_bi.fact_active_listings: count(distinct listing_sk) per date_nk (weekly average)",
  "note": "Matches within 0.2%. Trino stock exists only from 3 Aug."},
 {"key": "se", "name": "SE", "kind": "int", "status": "match", "group_start": True,
  "trino": "gold.olxuz_se_daily ← gold.fact_olxuz_replies_events (is_first_contact, session × listing)", "yamato": "eu_bi.fact_replies_success_legacy: count(*) by date_sent_nk",
  "note": "+8% in January, shrinking to about +0.3% by June: Trino's reply history starts around January, so early repeat contacts look like first contacts. Same session × listing key on both sides since 2 Oct."},
 {"key": "repwk", "name": "Repliers (weekly)", "kind": "int", "status": "match",
  "trino": "gold.olxuz_repliers_weekly.replier_days (ALL)", "yamato": "eu_bi.fact_replies_success_legacy: count(distinct session_long_sk) per ISO week",
  "note": "Week-distinct sessions with a first contact. Within +0.5% (median)."},
 {"key": "ser", "name": "SE / replier", "kind": "r2", "status": "match",
  "trino": "gold.olxuz_se_daily (week sum) / gold.olxuz_repliers_weekly", "yamato": "eu_bi.fact_replies_success_legacy: count(*) / count(distinct session_long_sk) per week",
  "note": "Fixed on 2 Oct by olxuz_repliers_weekly. Before that the WBR summed daily repliers and showed about −29%."},
 {"key": "liq", "name": "Liquidity", "kind": "r3", "status": "match",
  "trino": "gold.olxuz_se_daily / avg(gold.olxuz_active_listings_daily)", "yamato": "eu_bi.fact_replies_success_legacy / avg(eu_bi.fact_active_listings)",
  "note": "SE ÷ average stock. Few comparable weeks: Trino stock starts 3 Aug and Hydra is partly loaded from 7 Sep."},
 {"key": "vw", "name": "Views", "kind": "int", "status": "small", "group_start": True,
  "trino": "gold.olxuz_views_daily ← gold.fact_olxuz_ad_page_daily (distinct session × listing × day)", "yamato": "eu_bi.fact_listings_traffic: distinct (date_sent_nk, listing_sk, session_long_sk) on pv|ad_page|%",
  "note": "+0.3% to +1.1% a week: bot traffic Trino does not filter. Yamato dates traffic by UTC day, which only shifts views between days."},
 {"key": "dau", "name": "DAU", "kind": "int", "status": "gap",
  "trino": "gold.olxuz_dau_site_daily ← gold.fact_olxuz_audience_daily (weekly average)", "yamato": "eu_bi.fact_audience_categories: count(distinct session_long_sk) where applicable_to_active_users and not is_outlier (weekly average)",
  "note": "+5.6% to +8.4% every week: Trino has no is_outlier bot filter."},
 {"key": "sev", "name": "SE / views %", "kind": "pct", "status": "match",
  "trino": "gold.olxuz_se_daily / gold.olxuz_views_daily", "yamato": "eu_bi.fact_replies_success_legacy / eu_bi.fact_listings_traffic",
  "note": "Follows the SE burn-in: higher in January, about 0% from June."},
 {"key": "vpl", "name": "Views / listing", "kind": "r2", "status": "small",
  "trino": "gold.olxuz_views_daily / avg(gold.olxuz_active_listings_daily)", "yamato": "eu_bi.fact_listings_traffic / avg(eu_bi.fact_active_listings)",
  "note": "Needs Trino stock and loaded views in the same week: few comparable weeks."},
 {"key": "rv", "name": "Revenue (net)", "kind": "big", "status": "small", "group_start": True,
  "trino": "gold.olxuz_revenue_daily.revenue_net ← gold.fact_olxuz_payments (latest version per payment id)", "yamato": "eu_bi.fact_payments + eu_bi.dim_products: sum(trans_value_net), excl. Not Revenue and zero-net rows",
  "note": "+0.5% to +2% every week (one +3.8% week): the bonus / IFRS / is_valid residual. The double load was fixed on 2 Oct."},
 {"key": "tr", "name": "Transactions", "kind": "int", "status": "match",
  "trino": "gold.olxuz_revenue_daily.n_transactions (daily distinct id_transaction)", "yamato": "eu_bi.fact_payments: count(distinct transaction_sk) per day",
  "note": "−0.2% to −0.6% every week. The reference value 135,493 for w35 doesn't come from any Yamato table found so far."},
 {"key": "py", "name": "Payers", "kind": "int", "status": "match",
  "trino": "gold.olxuz_revenue_daily.n_payers (Σ distinct id_user per day × L1 × product)", "yamato": "eu_bi.fact_payments: Σ count(distinct user_sk) per day × L1 × product_nk",
  "note": "Same grain on both sides since 2 Oct: within ±0.3%. The reference 84,411 for w35 is still not reproducible."},
 {"key": "arpi", "name": "ARPI", "kind": "int", "status": "bug",
  "trino": "gold.olxuz_revenue_daily / gold.olxuz_nnl_daily", "yamato": "eu_bi.fact_payments / eu_bi.fact_listings (NNL)",
  "note": "Revenue residual plus the NNL shortfall from August."},
 {"key": "atv", "name": "ATV", "kind": "int", "status": "small",
  "trino": "gold.olxuz_revenue_daily: revenue_net / n_transactions", "yamato": "eu_bi.fact_payments: sum(trans_value_net) / count(distinct transaction_sk)",
  "note": "Revenue residual only: about +1.5% a week."},
]


def _excluded(key, day):
    for r in KNOWN_PARTIAL_LOADS:
        if key in r["metrics"] and day >= r["from"] and (r["to"] is None or day <= r["to"]):
            return True
    return False


def _monday(day):
    d = date.fromisoformat(day)
    return (d - timedelta(days=d.weekday())).isoformat()


def _load():
    daily, weekly = {}, {}
    with data._conn() as conn:
        for r in conn.execute(
                "SELECT metric, grain, period, dim_value, value FROM metrics "
                "WHERE metric LIKE ? AND dim_name = ? AND period >= ?",
                (WBR_PREFIX + "%", SOURCE_DIM, _monday(WBR_START))):
            key = r["metric"][len(WBR_PREFIX):]
            target = daily if r["grain"] == "daily" else weekly
            target.setdefault(key, {}).setdefault(r["dim_value"], {})[r["period"]] = r["value"]
        meta = {r["metric"]: dict(r) for r in conn.execute(
            "SELECT * FROM meta WHERE metric IN (%s)" % ",".join("?" * len(WBR_META_NAMES)),
            WBR_META_NAMES)}
        info = {r["key"]: r["value"] for r in conn.execute(
            "SELECT key, value FROM snapshot_info WHERE key LIKE 'wbr_%' OR key = 'built_at_utc'")}
    return daily, weekly, meta, info


def _build():
    daily, weekly, meta, info = _load()
    t_days = sorted({d for k in daily for d in daily[k].get("trino", {}) if d >= WBR_START})
    weeks = sorted({_monday(d) for d in t_days})
    widx = {w: i for i, w in enumerate(weeks)}
    out = {}

    def kept(key, d):
        t = daily.get(key, {}).get("trino", {}).get(d)
        y = daily.get(key, {}).get("yamato", {}).get(d)
        if not t or not y:
            return None
        if key != "re" and (t / y < GAP_RATIO or _excluded(key, d)):
            return None
        return t, y

    for key in ADDITIVE + AVERAGED:
        acc = [[0.0, 0.0, 0] for _ in weeks]
        for d in t_days:
            ty = kept(key, d)
            if ty:
                a = acc[widx[_monday(d)]]
                a[0] += ty[0]; a[1] += ty[1]; a[2] += 1
        avg = key in AVERAGED
        out[key] = {
            "t": [round(a[0] / a[2] if avg else a[0], 2) if a[2] else None for a in acc],
            "y": [round(a[1] / a[2] if avg else a[1], 2) if a[2] else None for a in acc],
            "n": [a[2] for a in acc],
        }

    # week-distinct repliers: only weeks whose SE days are all kept
    rt, ry = weekly.get("repwk", {}).get("trino", {}), weekly.get("repwk", {}).get("yamato", {})
    full = [out["se"]["n"][i] == 7 and not _excluded("repwk", w) for i, w in enumerate(weeks)]
    out["repwk"] = {"t": [rt.get(w) if full[i] else None for i, w in enumerate(weeks)],
                    "y": [ry.get(w) if full[i] else None for i, w in enumerate(weeks)],
                    "n": [7 if f else 0 for f in full]}

    def ratio(a, b, scale=1.0):
        A, B = out[a], out[b]
        res = {"t": [], "y": [], "n": []}
        for i in range(len(weeks)):
            ok = A["n"][i] == B["n"][i] and A["n"][i] > 0
            for s in ("t", "y"):
                num, den = A[s][i], B[s][i]
                res[s].append(round(num / den * scale, 4) if ok and num and den else None)
            res["n"].append(A["n"][i] if ok else 0)
        return res

    out["liq"] = ratio("se", "al")
    out["arpi"] = ratio("rv", "nnl")
    out["atv"] = ratio("rv", "tr")
    out["sev"] = ratio("se", "vw", 100)
    out["vpl"] = ratio("vw", "al")
    se, rw = out["se"], out["repwk"]
    out["ser"] = {s: [round(se[s][i] / rw[s][i], 3) if rw[s][i] and se["n"][i] == 7 else None
                      for i in range(len(weeks))] for s in ("t", "y")}
    out["ser"]["n"] = rw["n"]

    return {
        "weeks": weeks, "m": out, "rows": BOARD_ROWS,
        "partial_loads": KNOWN_PARTIAL_LOADS, "gap_ratio": GAP_RATIO,
        "extracted_at_utc": info.get("wbr_extracted_at_utc"),
        "snapshot_built_at_utc": info.get("built_at_utc"),
        "sources": {k: {"status": v.get("status"), "extracted_at_utc": v.get("extracted_at_utc"),
                        "latest_day": v.get("latest_period"), "error": v.get("error")}
                    for k, v in meta.items()},
    }


def board():
    """Weekly board payload (memoized per snapshot file); None without wbr data."""
    res = data._memoized(_build, "wbr_board")
    return res if res["weeks"] else None
