"""OLX UZ KPI dashboard — Flask app.

Reads the read-only SQLite snapshot (shipped daily from the Mac updater);
never touches the warehouse. Shared-password session login.

Every tab carries a dimension filter (?dim=...&value=...). All slices were
pre-aggregated in Redshift, so a filter just selects a different set of
rows — nothing is ever re-aggregated here. Metrics that don't carry the
chosen dimension fall back to the site total and say so in their label.
"""

import base64
import json
import os
import secrets
import time
import urllib.parse
import urllib.request
from urllib.parse import quote

from flask import (
    Flask, g, jsonify, make_response, redirect, render_template, request,
    session, url_for,
)
from itsdangerous import URLSafeTimedSerializer
from werkzeug.security import check_password_hash, generate_password_hash

from webapp import data
from webapp.definitions import METRIC_DEFS, info

KPI_TABS = [
    ("overview", "/overview", "Overview"),
    ("listings", "/listings", "Listings"),
    ("engagement", "/engagement", "Engagement"),
    ("liquidity", "/liquidity", "Liquidity"),
    ("monetization", "/monetization", "Monetization"),
    ("users", "/users", "Users"),
    ("player", "/player", "Timeline Player"),
    ("dictionary", "/dictionary", "Dictionary"),
]
TABS = KPI_TABS  # legacy alias; hand routes below still reference it

SEARCH_TABS = [
    ("search", "/search", "Search volume"),
    ("search_users_tab", "/search/users", "Search users"),
    ("search_zsr", "/search/zsr", "Zero results"),
    ("search_ctr", "/search/ctr", "CTR"),
    ("search_keywords", "/search/keywords", "Keywords"),
    ("search_methodology", "/search/methodology", "Methodology"),
    ("search_definitions", "/search/definitions", "Definitions"),
]

# tabs with hand-written routes, skipped by the render_tab registration loop
HAND_ROUTED = {"dictionary", "player", "search_methodology", "search_definitions"}

JIRA_REQUEST_URL = (
    "https://tteam.atlassian.net/jira/software/projects/AN/list"
    "?jql=project%20%3D%20AN%20ORDER%20BY%20cf%5B10019%5D%20ASC"
)

# Landing-page registry (Dashxona convention, hardcoded — no folder scan).
KPI_DASH_TITLE = "OLX UZ KPI dashboard"
SEARCH_DASH_TITLE = "OLX UZ Search"
DASHBOARDS = [
    {"slug": "kpi", "title": KPI_DASH_TITLE,
     "description": "Marketplace health metrics: listings, engagement, "
                    "liquidity, monetization, users.",
     "requested_by": "T-Team", "url": "/overview", "tabs": KPI_TABS},
    {"slug": "search", "title": SEARCH_DASH_TITLE,
     "description": "Search KPIs: search funnel, volume and method mix, "
                    "category and region splits, top keywords with "
                    "zero-result rates.",
     "requested_by": "T-Team", "url": "/search", "tabs": SEARCH_TABS},
]
DASH_TITLE_BY_TAB = {
    tab_id: d["title"] for d in DASHBOARDS for tab_id, _, _ in d["tabs"]
}
TABS_BY_TAB = {
    tab_id: d["tabs"] for d in DASHBOARDS for tab_id, _, _ in d["tabs"]
}

# Timeline Player metric roster: key → (label, {grain: stored metric}).
# Metrics without a daily entry get the day toggle disabled.
def _all_grains(m):
    return {"monthly": m, "weekly": m, "daily": m}


PLAYER_METRICS = [
    ("nnl", "Net new listings", _all_grains("nnl")),
    ("revenue_net", "Revenue net (UZS)", _all_grains("revenue_net")),
    ("pmul", "Paying listers", _all_grains("pmul")),
    ("unique_listers", "Unique listers", _all_grains("unique_listers")),
    ("replies", "Replies", _all_grains("replies")),
    ("unique_repliers", "Unique repliers", _all_grains("unique_repliers")),
    ("active_users", "Active users", {"monthly": "mau", "weekly": "wau", "daily": "dau"}),
    ("gnl", "Gross new listings", _all_grains("gnl")),
    ("insertions_all", "Insertions", _all_grains("insertions_all")),
]
PLAYER_METRIC_MAP = {k: grains for k, _, grains in PLAYER_METRICS}
PLAYER_DIMS = [
    ("finance_l1", "Fin category L1"),
    ("finance_l2", "Fin category L2"),
    ("category_l1", "Category L1"),
    ("category_l2", "Category L2"),
]

DIM_LABELS = [
    ("finance_l1", "Finance category L1"),
    ("finance_l2", "Finance category L2"),
    ("category_l1", "Category L1"),
    ("category_l2", "Category L2"),
    ("category_l3", "Category L3"),
    ("category_l4", "Category L4"),
    ("region", "Region"),
    ("seller_type", "Seller type"),
    ("revenue_stream", "Revenue stream"),
]
VALID_DIMS = {d for d, _ in DIM_LABELS}

UZS = "UZS"


DIM_LABEL_MAP = dict(DIM_LABELS)
CATEGORY_FAMILY = ["category_l4", "category_l3", "category_l2", "category_l1"]
FINANCE_FAMILY = ["finance_l2", "finance_l1"]

# --- time grain -------------------------------------------------------------
GRAIN_PARAM = {"day": "daily", "week": "weekly", "month": "monthly"}
GRAIN_PARAM_INV = {v: k for k, v in GRAIN_PARAM.items()}
# metrics whose name differs per grain (same definition, different period)
GRAIN_METRIC = {"mau": {"weekly": "wau", "daily": "dau"}}


def current_grain():
    """Store grain from ?grain=day|week|month (default: monthly)."""
    return GRAIN_PARAM.get(request.args.get("grain", ""), "monthly")


def metric_at(metric, grain):
    return GRAIN_METRIC.get(metric, {}).get(grain, metric)


def current_constraints():
    """[(dim, [values]), ...] from per-dim query params (?category_l2=X&
    category_l2=Y&region=Z...). Each category/finance family collapses to its
    deepest selected level (the cascade means deeper implies shallower).
    Priority order: category, finance, region, seller, stream."""
    raw = {}
    for dim in VALID_DIMS:
        vals = [v for v in request.args.getlist(dim) if v]
        if vals:
            raw[dim] = vals
    out = []
    for family in (CATEGORY_FAMILY, FINANCE_FAMILY):
        for dim in family:  # deepest first
            if dim in raw:
                out.append((dim, raw[dim]))
                break
    for dim in ("region", "seller_type", "revenue_stream"):
        if dim in raw:
            out.append((dim, raw[dim]))
    return out


def raw_selected():
    """{dim: [values]} exactly as in the URL — for re-rendering the panel."""
    out = {}
    for dim in VALID_DIMS:
        vals = [v for v in request.args.getlist(dim) if v]
        if vals:
            out[dim] = vals
    return out


# --------------------------------------------------------------------------
# Series helpers — filter-aware
# --------------------------------------------------------------------------

def track(metric):
    """Record which metrics a page uses, so the filter panel can show only
    the dimensions that apply to this page."""
    if hasattr(g, "page_metrics"):
        g.page_metrics.add(metric)
    return metric


def _suffix(status):
    """Human-readable label suffix for how a filter was applied."""
    parts = []
    if status.get("total") and (status["ignored"] or status["applied"]):
        return " (site total)"
    if status.get("ignored"):
        parts.append(" (%s not applied)" % ", ".join(
            DIM_LABEL_MAP.get(d, d) for d in status["ignored"]))
    if status.get("approx"):
        parts.append(" ≈")
    return "".join(parts)


def named(label, metric, grain=None):
    """Series for a metric under the active filter constraints and the
    page's time grain (or an explicit fixed grain), annotated when the
    filter could only be partially or approximately applied."""
    g = grain or current_grain()
    m = metric_at(metric, g)
    track(m)
    constraints = current_constraints()
    pts, status = data.resolve_series(m, constraints, g)
    return {"label": label + (_suffix(status) if constraints else ""),
            "points": pts}


def card(label, metric, spark_metric=None, spark_grain="daily", unit=None, fmt=None):
    g = current_grain()
    m = metric_at(metric, g)
    track(m)
    constraints = current_constraints()
    payload = None
    badge = None
    if constraints:
        pts, status = data.resolve_series(m, constraints, g)
        if status["total"]:
            badge = "site total"
        else:
            payload = data.kpi_from_points(pts, g)
            bits = []
            if status["ignored"]:
                bits.append("%s n/a" % ", ".join(
                    DIM_LABEL_MAP.get(d, d) for d in status["ignored"]))
            if status["approx"]:
                bits.append("≈ summed")
            badge = "; ".join(bits) or None
            if payload is None:
                # the filtered slice is legitimately empty — show an honest
                # empty card, never the unfiltered site total unbadged
                return {"label": label, "kpi": None, "unit": unit, "fmt": fmt,
                        "badge": badge, "info": info(metric)}
    if payload is None:
        if g == "monthly":
            payload = data.kpi(m, spark_metric=spark_metric, spark_grain=spark_grain)
        else:
            payload = data.kpi(m, g)
    return {"label": label, "kpi": payload, "unit": unit, "fmt": fmt,
            "badge": badge, "info": info(metric)}


def line(title, series_list, unit=None, grain=None, note=None, pct=False,
         info_key=None, area=False):
    series = [s for s in series_list if s["points"]]
    if not series:
        return None
    grain = grain or current_grain()
    return {"kind": "line", "title": title, "unit": unit, "grain": grain,
            "series": series, "note": note,
            "pct": pct, "area": area, "info": info(info_key) if info_key else None}


def stacked(title, series_list, unit=None, note=None, info_key=None):
    series = [s for s in series_list if s["points"]]
    if not series:
        return None
    return {"kind": "stacked", "title": title, "unit": unit,
            "grain": current_grain(), "series": series, "note": note,
            "info": info(info_key) if info_key else None}


def _monthly_pin_note(note):
    """Snapshot charts stay monthly by design — say so when the page grain
    differs, so a week/day page never reads as inconsistent."""
    if current_grain() == "monthly":
        return note
    pin = "Monthly view — not affected by the grain toggle."
    return (note + " " + pin) if note else pin


def barh(title, metric, dim_name="finance_l2", unit=None, note=None):
    track(metric)
    period, rows = data.breakdown(metric, dim_name)
    if not rows:
        return None
    t = title + (" — " + period if period else "")
    return {"kind": "barh", "title": t, "unit": unit, "rows": rows,
            "note": _monthly_pin_note(note), "info": info(metric)}


def mom_growth(title, metric, dim_name="finance_l2", note=None):
    """Diverging bars: MoM % change per dim value, latest full month."""
    track(metric)
    p0 = data.latest_full_period(metric, "monthly", dim_name)
    if p0 is None:
        return None
    prev = data._shift_month(p0, -1)
    _, cur_rows = data.breakdown(metric, dim_name, period=p0, top_n=14)
    _, prev_rows = data.breakdown(metric, dim_name, period=prev, top_n=100)
    prev_map = dict(prev_rows)
    rows = []
    for name, v in cur_rows:
        pv = prev_map.get(name)
        if pv:
            rows.append([name, round((v - pv) / pv * 100.0, 1)])
    if not rows:
        return None
    rows.sort(key=lambda r: -r[1])
    return {"kind": "divergingbarh", "title": "%s — %s vs %s" % (title, p0, prev),
            "rows": rows, "note": _monthly_pin_note(note), "info": info(metric),
            "pct": True}


def heatmap(title, metric, note=None):
    track(metric)
    pts = data.daily(metric)
    if not pts:
        return None
    return {"kind": "heatmap", "title": title, "points": pts, "note": note,
            "info": info(metric)}


def region_map(title, metric, unit=None, note=None):
    """Uzbekistan choropleth of the latest full month by region."""
    track(metric)
    period, rows = data.breakdown(metric, "region", top_n=20)
    if not rows:
        return None
    return {"kind": "map", "title": "%s — %s" % (title, period), "unit": unit,
            "rows": rows, "note": _monthly_pin_note(note), "info": info(metric)}


def matrix(title, metric, dim_name="finance_l2", months=13, top_n=12, note=None):
    """dim value × month heatmap matrix of the last `months` full months."""
    track(metric)
    values = data.dim_values(metric, dim_name, top_n=top_n)
    if not values:
        return None
    periods = None
    rows = []
    for v in values:
        pts = data.monthly(metric, dim_name, v)[-months:]
        by_p = dict(pts)
        if periods is None:
            periods = [p for p, _ in pts]
        rows.append([v, [by_p.get(p) for p in (periods or [])]])
    if not periods:
        return None
    return {"kind": "matrix", "title": title, "periods": periods, "rows": rows,
            "note": _monthly_pin_note(note), "info": info(metric)}


def filtered_ratio(numerator, denominator, as_pct=True):
    """Ratio series honoring the active filter — but only when both metrics
    resolve to the SAME slice (mixing a filtered numerator with a total
    denominator would be silently wrong). Returns (points, note_suffix)."""
    g = current_grain()
    track(numerator)
    track(denominator)
    constraints = current_constraints()
    if constraints:
        num, s_num = data.resolve_series(numerator, constraints, g)
        den, s_den = data.resolve_series(denominator, constraints, g)
        same_slice = (s_num["applied"] == s_den["applied"]
                      and not s_num["total"] and not s_den["total"])
        if same_slice and num and den:
            den_map = dict(den)
            pts = []
            for p, v in num:
                if den_map.get(p):
                    r = v / den_map[p]
                    pts.append([p, round(r * 100.0, 2) if as_pct else round(r, 4)])
            return pts, _suffix(s_num)
        return data.ratio_at(numerator, denominator, g, as_pct), " (site total)"
    return data.ratio_at(numerator, denominator, g, as_pct), ""


def derived_diff(minuend, subtrahend, label):
    """Series of (minuend − subtrahend) at the page grain, honoring the
    active filter — but only when BOTH components resolve to the same slice
    (subtracting a filtered slice from a site total mixes populations)."""
    g = current_grain()
    track(minuend)
    track(subtrahend)
    constraints = current_constraints()
    a, sa = data.resolve_series(minuend, constraints, g)
    b, sb = data.resolve_series(subtrahend, constraints, g)
    suffix = ""
    if constraints:
        same_slice = (sa["applied"] == sb["applied"] and sa["total"] == sb["total"])
        if not same_slice:
            a = data.series_at(minuend, g)
            b = data.series_at(subtrahend, g)
            suffix = " (site total)"
        else:
            suffix = _suffix(sa)
    bmap = dict(b)
    return {"label": label + suffix,
            "points": [[p, v - bmap.get(p, 0)] for p, v in a]}


# --------------------------------------------------------------------------
# Page builders
# --------------------------------------------------------------------------

def build_overview():
    g = current_grain()
    liq_rate, liq_sfx = filtered_ratio("liquid_listings_7d_1r", "nnl")
    cards = [
        card("Net new listings", "nnl", spark_metric="nnl"),
        card("Active users", "mau", spark_metric="dau"),
        card("Unique listers", "unique_listers", spark_metric="unique_listers"),
        {"label": "Liquidity 7d (1 reply)" + liq_sfx,
         "kpi": data.kpi_from_points(liq_rate, g),
         "fmt": "pct", "info": info("liquidity_rate_7d_1r"), "badge": None, "unit": None},
        card("Unique repliers", "unique_repliers", spark_metric="unique_repliers"),
        card("Paying listers", "pmul", spark_metric="pmul"),
        card("Revenue net", "revenue_net", spark_metric="revenue_net", unit=UZS),
        card("Replies", "replies", spark_metric="replies"),
    ]
    charts = [
        line("Net new listings", [named("NNL", "nnl")], info_key="nnl"),
        line("Active users", [named("MAU", "mau")], info_key="mau"),
        line("Unique vs paying listers",
             [named("Unique listers", "unique_listers"), named("Paying listers", "pmul")],
             info_key="unique_listers"),
        line("Liquidity 7d, share of NNL cohort",
             [{"label": "Liquid in 7d (1 reply)" + liq_sfx, "points": liq_rate}],
             pct=True, info_key="liquidity_rate_7d_1r",
             note="Liquid listings ÷ NNL, monthly posting cohorts (mature cohorts only)"),
        line("Unique repliers", [named("Unique repliers", "unique_repliers")],
             info_key="unique_repliers"),
        line("Revenue net", [named("Revenue net", "revenue_net")], unit=UZS,
             area=True, info_key="revenue_net"),
    ]
    return {"cards": cards, "charts": charts}


def build_listings():
    cards = [
        card("Net new listings", "nnl", spark_metric="nnl"),
        card("Gross new listings", "gnl", spark_grain="monthly"),
        card("Active listings", "active_listings", spark_grain="monthly"),
        card("Insertions", "insertions_all", spark_grain="monthly"),
    ]
    charts = [
        line("NNL vs GNL", [named("NNL", "nnl"), named("GNL", "gnl")], info_key="nnl"),
        line("Active listings & listers",
             [named("Active listings", "active_listings"),
              named("Active listers", "active_listers")], info_key="active_listings"),
        stacked("Insertions: new vs renewed",
                [derived_diff("insertions_all", "insertions_renewed", "New"),
                 named("Renewed", "insertions_renewed")],
                note="New = all insertions − renewals (site total)",
                info_key="insertions_all"),
        stacked("Insertions: free vs paid",
                [named("Free", "insertions_free"),
                 derived_diff("insertions_all", "insertions_free", "Paid")],
                note="Paid = all insertions − free (site total)",
                info_key="insertions_free"),
        line("Insertions trend",
             [named("All", "insertions_all"), named("Renewed", "insertions_renewed"),
              named("Free", "insertions_free")],
             info_key="insertions_all"),
        line("Unique listers", [named("Unique listers", "unique_listers")],
             area=True, info_key="unique_listers"),
        barh("NNL by finance category", "nnl"),
        mom_growth("NNL MoM growth by category", "nnl"),
        region_map("NNL by region", "nnl"),
        matrix("NNL by category × month", "nnl"),
        line("NNL by seller type",
             data.multi_series("nnl", "seller_type", top_n=3, grain=current_grain()),
             note="Site total split (unfiltered)", info_key="nnl"),
    ]
    return {"cards": cards, "charts": charts}


def build_engagement():
    cards = [
        card("Active users", "mau", spark_metric="dau"),
        card("Pageviews", "pageviews", spark_grain="monthly"),
        card("Replies", "replies", spark_metric="replies"),
        card("Ad views", "ad_views", spark_grain="monthly"),
    ]
    charts = [
        line("Active users", [named("Active users", "mau")], info_key="mau"),
        line("Daily active users (90d)", [named("DAU", "dau", "daily")],
             grain="daily", area=True, info_key="dau"),
        heatmap("DAU by weekday", "dau",
                note="Rows = weekday, columns = week. Darker = more active users."),
        mom_growth("MAU MoM growth by category", "mau"),
        matrix("MAU by category × month", "mau"),
        line("Pageviews", [named("Pageviews", "pageviews")], area=True, info_key="pageviews"),
        line("Bounces", [named("Bounces", "bounces")], info_key="bounces"),
        line("Replies & unique repliers",
             [named("Replies", "replies"), named("Unique repliers", "unique_repliers")],
             info_key="replies"),
        barh("MAU by finance category", "mau"),
        line("Ad impressions", [named("Ad impressions", "ad_impressions")],
             info_key="ad_impressions"),
        line("Ad views", [named("Ad views", "ad_views")], info_key="ad_views"),
    ]
    return {"cards": cards, "charts": charts}


def build_liquidity():
    g = current_grain()
    rate_1r, sfx = filtered_ratio("liquid_listings_7d_1r", "nnl")
    rate_3r, _ = filtered_ratio("liquid_listings_7d_3r", "nnl")
    rate_14d, _ = filtered_ratio("liquid_listings_14d_1r", "nnl")
    rate_28d, _ = filtered_ratio("liquid_listings_28d_1r", "nnl")
    cards = [
        {"label": "Liquidity 7d (1 reply)" + sfx,
         "kpi": data.kpi_from_points(rate_1r, g),
         "fmt": "pct", "info": info("liquidity_rate_7d_1r"), "badge": None, "unit": None},
        {"label": "Liquidity 7d (3 replies)" + sfx,
         "kpi": data.kpi_from_points(rate_3r, g),
         "fmt": "pct", "info": info("liquid_listings_7d_3r"), "badge": None, "unit": None},
        card("Liquid listings 7d", "liquid_listings_7d_1r", spark_grain="monthly"),
        card("First-time listers success", "ftl_success_listers_14d_3r", spark_grain="monthly"),
    ]
    charts = [
        line("Liquidity rate by window (share of NNL cohort)",
             [{"label": "7d / 1 reply", "points": rate_1r},
              {"label": "14d / 1 reply", "points": rate_14d},
              {"label": "28d / 1 reply", "points": rate_28d}],
             pct=True, info_key="liquidity_rate_7d_1r",
             note="Mature posting cohorts only — a cohort month appears once its window closes (unfiltered)"),
        line("Liquidity depth: 1 vs 3 replies (7d)",
             [{"label": "7d / 1 reply", "points": rate_1r},
              {"label": "7d / 3 replies", "points": rate_3r}], pct=True,
             info_key="liquid_listings_7d_3r"),
        line("Liquid listings (7d)",
             [named("1 reply", "liquid_listings_7d_1r"),
              named("3 replies", "liquid_listings_7d_3r")], info_key="liquid_listings_7d_1r"),
        line("Liquid listers (7d, 1 reply)",
             [named("Liquid listers", "liquid_listers_7d_1r")],
             area=True, info_key="liquid_listers_7d_1r"),
        barh("Liquid listings 7d by finance category", "liquid_listings_7d_1r"),
        region_map("Liquid listings 7d by region", "liquid_listings_7d_1r"),
        line("Daily liquid listings 7d/1r (mature cohort days)",
             [named("Liquid listings", "liquid_listings_7d_1r", "daily")], grain="daily",
             info_key="liquid_listings_7d_1r"),
        line("First-time listers success (14d / 3 replies)",
             [named("Listings", "ftl_success_listings_14d_3r"),
              named("Listers", "ftl_success_listers_14d_3r")],
             info_key="ftl_success_listers_14d_3r"),
    ]
    return {"cards": cards, "charts": charts}


def build_monetization():
    cards = [
        card("Revenue net", "revenue_net", spark_metric="revenue_net", unit=UZS),
        card("Paying listers", "pmul", spark_metric="pmul"),
        card("Transactions", "transactions", spark_grain="monthly"),
        card("Cash flows", "cash_flows", spark_grain="monthly"),
    ]
    charts = [
        line("Revenue net vs gross",
             [named("Net", "revenue_net"), named("Gross", "revenue_gross")],
             unit=UZS, info_key="revenue_net"),
        stacked("Revenue net by stream",
                data.multi_series("revenue_net", "revenue_stream", top_n=4,
                                  grain=current_grain()),
                unit=UZS, note="Site total split (unfiltered)", info_key="revenue_net"),
        line("Paying listers", [named("Paying listers", "pmul")], area=True, info_key="pmul"),
        barh("Paying listers by finance category", "pmul"),
        mom_growth("Revenue net MoM growth by category", "revenue_net"),
        region_map("Revenue net by region", "revenue_net", unit=UZS),
        matrix("Revenue net by category × month", "revenue_net"),
        line("Transactions & payments",
             [named("Transactions", "transactions"), named("Payments", "payments")],
             info_key="transactions"),
        line("Bonus & refunds",
             [named("Bonus gross", "bonus_gross"), named("Refunds gross", "refund_gross")],
             unit=UZS, info_key="bonus_gross"),
        line("Cash-flow operations",
             [named("Cash flows", "cash_flows"), named("Unique payers", "cash_flow_payers")],
             info_key="cash_flows"),
    ]
    return {"cards": cards, "charts": charts}


def build_users():
    cards = [
        card("New users", "new_users", spark_grain="monthly"),
        card("Confirmed new users", "confirmed_new_users", spark_grain="monthly"),
        card("Unique listers", "unique_listers", spark_metric="unique_listers"),
        card("Unique repliers", "unique_repliers", spark_metric="unique_repliers"),
    ]
    charts = [
        line("New users", [named("New users", "new_users"),
                           named("Confirmed", "confirmed_new_users")], info_key="new_users"),
        line("Supply vs demand actors",
             [named("Unique listers", "unique_listers"),
              named("Unique repliers", "unique_repliers")], info_key="unique_listers"),
        line("Daily unique listers (90d)",
             [named("Listers", "unique_listers", "daily")], grain="daily",
             area=True, info_key="unique_listers"),
        line("Daily unique repliers (90d)",
             [named("Repliers", "unique_repliers", "daily")], grain="daily",
             area=True, info_key="unique_repliers"),
        region_map("Unique repliers by region ≈ latest month", "unique_repliers",
                   note="Distinct repliers per region (a user can appear in several regions)"),
    ]
    return {"cards": cards, "charts": charts}


# --------------------------------------------------------------------------
# Search dashboard — page-local helpers that never call track(), so the page
# renders with NO filter panel / grain toggle: every section pins its own
# grain (the only one its source exists at) and says so in a note.
# --------------------------------------------------------------------------

AVG_DAILY_NOTE = "Average daily values (weekly points = average of that week's days)."
ZSR_PLATFORM_NOTE = ("Android reports true zero-result rates (~15%); web and iOS "
                     "auto-extend empty searches, so their hard-zero rates are "
                     "near 0. Rates are never blended across platforms.")


def _s_line(title, series_list, grain, unit=None, note=None, pct=False,
            area=False, info_key=None):
    series = [s for s in series_list if s["points"]]
    if not series:
        return None
    return {"kind": "line", "title": title, "unit": unit, "grain": grain,
            "series": series, "note": note, "pct": pct, "area": area,
            "info": info(info_key) if info_key else None}


def _s_card(label, points, grain, fmt=None, unit=None, badge=None, info_key=None):
    return {"label": label, "kpi": data.kpi_from_points(points, grain),
            "fmt": fmt, "unit": unit, "badge": badge,
            "info": info(info_key) if info_key else None}


def _slice_ratio(num_metric, den_metric, grain, dim_name, dim_value, as_pct=True):
    """Per-period ratio of two metrics at the SAME dim slice."""
    num = dict(data.series_at(num_metric, grain, dim_name, dim_value))
    den = dict(data.series_at(den_metric, grain, dim_name, dim_value))
    out = []
    for p in sorted(set(num) & set(den)):
        if den[p]:
            v = num[p] / den[p]
            out.append([p, round(v * 100.0, 2) if as_pct else round(v, 4)])
    return out


SEARCH_PLATFORMS = ["Android", "iOS", "Web Desktop", "Web Mobile"]
# hydra clickstream tracks web as one platform
HYDRA_PLAT = {"Android": "Android", "iOS": "iOS",
              "Web Desktop": "Web", "Web Mobile": "Web"}


SEARCH_GRAINS = [("day", "daily", "Day"), ("week", "weekly", "Week"),
                 ("month", "monthly", "Month")]
SEARCH_GRAIN_PARAM = {p: g for p, g, _ in SEARCH_GRAINS}
SEARCH_GRAIN_INV = {g: p for p, g, _ in SEARCH_GRAINS}


def _search_ctx():
    """Page-local control: ?grain=day|week|month (platform filtering is
    per-chart, client-side, via each chart's pfilter payload)."""
    return SEARCH_GRAIN_PARAM.get(request.args.get("grain", ""), "weekly")


def _search_controls(grain):
    def url(g):
        return request.path + (("?grain=" + SEARCH_GRAIN_INV[g])
                               if g != "weekly" else "")
    return {"grains": [{"label": label, "url": url(g), "active": grain == g}
                       for _, g, label in SEARCH_GRAINS]}


def _pp(metric, grain, method=None):
    """{platform: series} for every platform that has data — the payload a
    chart's client-side platform filter recombines."""
    out = {}
    for p in SEARCH_PLATFORMS:
        if method:
            pts = data.series_at(metric, grain, "platform|method",
                                 p + data.PAIR_SEP + method)
        else:
            pts = data.series_at(metric, grain, "platform", p)
        if pts:
            out[p] = pts
    return out


def _psl(metric, grain, plat, method=None):
    """Series honoring the platform selection ('' = site total). With a
    method, uses the exact platform|method pair slice when a platform is
    selected."""
    if plat and method:
        return data.series_at(metric, grain, "platform|method",
                              plat + data.PAIR_SEP + method)
    if plat:
        return data.series_at(metric, grain, "platform", plat)
    if method:
        return data.series_at(metric, grain, "method", method)
    return data.series_at(metric, grain)


def _ratio_points(num_pts, den_pts, as_pct=True):
    num, den = dict(num_pts), dict(den_pts)
    out = []
    for p in sorted(set(num) & set(den)):
        if den[p]:
            v = num[p] / den[p]
            out.append([p, round(v * 100.0, 2) if as_pct else round(v, 4)])
    return out


def _noplat(note, plat):
    """Prefix for charts that have no per-platform slice."""
    if not plat:
        return note
    return "All platforms — this chart has no per-platform slice. " + note


GRAIN_WORD = {"daily": "day", "weekly": "week", "monthly": "month"}


def _mekko_cols(grain, dim_name="total", dim_value=None, periods=5):
    dim_value = dim_value or data.TOTAL
    users = data.series_at("search_users", grain, dim_name, dim_value)
    adview = dict(data.series_at("search_users_adview", grain,
                                 dim_name, dim_value))
    lead = dict(data.series_at("search_users_lead", grain, dim_name, dim_value))
    cols = []
    for period, u in users[-periods:]:
        a, l = adview.get(period), lead.get(period)
        if not u or a is None or l is None:
            continue
        cols.append({"label": period, "total": u, "segs": [
            ["Searched only", max(u - a, 0)],
            ["Ad view only", max(a - l, 0)],
            ["Sent a reply", l],
        ]})
    return cols


def _search_funnel_marimekko(grain, periods=5):
    """Funnel composition over the last N full periods as a marimekko: column
    width = that period's avg daily search users, segments = the mutually
    exclusive engagement split (searched only / ad view only / replied),
    which sums exactly to search users — unlike the nested funnel stages."""
    cols = _mekko_cols(grain, periods=periods)
    if len(cols) < 2:
        return None
    pdata = {p: c for p in SEARCH_PLATFORMS
             if (c := _mekko_cols(grain, "platform", p, periods))}
    trino_meta = data.meta().get("search_trino") or {}
    extracted = (trino_meta.get("extracted_at_utc") or "")[:10]
    word = GRAIN_WORD[grain]
    note = ("Column width = that %s's avg daily search users; segments are "
            "exclusive (they sum to all search users). " % word
            + AVG_DAILY_NOTE + " All platforms and methods."
            + (" Search data extracted %s." % extracted if extracted else ""))
    return {"kind": "marimekko",
            "title": "Search funnel by %s (avg daily users) — last %d %ss"
                     % (word, len(cols), word),
            "cols": cols, "note": note, "info": info("search_users"),
            "pfilter": {"mode": "mekko", "platforms": list(pdata),
                        "data": pdata, "approx": True}}


FILTER_DEPTHS = ["No filters", "1 filter", "2 filters", "3+ filters"]


def _filter_depth_stacked(plat=""):
    series = [{"label": d,
               "points": data.series_at("search_filter_depth", "monthly",
                                        "filter_depth", d)}
              for d in FILTER_DEPTHS]
    series = [s for s in series if s["points"]]
    if not series:
        return None
    return {"kind": "stacked", "title": "Keyword searches by filter depth (monthly totals)",
            "unit": None, "grain": "monthly", "series": series,
            "note": _noplat("Narrowing criteria per search: category, region, "
                            "price and attribute filters all count. Fixed "
                            "monthly view.", plat),
            "info": info("search_filter_depth")}


def _filter_type_bars(plat=""):
    period = data.latest_full_period("search_filter_use", "monthly", "filter_type")
    if period is None:
        return None
    _, rows = data.breakdown("search_filter_use", "filter_type", period=period)
    serp = dict(data.series("search_serp", "monthly")).get(period)
    if not rows or not serp:
        return None
    pct_rows = [[name, round(v / serp * 100.0, 1)] for name, v in rows]
    return {"kind": "barh",
            "title": "Searches using each filter type — share of %s's searches" % period,
            "unit": "%", "rows": pct_rows,
            "note": _noplat("Share of the month's TOTAL keyword searches (event "
                            "counts). Overlapping — one search can use several "
                            "criteria, so shares can sum past 100%.", plat),
            "info": info("search_filter_use")}


def _filter_depth_results_bars(plat=""):
    period = data.latest_full_period("search_filter_avg_results", "monthly",
                                     "filter_depth")
    if period is None:
        return None
    vals = dict(data.breakdown("search_filter_avg_results", "filter_depth",
                               period=period)[1])
    rows = [[d, vals[d]] for d in FILTER_DEPTHS if d in vals]
    if not rows:
        return None
    return {"kind": "barh",
            "title": "Avg results per search (≤1000) by filter depth — " + period,
            "rows": rows,
            "note": _noplat("AVERAGE result count per search over all of the "
                            "month's searches in each bucket — not a total. "
                            "Each narrowing criterion shrinks the result set; "
                            "the app caps result counts at 1000, so the "
                            "unfiltered bar is understated the most.", plat),
            "info": info("search_filter_avg_results")}


def _search_treemap(plat=""):
    period, rows = data.breakdown("search_searches", "search_cat_l1|search_cat",
                                  top_n=60)
    if not rows:
        return None
    tiles = []
    for name, v in rows:
        if data.PAIR_SEP not in name:
            continue
        l1, l2 = name.split(data.PAIR_SEP, 1)
        if l2 == "No category":
            l2 = l1 + " (uncategorized)"
        tiles.append([l1, l2, v])
    if not tiles:
        return None
    return {"kind": "treemap", "title": "Searches by category — " + period,
            "rows": tiles,
            "note": _noplat("True search event totals, latest full month. "
                            "Tile = finance L2, color = finance L1 group.", plat),
            "info": info("search_searches")}


def _search_cat_matrix(plat="", months=13, top_n=12):
    values = data.dim_values("search_searches", "search_cat", top_n=top_n)
    values = [v for v in values if v != "No category"]
    if not values:
        return None
    periods, rows = None, []
    for v in values:
        pts = data.monthly("search_searches", "search_cat", v)[-months:]
        by_p = dict(pts)
        if periods is None:
            periods = [p for p, _ in pts]
        rows.append([v, [by_p.get(p) for p in (periods or [])]])
    if not periods:
        return None
    return {"kind": "matrix", "title": "Searches by category × month",
            "periods": periods, "rows": rows,
            "note": _noplat("True search event totals per finance L2. Fixed "
                            "monthly view.", plat),
            "info": info("search_searches")}


def _search_region_map():
    period, rows = data.breakdown("search_serp", "region", top_n=20)
    if not rows:
        return None
    _, prows = data.breakdown("search_serp", "platform|region",
                              period=period, top_n=200)
    pdata = {}
    for n, v in prows:
        if data.PAIR_SEP in n:
            hp, region = n.split(data.PAIR_SEP, 1)
            pdata.setdefault(hp, []).append([region, v])
    return {"kind": "map", "title": "Keyword SERP views by region — " + period,
            "rows": rows,
            "note": "First-page keyword searches from clickstream (additive "
                    "event counts). Clickstream tracks web as one platform. "
                    "Fixed monthly view.",
            "info": info("search_serp"),
            "pfilter": {"mode": "map", "platforms": sorted(pdata),
                        "data": pdata, "approx": False}}


def _search_zsr_region_bars():
    period = data.latest_full_period("search_zsr", "monthly", "platform|region")
    if period is None:
        return None
    _, zsr_rows = data.breakdown("search_zsr", "platform|region",
                                 period=period, top_n=100)
    _, serp_rows = data.breakdown("search_serp", "platform|region",
                                  period=period, top_n=100)
    serp = dict(serp_rows)
    rows = []
    for name, z in zsr_rows:
        if not name.startswith("Android" + data.PAIR_SEP):
            continue
        s = serp.get(name)
        if s:
            rows.append([name.split(data.PAIR_SEP, 1)[1], round(z / s * 100.0, 1)])
    if not rows:
        return None
    rows.sort(key=lambda r: -r[1])
    return {"kind": "barh", "title": "Zero-result rate by region (Android) — " + period,
            "unit": "%", "rows": rows,
            "note": ZSR_PLATFORM_NOTE, "info": info("search_zsr")}


def _search_kwtable(flag, title, note):
    """One keywords table for a filter slice: 1 = narrowed, 0 = bare query."""
    payload = data.search_keywords()
    rows = []
    for platform, kw, filtered, searches, zsr, low, avg_results in payload["rows"]:
        if filtered != flag:
            continue
        rows.append([kw, platform, searches,
                     round(zsr / searches * 100.0, 1) if searches else None,
                     round(low / searches * 100.0, 1) if searches else None,
                     avg_results])
    if not rows:
        return None
    d1, d2 = payload["window"]
    window = (" (%s → %s)" % (d1, d2)) if d1 and d2 else ""
    return {
        "kind": "kwtable", "title": title + " — 28 days" + window,
        "columns": [
            {"key": "keyword", "label": "Keyword"},
            {"key": "platform", "label": "Platform"},
            {"key": "searches", "label": "Searches", "num": True},
            {"key": "zsr_pct", "label": "ZSR %", "num": True, "pct": True},
            {"key": "low_pct", "label": "Low-supply %", "num": True, "pct": True},
            {"key": "avg_results", "label": "Avg results (≤1000)", "num": True},
        ],
        "rows": rows, "platforms": ["Web", "Android", "iOS"], "wide": True,
        "note": note + " First-page keyword SERPs, bot-filtered; all figures "
                "computed over this slice only. Result counts are CAPPED at "
                "1000 by the app. " + ZSR_PLATFORM_NOTE,
        "info": info("search_keywords_table"),
    }


def _platform_share_area(wk):
    """100%-stacked platform mix of search users, legend shows first → last
    share. Shares are of the SUM of platform values (a user active on two
    platforms counts in both, so this is a mix, not an exact partition)."""
    values = data.dim_values("search_users", "platform", top_n=6)
    if not values:
        return None
    per_value = {v: dict(data.series_at("search_users", wk, "platform", v))
                 for v in values}
    periods = sorted(set().union(*per_value.values()))
    shares = {v: [] for v in values}
    for p in periods:
        tot = sum(per_value[v].get(p, 0) for v in values)
        if not tot:
            continue
        for v in values:
            shares[v].append([p, round(per_value[v].get(p, 0) / tot * 100, 2)])
    series = []
    for v in values:
        pts = shares[v]
        if not pts:
            continue
        series.append({"label": "%s %.0f%% → %.0f%%" % (v, pts[0][1], pts[-1][1]),
                       "points": pts, "_last": pts[-1][1]})
    if not series:
        return None
    series.sort(key=lambda s: -s.pop("_last"))  # largest = bottom layer
    return {"kind": "sharearea", "title": "Platform mix of search users (avg daily)",
            "grain": wk, "series": series, "pct": True,
            "note": "Share of the sum of platform search users per %s; a "
                    "user active on two platforms counts in both."
                    % GRAIN_WORD[wk],
            "info": info("search_users")}


def _method_volume_stacked(grain, methods, plat=""):
    series = [{"label": m, "points": _psl("search_volume", grain, plat, method=m)}
              for m in methods]
    series = [s for s in series if s["points"]]
    if not series:
        return None
    return {"kind": "stacked", "title": "Search volume by method (avg daily)", "unit": None,
            "grain": grain,
            "note": "Avg daily searches; methods are additive (each search "
                    "has exactly one method)." + (" %s only." % plat if plat else ""),
            "series": series, "info": info("search_volume")}


def _search_totals_chart():
    """TRUE search-event totals stacked by platform (additive), the
    counterpart to the avg-daily charts — with a D/M grain toggle."""
    def variant(gg):
        series = _nz([{"label": p,
                       "points": data.series_at("search_searches", gg,
                                                "platform", p)}
                      for p in SEARCH_PLATFORMS])
        return ({"grain": gg, "series": series,
                 "title": "Search totals by %s (event counts)"
                          % GRAIN_WORD[gg]} if series else None)

    base = variant("monthly")
    if base is None:
        return None
    chart = {"kind": "stacked", "title": base["title"], "unit": None,
             "grain": "monthly", "series": base["series"],
             "note": "True totals of search events by platform — unlike the "
                     "avg-daily charts, these grow with the period. Platforms "
                     "stack exactly (each event has one platform). Current "
                     "partial period excluded.",
             "info": info("search_searches")}
    return _with_grains(chart, variant, grains=("daily", "monthly"),
                        default="monthly")


def _serp_totals_table(months=13):
    serp = data.monthly("search_serp")[-months:]
    if not serp:
        return None
    zsr = dict(data.monthly("search_zsr"))
    low = dict(data.monthly("search_zsr_low"))
    rows = [[m, v, zsr.get(m), low.get(m)] for m, v in reversed(serp)]
    return {"kind": "table",
            "title": "Keyword SERP totals by month (clickstream event counts)",
            "columns": [{"label": "Month"},
                        {"label": "SERP views", "num": True},
                        {"label": "Zero results", "num": True},
                        {"label": "Low supply (1–10)", "num": True}],
            "rows": rows,
            "note": "True totals across all platforms. Counts are additive; "
                    "only RATES must never be blended across platforms.",
            "info": info("search_serp")}


GRAINS_DWM = ("daily", "weekly", "monthly")


def _nz(series_list):
    return [s for s in series_list if s["points"]]


def _with_grains(chart, variant_fn, grains=GRAINS_DWM, default="weekly"):
    """Attach per-chart grain variants (client-side D/W/M toggle). Grains
    whose data is missing are dropped; a single-grain chart gets no toggle."""
    if chart is None:
        return None
    gd = {}
    for g in grains:
        v = variant_fn(g)
        if v and (v.get("series") or v.get("cols")):
            gd[g] = v
    if len(gd) < 2:
        return chart
    if default not in gd:
        default = next(iter(gd))
    chart["gfilter"] = {"grains": [g for g in grains if g in gd],
                        "active": default}
    chart["gdata"] = gd
    return chart


def _with_pf(chart, mode, pdata, approx=False):
    """Attach a client-side platform filter payload to a chart spec."""
    if chart is None or not pdata:
        return chart
    if mode in ("lines", "sum_series"):   # nested: {base: {platform: pts}}
        present = set().union(*(d.keys() for d in pdata.values()))
    else:                                 # flat: {platform: pts/cols/rows}
        present = set(pdata)
    platforms = [p for p in SEARCH_PLATFORMS if p in present] \
        or sorted(present)                # hydra map uses Web/Android/iOS
    if len(platforms) < 2:
        return chart
    chart["pfilter"] = {"mode": mode, "platforms": platforms,
                        "data": pdata, "approx": approx}
    return chart


def _section(title):
    return {"kind": "section", "title": title}


def build_search():
    """Search volume tab: everything measured in search EVENTS."""
    g = "weekly"   # each chart carries its own D/W/M toggle; cards are pinned
    methods = ["Keyword", "Browsing"]
    cards = [
        _s_card("Searches / day (events)", data.daily("search_searches"),
                "daily", info_key="search_searches"),
        _s_card("Searches per search user",
                data.ratio_at("search_volume", "search_users", g, as_pct=False),
                g, info_key="search_volume"),
        _s_card("Searches / day (avg, weekly)",
                data.series_at("search_volume", g), g,
                info_key="search_volume"),
    ]

    def vol_variant(gg):
        pts = data.series_at("search_volume", gg)
        return ({"grain": gg,
                 "series": [{"label": "Searches", "points": pts}],
                 "pfdata": _pp("search_volume", gg)} if pts else None)

    def mstack_variant(gg):
        series = _nz([{"label": m,
                       "points": data.series_at("search_volume", gg,
                                                "method", m)}
                      for m in methods])
        return ({"grain": gg, "series": series,
                 "pfdata": {m: _pp("search_volume", gg, method=m)
                            for m in methods}} if series else None)

    def kwb_variant(gg):
        series = _nz([{"label": m,
                       "points": data.series_at("search_searches", gg,
                                                "method", m)}
                      for m in methods])
        return ({"grain": gg, "series": series,
                 "title": "Keyword vs browsing searches (%s event totals)"
                          % GRAIN_WORD[gg],
                 "pfdata": {m: _pp("search_searches", gg, method=m)
                            for m in methods}} if series else None)

    charts = [
        _section("Volume trends"),
        _with_grains(_with_pf(
            _s_line("Search volume (avg daily)",
                    [{"label": "Searches",
                      "points": data.series_at("search_volume", g)}],
                    g, area=True, note=AVG_DAILY_NOTE, info_key="search_volume"),
            "sum", _pp("search_volume", g)), vol_variant),
        _search_totals_chart(),
        _section("Method mix — keyword vs browsing"),
        _with_grains(_with_pf(_method_volume_stacked(g, methods),
                              "sum_series",
                              {m: _pp("search_volume", g, method=m)
                               for m in methods}), mstack_variant),
        _with_grains(_with_pf(
            _s_line("Keyword vs browsing searches (daily event totals)",
                    kwb_variant("daily")["series"],
                    "daily", note="True event totals.",
                    info_key="search_searches"),
            "sum_series",
            {m: _pp("search_searches", "daily", method=m) for m in methods}),
            kwb_variant, grains=("daily", "monthly"), default="daily"),
        _section("How searches are narrowed"),
        _filter_depth_stacked(),
        _filter_type_bars(),
        _filter_depth_results_bars(),
        _section("Categories"),
        _search_treemap(),
        _search_cat_matrix(),
        _section("Regions"),
        _search_region_map(),
    ]
    return {"cards": cards, "charts": charts}


def build_search_users():
    """Search users tab: everything measured in PEOPLE (avg daily users)."""
    g = "weekly"
    methods = ["Keyword", "Browsing"]
    cards = [
        _s_card("Search users / day", data.series_at("search_users", g), g,
                info_key="search_users"),
        _s_card("Search → ad view", data.series_at("search_ssu_adview", g), g,
                fmt="pct", info_key="search_ssu_adview"),
        _s_card("Search → reply", data.series_at("search_ssu_lead", g), g,
                fmt="pct", info_key="search_ssu_lead"),
    ]

    platforms = data.dim_values("search_users", "platform", top_n=4)

    def mekko_variant(gg):
        cols = _mekko_cols(gg)
        if len(cols) < 2:
            return None
        word = GRAIN_WORD[gg]
        return {"cols": cols, "grain": gg,
                "title": "Search funnel by %s (avg daily users) — last %d %ss"
                         % (word, len(cols), word),
                "pfdata": {p: c for p in SEARCH_PLATFORMS
                           if (c := _mekko_cols(gg, "platform", p))}}

    def conv_variant(gg):
        series = _nz([{"label": "Search → ad view",
                       "points": data.series_at("search_ssu_adview", gg)},
                      {"label": "Search → reply",
                       "points": data.series_at("search_ssu_lead", gg)}])
        return {"grain": gg, "series": series,
                "pfdata": {"Search → ad view": _pp("search_ssu_adview", gg),
                           "Search → reply": _pp("search_ssu_lead", gg)}} \
            if series else None

    def users_variant(gg):
        pp = _pp("search_users", gg)
        series = [{"label": p, "points": pp[p]} for p in platforms if p in pp]
        return {"grain": gg, "series": series, "pfdata": {"": pp}} \
            if series else None

    def mix_variant(gg):
        ch = _platform_share_area(gg)
        return ({"grain": gg, "series": ch["series"],
                 "pfdata": _pp("search_users", gg)} if ch else None)

    def mshare_variant(gg):
        series = _nz([{"label": m,
                       "points": data.series_at("search_share_on_platform",
                                                gg, "method", m)}
                      for m in methods])
        return ({"grain": gg, "series": series,
                 "pfdata": {m: _pp("search_share_on_platform", gg, method=m)
                            for m in methods}} if series else None)

    users_pp = _pp("search_users", g)
    charts = [
        _section("Funnel"),
        _with_grains(_search_funnel_marimekko(g), mekko_variant),
        _with_grains(_with_pf(
            _s_line("Funnel conversion trend (avg of daily ratios)",
                    conv_variant(g)["series"],
                    g, pct=True, info_key="search_ssu_adview",
                    note="Unweighted average of daily ratios."),
            "lines", conv_variant(g)["pfdata"]), conv_variant),
        _section("Platforms"),
        _with_grains(_with_pf(
            _s_line("Search users by platform (avg daily)",
                    users_variant(g)["series"],
                    g, note=AVG_DAILY_NOTE, info_key="search_users"),
            "lines", {"": users_pp}), users_variant),
        _with_grains(_with_pf(_platform_share_area(g), "mix", users_pp),
                     mix_variant),
        _section("Method mix"),
        _with_grains(_with_pf(
            _s_line("Method share of platform searchers (avg of daily shares)",
                    mshare_variant(g)["series"],
                    g, pct=True, info_key="search_share_on_platform",
                    note="Users can use both methods in a day, so shares can "
                         "sum past 100%."),
            "lines", mshare_variant(g)["pfdata"]), mshare_variant),
    ]
    return {"cards": cards, "charts": charts}


def _zsr_low_region_bars():
    period = data.latest_full_period("search_zsr_low", "monthly",
                                     "platform|region")
    if period is None:
        return None
    _, low_rows = data.breakdown("search_zsr_low", "platform|region",
                                 period=period, top_n=100)
    _, serp_rows = data.breakdown("search_serp", "platform|region",
                                  period=period, top_n=100)
    serp = dict(serp_rows)
    rows = []
    for name, z in low_rows:
        if not name.startswith("Android" + data.PAIR_SEP):
            continue
        s = serp.get(name)
        if s:
            rows.append([name.split(data.PAIR_SEP, 1)[1],
                         round(z / s * 100.0, 1)])
    if not rows:
        return None
    rows.sort(key=lambda r: -r[1])
    return {"kind": "barh",
            "title": "Low-supply rate by region (Android) — " + period,
            "unit": "%", "rows": rows,
            "note": "Share of keyword searches returning 1–10 results.",
            "info": info("search_zsr_low")}


def _zsr_category_bars(metric, title, info_key):
    period = data.latest_full_period(metric, "monthly", "platform|zsr_category")
    if period is None:
        return None
    _, num_rows = data.breakdown(metric, "platform|zsr_category",
                                 period=period, top_n=200)
    _, serp_rows = data.breakdown("search_serp", "platform|zsr_category",
                                  period=period, top_n=200)
    serp = dict(serp_rows)
    rows = []
    for name, z in num_rows:
        if not name.startswith("Android" + data.PAIR_SEP):
            continue
        s = serp.get(name)
        if s and s > 1000:   # skip noise categories
            cat = name.split(data.PAIR_SEP, 1)[1]
            if cat == "unknown":
                cat = "No category selected"
            rows.append([cat, round(z / s * 100.0, 1)])
    if not rows:
        return None
    rows.sort(key=lambda r: -r[1])
    return {"kind": "barh", "title": title + " — " + period,
            "unit": "%", "rows": rows[:14],
            "note": "Android keyword searches, by the category the search "
                    "was made in (category L1).",
            "info": info(info_key)}


def _zsr_top_keywords_table(top_n=15):
    """Top Android keywords by zero-result searches, both filter slices
    combined — derived from the keywords store, no extra query."""
    payload = data.search_keywords()
    agg = {}
    for platform, kw, _f, searches, zsr, low, _avg in payload["rows"]:
        if platform != "Android":
            continue
        a = agg.setdefault(kw, [0, 0, 0])
        a[0] += searches
        a[1] += zsr
        a[2] += low
    rows = sorted(([kw, s, z, round(z / s * 100.0, 1) if s else None, lo]
                   for kw, (s, z, lo) in agg.items() if z),
                  key=lambda r: -r[2])[:top_n]
    if not rows:
        return None
    d1, d2 = payload["window"]
    window = (" (%s → %s)" % (d1, d2)) if d1 and d2 else ""
    return {"kind": "table",
            "title": "Top zero-result keywords (Android) — 28 days" + window,
            "columns": [{"label": "Keyword"},
                        {"label": "Searches", "num": True},
                        {"label": "Zero results", "num": True},
                        {"label": "ZSR %", "num": True},
                        {"label": "Low supply", "num": True}],
            "rows": rows,
            "note": "Ranked by zero-result search count; filtered and bare "
                    "searches combined. These are the supply gaps users hit "
                    "most often.",
            "info": info("search_zsr")}


def build_search_zsr():
    android_zsr = _slice_ratio("search_zsr", "search_serp", "monthly",
                               "platform", "Android")
    android_low = _slice_ratio("search_zsr_low", "search_serp", "monthly",
                               "platform", "Android")
    zsr_count = data.series_at("search_zsr", "monthly", "platform", "Android")
    low_count = data.series_at("search_zsr_low", "monthly", "platform",
                               "Android")
    cards = [
        _s_card("Zero-result rate", android_zsr, "monthly", fmt="pct",
                badge="Android only", info_key="search_zsr"),
        _s_card("Low-supply rate (1–10)", android_low, "monthly", fmt="pct",
                badge="Android only", info_key="search_zsr_low"),
        _s_card("Zero-result searches / month", zsr_count, "monthly",
                badge="Android", info_key="search_zsr"),
        _s_card("Low-supply searches / month", low_count, "monthly",
                badge="Android", info_key="search_zsr_low"),
    ]
    def zsr_trend_variant(gg):
        series = _nz([{"label": "Zero results", "points": _slice_ratio(
                          "search_zsr", "search_serp", gg, "platform",
                          "Android")},
                      {"label": "Low supply (1–10)", "points": _slice_ratio(
                          "search_zsr_low", "search_serp", gg, "platform",
                          "Android")}])
        return {"grain": gg, "series": series} if series else None

    def low_plat_variant(gg):
        series = _nz([{"label": p, "points": _slice_ratio(
                          "search_zsr_low", "search_serp", gg, "platform", p)}
                      for p in ("Android", "iOS", "Web")])
        return {"grain": gg, "series": series} if series else None

    charts = [
        _with_grains(
            _s_line("Zero-result & low-supply rate (Android)",
                    zsr_trend_variant("daily")["series"],
                    "daily", pct=True, info_key="search_zsr",
                    note="Share of Android keyword searches returning zero / "
                         "1–10 results. " + ZSR_PLATFORM_NOTE),
            zsr_trend_variant, grains=("daily", "monthly"), default="daily"),
        _search_zsr_region_bars(),
        _zsr_low_region_bars(),
        _zsr_category_bars("search_zsr",
                           "Zero-result rate by category (Android)",
                           "search_zsr"),
        _zsr_category_bars("search_zsr_low",
                           "Low-supply rate by category (Android)",
                           "search_zsr_low"),
        _with_grains(
            _s_line("Low-supply rate by platform",
                    low_plat_variant("daily")["series"],
                    "daily", pct=True, info_key="search_zsr_low",
                    note="Unlike hard zeros, the 1–10 results band is "
                         "meaningful on every platform (the auto-extend "
                         "fallback only kicks in on empty results)."),
            low_plat_variant, grains=("daily", "monthly"), default="daily"),
        _zsr_top_keywords_table(),
        _serp_totals_table(),
    ]
    return {"cards": cards, "charts": charts}


CTR_NOTE = ("CTR@N = ad clicks on positions ≤ N per 100 result-page views — "
            "an event-level rate (several clicks from one page all count), "
            "not a per-session deduplicated rate. Position 40 ≈ one page.")


def _ctr_ratio(bucket, mode, grain="monthly"):
    return _slice_ratio("search_ctr_clicks_" + bucket, "search_ctr_serps",
                        grain, "search_mode", mode)


def _ctr_trend(mode):
    def variant(gg):
        series = _nz([{"label": "CTR@" + b[1:],
                       "points": _ctr_ratio(b, mode, gg)}
                      for b in ("p1", "p3", "p40")])
        return {"grain": gg, "series": series} if series else None

    base = variant("monthly")
    if base is None:
        return None
    return _with_grains(
        _s_line("CTR trend — %s" % mode.lower(), base["series"],
                "monthly", pct=True, info_key="search_ctr", note=CTR_NOTE),
        variant, grains=("daily", "monthly"), default="monthly")


def _ctr_platform_bars(mode):
    period = data.latest_full_period("search_ctr_clicks_p40", "monthly",
                                     "platform|search_mode")
    if period is None:
        return None
    _, clicks = data.breakdown("search_ctr_clicks_p40", "platform|search_mode",
                               period=period, top_n=50)
    _, serps = data.breakdown("search_ctr_serps", "platform|search_mode",
                              period=period, top_n=50)
    serp = dict(serps)
    rows = []
    for name, c in clicks:
        if not name.endswith(data.PAIR_SEP + mode):
            continue
        s = serp.get(name)
        if s:
            rows.append([name.split(data.PAIR_SEP, 1)[0],
                         round(c / s * 100.0, 1)])
    if not rows:
        return None
    rows.sort(key=lambda r: -r[1])
    return {"kind": "barh",
            "title": "CTR@40 by platform — %s, %s" % (mode.lower(), period),
            "unit": "%", "rows": rows, "note": CTR_NOTE,
            "info": info("search_ctr")}


def _ctr_totals_table(months=13):
    serps_s = dict(data.series("search_ctr_serps", "monthly",
                               "search_mode", "Search"))
    serps_n = dict(data.series("search_ctr_serps", "monthly",
                               "search_mode", "Navigation"))
    clicks_s = dict(data.series("search_ctr_clicks", "monthly",
                                "search_mode", "Search"))
    clicks_n = dict(data.series("search_ctr_clicks", "monthly",
                                "search_mode", "Navigation"))
    cutoff = data.current_period_key("monthly")
    months_list = sorted((set(serps_s) | set(serps_n)) - {cutoff})[-months:]
    if not months_list:
        return None
    rows = [[m, serps_s.get(m), clicks_s.get(m), serps_n.get(m),
             clicks_n.get(m)] for m in reversed(months_list)]
    return {"kind": "table",
            "title": "Result-page views & ad clicks by month (event counts)",
            "columns": [{"label": "Month"},
                        {"label": "Search SERPs", "num": True},
                        {"label": "Search clicks", "num": True},
                        {"label": "Navigation pages", "num": True},
                        {"label": "Navigation clicks", "num": True}],
            "rows": rows,
            "note": "True totals across all platforms. Search = typed "
                    "keyword; Navigation = category browsing without one.",
            "info": info("search_ctr")}


def build_search_ctr():
    cards = [
        _s_card("CTR@1 — search", _ctr_ratio("p1", "Search"), "monthly",
                fmt="pct", info_key="search_ctr"),
        _s_card("CTR@3 — search", _ctr_ratio("p3", "Search"), "monthly",
                fmt="pct", info_key="search_ctr"),
        _s_card("CTR@40 — search", _ctr_ratio("p40", "Search"), "monthly",
                fmt="pct", info_key="search_ctr"),
        _s_card("CTR@40 — navigation", _ctr_ratio("p40", "Navigation"),
                "monthly", fmt="pct", info_key="search_ctr"),
    ]
    charts = [
        _ctr_trend("Search"),
        _ctr_trend("Navigation"),
        _ctr_platform_bars("Search"),
        _ctr_platform_bars("Navigation"),
        _ctr_totals_table(),
    ]
    return {"cards": cards, "charts": charts}


def build_search_keywords():
    return {"cards": [], "charts": [
        _search_kwtable(
            1, "Top keywords — searches with filters",
            "Searches narrowed by any criterion (category, region, price, "
            "attribute filters); ranked by this slice's own volume."),
        _search_kwtable(
            0, "Top keywords — bare queries (no filters)",
            "Searches with no narrowing at all — the query exactly as typed."),
    ]}


BUILDERS = {
    "overview": build_overview,
    "listings": build_listings,
    "engagement": build_engagement,
    "liquidity": build_liquidity,
    "monetization": build_monetization,
    "users": build_users,
    "search": build_search,
    "search_users_tab": build_search_users,
    "search_zsr": build_search_zsr,
    "search_ctr": build_search_ctr,
    "search_keywords": build_search_keywords,
}


GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
OAUTH_REDIRECT_URI = "https://dash.209-38-224-32.sslip.io/auth/callback"
ALLOWED_DOMAIN = "tteam.pro"
ALLOWED_EXTRA = {"mshcheglov1@gmail.com"}

# central auth gate: Caddy forward_auth sends /gate/verify here for every
# protected path on the bare domain; a signed cookie scoped to the PARENT
# domain lets one Google login cover all sslip sites.
GATE_COOKIE = "tteam_gate"
GATE_DOMAIN = ".209-38-224-32.sslip.io"
GATE_MAX_AGE = 30 * 86400
ALLOWED_NEXT_HOSTS = {"209-38-224-32.sslip.io", "dash.209-38-224-32.sslip.io"}


def _safe_next(nxt):
    if nxt.startswith("/") and not nxt.startswith("//"):
        return nxt
    try:
        u = urllib.parse.urlsplit(nxt)
        if u.scheme == "https" and u.netloc in ALLOWED_NEXT_HOSTS:
            return nxt
    except ValueError:
        pass
    return "/"


def create_app():
    app = Flask(__name__)
    app.secret_key = os.environ.get("SECRET_KEY", os.urandom(32))
    app.config["SESSION_COOKIE_SECURE"] = True
    gate_signer = URLSafeTimedSerializer(app.secret_key, salt="tteam-gate")
    password_hash = os.environ.get("KPI_PASSWORD_HASH")  # optional break-glass
    google_client_id = os.environ.get("GOOGLE_LOGIN_CLIENT_ID")
    google_client_secret = os.environ.get("GOOGLE_LOGIN_CLIENT_SECRET")
    if not (google_client_id or password_hash):
        app.logger.warning("no auth configured — using dev password 'dev'")
        password_hash = generate_password_hash("dev", method="pbkdf2:sha256")

    # pre-warm the mtime-keyed caches so the first page hit is fast
    try:
        if data.available():
            data.filter_options()
            data.category_tree()
    except Exception:
        app.logger.exception("cache pre-warm failed (non-fatal)")

    @app.before_request
    def require_login():
        if request.endpoint in ("login", "health", "static",
                                "auth_start", "auth_callback", "gate_verify"):
            return None
        if not session.get("authed"):
            return redirect(url_for("login", next=request.path))
        return None

    @app.route("/login", methods=["GET", "POST"])
    def login():
        error = None
        if request.method == "POST":
            if password_hash and check_password_hash(
                    password_hash, request.form.get("password", "")):
                session["authed"] = True
                session.permanent = True
                return redirect(request.args.get("next") or "/")
            time.sleep(1)  # slow down brute force
            error = "Wrong password"
        return render_template(
            "login.html", error=error,
            google=bool(google_client_id),
            show_password=bool(password_hash),
            next=request.args.get("next", "/"))

    @app.route("/auth/login")
    def auth_start():
        state = secrets.token_urlsafe(24)
        session["oauth_state"] = state
        session["oauth_next"] = _safe_next(request.args.get("next", "/"))
        params = {
            "client_id": google_client_id,
            "redirect_uri": OAUTH_REDIRECT_URI,
            "response_type": "code",
            "scope": "openid email",
            "state": state,
            "hd": ALLOWED_DOMAIN,  # hint only; enforced below
            "prompt": "select_account",
        }
        return redirect(GOOGLE_AUTH_URL + "?" + urllib.parse.urlencode(params))

    @app.route("/auth/callback")
    def auth_callback():
        if request.args.get("state") != session.pop("oauth_state", None):
            return render_template("login.html", error="Login expired — try again.",
                                   google=True, show_password=bool(password_hash),
                                   next="/"), 403
        code = request.args.get("code")
        if not code:
            return redirect(url_for("login"))
        body = urllib.parse.urlencode({
            "code": code,
            "client_id": google_client_id,
            "client_secret": google_client_secret,
            "redirect_uri": OAUTH_REDIRECT_URI,
            "grant_type": "authorization_code",
        }).encode()
        try:
            with urllib.request.urlopen(
                    urllib.request.Request(GOOGLE_TOKEN_URL, data=body), timeout=15) as r:
                tok = json.loads(r.read())
            payload = tok["id_token"].split(".")[1]
            claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        except Exception:
            app.logger.exception("google token exchange failed")
            return render_template("login.html", error="Google sign-in failed — try again.",
                                   google=True, show_password=bool(password_hash),
                                   next="/"), 502
        email = (claims.get("email") or "").lower()
        ok = claims.get("email_verified") and (
            email.endswith("@" + ALLOWED_DOMAIN) or email in ALLOWED_EXTRA)
        if not ok:
            return render_template(
                "login.html", error=f"Access is limited to @{ALLOWED_DOMAIN} accounts.",
                google=True, show_password=bool(password_hash), next="/"), 403
        session["authed"] = True
        session["email"] = email
        session.permanent = True
        resp = redirect(session.pop("oauth_next", "/"))
        resp.set_cookie(GATE_COOKIE, gate_signer.dumps(email),
                        max_age=GATE_MAX_AGE, domain=GATE_DOMAIN,
                        secure=True, httponly=True, samesite="Lax")
        return resp

    @app.route("/gate/verify")
    def gate_verify():
        tok = request.cookies.get(GATE_COOKIE, "")
        if tok:
            try:
                email = gate_signer.loads(tok, max_age=GATE_MAX_AGE)
                resp = make_response("", 204)
                resp.headers["X-Gate-Email"] = email
                return resp
            except Exception:
                pass
        host = request.headers.get("X-Forwarded-Host", "209-38-224-32.sslip.io")
        uri = request.headers.get("X-Forwarded-Uri", "/")
        target = "https://" + host + uri
        return redirect("https://dash.209-38-224-32.sslip.io/auth/login?"
                        + urllib.parse.urlencode({"next": target}))

    @app.route("/logout")
    def logout():
        session.clear()
        resp = redirect(url_for("login"))
        resp.delete_cookie(GATE_COOKIE, domain=GATE_DOMAIN)
        return resp

    @app.route("/health")
    def health():
        if not data.available():
            return jsonify({"status": "no-snapshot"}), 503
        built, age_h, level = data.freshness()
        return jsonify({"status": "ok", "built_at_utc": built,
                        "age_hours": age_h, "freshness": level})

    @app.route("/")
    def landing():
        entries = []
        for d in DASHBOARDS:
            e = {k: d[k] for k in ("slug", "title", "description",
                                   "requested_by", "url")}
            e["updated"] = ""
            if data.available():
                built, _, _ = data.freshness()
                e["updated"] = (built or "")[:10]
            entries.append(e)
        return render_template("landing.html", dashboards=entries,
                               request_url=JIRA_REQUEST_URL)

    @app.route("/player")
    def player():
        if not data.available():
            return render_template("nodata.html", tabs=TABS, active="player",
                                   dash_title=KPI_DASH_TITLE), 503
        built, age_h, level = data.freshness()
        metric_options = [
            {"key": k, "label": lbl, "grains": sorted(grains)}
            for k, lbl, grains in PLAYER_METRICS
        ]
        return render_template(
            "player.html", tabs=TABS, active="player",
            built_at=built, age_hours=age_h, freshness=level,
            metric_options=metric_options, player_dims=PLAYER_DIMS,
            dash_title=KPI_DASH_TITLE,
        )

    @app.route("/player/data")
    def player_data():
        key = request.args.get("metric", "")
        grain = request.args.get("grain", "monthly")
        dim = request.args.get("dim", "finance_l1")
        grains = PLAYER_METRIC_MAP.get(key)
        if not grains or grain not in ("monthly", "weekly", "daily") or grain not in grains \
                or dim not in dict(PLAYER_DIMS):
            return jsonify({"error": "bad params"}), 400
        payload = data.player_payload(grains[grain], grain, dim)
        if payload is None:
            return jsonify({"error": "no data for this combination"}), 404
        return jsonify(payload)

    @app.route("/dictionary")
    def dictionary():
        built, age_h, level = data.freshness() if data.available() else (None, None, "missing")
        entries = sorted(
            ({"metric": k, **v} for k, v in METRIC_DEFS.items()
             if not k.startswith("search_")),   # search metrics live on /search/definitions
            key=lambda e: e["label"].lower(),
        )
        return render_template(
            "dictionary.html", tabs=TABS, active="dictionary", entries=entries,
            built_at=built, age_hours=age_h, freshness=level,
            dash_title=KPI_DASH_TITLE,
        )

    @app.route("/search/definitions")
    def search_definitions():
        built, age_h, level = data.freshness() if data.available() else (None, None, "missing")
        entries = sorted(
            ({"metric": k, **v} for k, v in METRIC_DEFS.items()
             if k.startswith("search_")),
            key=lambda e: e["label"].lower(),
        )
        return render_template(
            "dictionary.html", tabs=SEARCH_TABS, active="search_definitions",
            entries=entries, built_at=built, age_hours=age_h, freshness=level,
            dash_title=SEARCH_DASH_TITLE, dict_title="Search metric definitions",
            dict_intro="Every metric on the Search dashboard, its definition and "
                       "lineage. Two sources with different definitions of a "
                       "'search' feed this page — see the Methodology tab for "
                       "how they are built and why their volumes are never "
                       "compared directly.",
        )

    @app.route("/search/methodology")
    def search_methodology():
        built, age_h, level = data.freshness() if data.available() else (None, None, "missing")
        m = data.meta() if data.available() else {}
        extracted = {k: (m.get(k) or {}).get("extracted_at_utc")
                     for k in ("search_trino", "search_hydra")}
        return render_template(
            "search_methodology.html", tabs=SEARCH_TABS,
            active="search_methodology", built_at=built, age_hours=age_h,
            freshness=level, dash_title=SEARCH_DASH_TITLE, extracted=extracted,
        )

    def render_tab(tab_id):
        if not data.available():
            return render_template("nodata.html", tabs=TABS_BY_TAB[tab_id],
                                   active=tab_id,
                                   dash_title=DASH_TITLE_BY_TAB[tab_id]), 503
        g.page_metrics = set()
        page = BUILDERS[tab_id]()
        page["charts"] = [c for c in page["charts"] if c]
        built, age_h, level = data.freshness()

        page_dims = data.dims_for_metrics(g.page_metrics)
        selected = raw_selected()
        base_parts = ["%s=%s" % (quote(d), quote(v))
                      for d, vals in selected.items() for v in vals]
        grain = current_grain()
        grain_param = GRAIN_PARAM_INV[grain]
        qs_parts = base_parts + (
            ["grain=%s" % grain_param] if grain != "monthly" else [])
        filter_qs = ("?" + "&".join(qs_parts)) if qs_parts else ""
        grain_urls = {}
        for gp in ("day", "week", "month"):
            parts = base_parts + (["grain=%s" % gp] if gp != "month" else [])
            grain_urls[gp] = request.path + (("?" + "&".join(parts)) if parts else "")
        n_active = sum(len(v) for v in selected.values())

        # explain the ≈ marker once per page when any series/card carries it
        has_approx = any("≈" in (c.get("badge") or "") for c in page["cards"]) or any(
            "≈" in s.get("label", "")
            for chart in page["charts"] if chart.get("series")
            for s in chart["series"]
        )
        filter_note = None
        if has_approx:
            filter_note = ("≈ marks metrics summed across several slices: counts of "
                           "unique users or sessions can double-count someone active "
                           "in more than one selected slice.")

        return render_template(
            "tab.html", tabs=TABS_BY_TAB[tab_id], active=tab_id, page=page,
            dash_title=DASH_TITLE_BY_TAB[tab_id],
            built_at=built, age_hours=age_h, freshness=level,
            meta=data.meta(), dim_labels=DIM_LABELS,
            filter_options=data.filter_options(),
            category_tree=data.category_tree(),
            page_dims=page_dims, selected=selected,
            n_active_filters=n_active, filter_qs=filter_qs,
            grain_param=grain_param, grain_urls=grain_urls,
            filter_note=filter_note,
        )

    for dash in DASHBOARDS:
        for tab_id, path, _label in dash["tabs"]:
            if tab_id in HAND_ROUTED:
                continue
            app.add_url_rule(path, tab_id, (lambda t=tab_id: render_tab(t)))

    return app


app = create_app()

if __name__ == "__main__":
    app.run(debug=True, port=5050)
