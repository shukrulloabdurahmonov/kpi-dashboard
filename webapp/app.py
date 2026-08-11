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

TABS = [
    ("overview", "/", "Overview"),
    ("listings", "/listings", "Listings"),
    ("engagement", "/engagement", "Engagement"),
    ("liquidity", "/liquidity", "Liquidity"),
    ("monetization", "/monetization", "Monetization"),
    ("users", "/users", "Users"),
    ("player", "/player", "Timeline Player"),
    ("dictionary", "/dictionary", "Dictionary"),
]

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


BUILDERS = {
    "overview": build_overview,
    "listings": build_listings,
    "engagement": build_engagement,
    "liquidity": build_liquidity,
    "monetization": build_monetization,
    "users": build_users,
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

    @app.route("/player")
    def player():
        if not data.available():
            return render_template("nodata.html", tabs=TABS, active="player"), 503
        built, age_h, level = data.freshness()
        metric_options = [
            {"key": k, "label": lbl, "grains": sorted(grains)}
            for k, lbl, grains in PLAYER_METRICS
        ]
        return render_template(
            "player.html", tabs=TABS, active="player",
            built_at=built, age_hours=age_h, freshness=level,
            metric_options=metric_options, player_dims=PLAYER_DIMS,
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
            ({"metric": k, **v} for k, v in METRIC_DEFS.items()),
            key=lambda e: e["label"].lower(),
        )
        return render_template(
            "dictionary.html", tabs=TABS, active="dictionary", entries=entries,
            built_at=built, age_hours=age_h, freshness=level,
        )

    def render_tab(tab_id):
        if not data.available():
            return render_template("nodata.html", tabs=TABS, active=tab_id), 503
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
            "tab.html", tabs=TABS, active=tab_id, page=page,
            built_at=built, age_hours=age_h, freshness=level,
            meta=data.meta(), dim_labels=DIM_LABELS,
            filter_options=data.filter_options(),
            category_tree=data.category_tree(),
            page_dims=page_dims, selected=selected,
            n_active_filters=n_active, filter_qs=filter_qs,
            grain_param=grain_param, grain_urls=grain_urls,
            filter_note=filter_note,
        )

    for tab_id, path, _label in TABS:
        if tab_id in ("dictionary", "player"):
            continue
        app.add_url_rule(path, tab_id, (lambda t=tab_id: render_tab(t)))

    return app


app = create_app()

if __name__ == "__main__":
    app.run(debug=True, port=5050)
