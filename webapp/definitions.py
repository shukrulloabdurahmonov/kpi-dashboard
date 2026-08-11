"""Metric definitions — single source for the data dictionary page and the
per-card / per-chart info (ⓘ) popovers.

Each entry: label, definition (plain language), formula (how it is computed),
source (warehouse tables), grain, dims (filterable slices), caveats.
"""

CAT_DIMS = "finance L1/L2, category L1–L4"
FULL_DIMS = CAT_DIMS + ", region, seller type"

METRIC_DEFS = {
    "nnl": {
        "label": "Net new listings (NNL)",
        "definition": "Listings that became active for the first time in the period, "
                      "counting only true net-new listings (renewals and reposts excluded).",
        "formula": "COUNT(DISTINCT listing_sk) WHERE listing_net_sk='net', by first_active_date",
        "source": "eu_bi.fact_listings",
        "grain": "monthly (24m) + daily (90d, total only)",
        "dims": FULL_DIMS,
        "caveats": "The 'net' flag is the official NNL definition — see GNL for the gross count.",
    },
    "gnl": {
        "label": "Gross new listings (GNL)",
        "definition": "All listings posted in the period, including renewals and reposts "
                      "(the NNL 'net' filter switched off).",
        "formula": "COUNT(DISTINCT listing_sk), by COALESCE(first_active_date, date_posted)",
        "source": "eu_bi.fact_listings",
        "grain": "monthly (24m)",
        "dims": FULL_DIMS,
        "caveats": "",
    },
    "active_listings": {
        "label": "Active listings",
        "definition": "Distinct listings that were live at any point during the month.",
        "formula": "COUNT(DISTINCT listing_sk) over daily active-listing snapshots, is_active=1",
        "source": "eu_bi.fact_active_listings + fact_listings",
        "grain": "monthly (24m)",
        "dims": FULL_DIMS,
        "caveats": "Deduplicated within the month — not an end-of-month stock figure.",
    },
    "active_listers": {
        "label": "Active listers",
        "definition": "Distinct users who had at least one live listing during the month.",
        "formula": "COUNT(DISTINCT user_sk) over daily active-listing snapshots",
        "source": "eu_bi.fact_active_listings + fact_listings",
        "grain": "monthly (24m)",
        "dims": FULL_DIMS,
        "caveats": "",
    },
    "insertions_all": {
        "label": "Insertions (all)",
        "definition": "Every insertion event — new posts and renewals — excluding removed ones.",
        "formula": "COUNT(listing_sk) insertion events, is_removed=0",
        "source": "eu_bi.fact_listings_insertions",
        "grain": "monthly (24m)",
        "dims": FULL_DIMS,
        "caveats": "Event-level: one listing can insert several times per month.",
    },
    "insertions_renewed": {
        "label": "Renewed insertions",
        "definition": "Insertion events that were renewals of an existing listing.",
        "formula": "SUM(is_renewal), is_removed=0",
        "source": "eu_bi.fact_listings_insertions",
        "grain": "monthly (24m)",
        "dims": FULL_DIMS,
        "caveats": "",
    },
    "insertions_free": {
        "label": "Free insertions",
        "definition": "Insertion events that used a free quota rather than a paid product.",
        "formula": "SUM(is_free), is_removed=0",
        "source": "eu_bi.fact_listings_insertions",
        "grain": "monthly (24m)",
        "dims": FULL_DIMS,
        "caveats": "",
    },
    "unique_listers": {
        "label": "Unique listers (MUL)",
        "definition": "Distinct users who posted at least one net-new listing in the period.",
        "formula": "COUNT(DISTINCT user_sk) WHERE listing_net_sk='net'",
        "source": "eu_bi.fact_listings",
        "grain": "monthly (24m) + daily (90d, total only)",
        "dims": FULL_DIMS,
        "caveats": "",
    },
    "mau": {
        "label": "Monthly active users (MAU)",
        "definition": "Distinct visitors active on the platform during the month — "
                      "new methodology (April 2026), based on qualified sessions.",
        "formula": "COUNT(DISTINCT session_long_sk) WHERE applicable_to_active_users AND NOT is_outlier",
        "source": "eu_bi.fact_audience_categories",
        "grain": "monthly (24m)",
        "dims": CAT_DIMS + " (no region — source has no geography)",
        "caveats": "Not comparable with the pre-2026 user_sk-based MAU.",
    },
    "wau": {
        "label": "Weekly active users (WAU)",
        "definition": "Distinct visitors active during the week (Mon-Sun), "
                      "new methodology — same definition as MAU at week grain.",
        "formula": "COUNT(DISTINCT session_long_sk) per week, same filters as MAU",
        "source": "eu_bi.fact_audience_categories",
        "grain": "weekly (26w)",
        "dims": CAT_DIMS,
        "caveats": "",
    },
    "dau": {
        "label": "Daily active users (DAU)",
        "definition": "Distinct visitors active on a given day (new methodology).",
        "formula": "COUNT(DISTINCT session_long_sk) per day, same filters as MAU",
        "source": "eu_bi.fact_audience_categories",
        "grain": "daily (90d)",
        "dims": "total only",
        "caveats": "",
    },
    "pageviews": {
        "label": "Pageviews",
        "definition": "Total pages viewed (crawler traffic excluded).",
        "formula": "SUM(num_pageviews) WHERE crawler_type IS NULL",
        "source": "eu_bi.fact_audience_categories",
        "grain": "monthly (24m)",
        "dims": "total + " + CAT_DIMS,
        "caveats": "",
    },
    "bounces": {
        "label": "Bounces",
        "definition": "Sessions that saw exactly one page in the whole visit.",
        "formula": "COUNT(DISTINCT session_eq1_pvs_sess)",
        "source": "eu_bi.fact_audience_categories",
        "grain": "monthly (24m)",
        "dims": "total only (whole-session metric)",
        "caveats": "Different definition from per-category bounces.",
    },
    "visits": {
        "label": "Visits (per category)",
        "definition": "Sessions that viewed more than one page within a category.",
        "formula": "COUNT(DISTINCT session_mt1_pvs_cat)",
        "source": "eu_bi.fact_audience_categories",
        "grain": "monthly (24m)",
        "dims": CAT_DIMS + " (category-scoped metric, no site total)",
        "caveats": "Only meaningful per category — one session can visit several categories.",
    },
    "bounces_per_category": {
        "label": "Bounces per category",
        "definition": "Sessions that saw exactly one page within a category.",
        "formula": "COUNT(DISTINCT session_eq1_pvs_cat)",
        "source": "eu_bi.fact_audience_categories",
        "grain": "monthly (24m)",
        "dims": CAT_DIMS,
        "caveats": "",
    },
    "entering_visits": {
        "label": "Entering visits",
        "definition": "Category visits minus category bounces.",
        "formula": "visits − bounces_per_category",
        "source": "eu_bi.fact_audience_categories",
        "grain": "monthly (24m)",
        "dims": CAT_DIMS,
        "caveats": "",
    },
    "ad_impressions": {
        "label": "Ad impressions",
        "definition": "Times listings were shown in list/search results.",
        "formula": "SUM(num_impressions)",
        "source": "eu_bi.fact_listings_traffic_agg",
        "grain": "monthly (24m)",
        "dims": "total only (source has no category/geography)",
        "caveats": "",
    },
    "ad_views": {
        "label": "Ad views",
        "definition": "Listing detail-page opens.",
        "formula": "SUM(num_ad_page)",
        "source": "eu_bi.fact_listings_traffic_agg",
        "grain": "monthly (24m)",
        "dims": "total only",
        "caveats": "",
    },
    "liquid_listings_7d_1r": {
        "label": "Liquid listings 7d (1 reply)",
        "definition": "Net-new listings that received at least 1 reply within 7 days of posting.",
        "formula": "COUNT(listing) WHERE num_replies_wk > 0, by posting cohort",
        "source": "eu_bi.fact_listings_liquidity_success + fact_listings",
        "grain": "monthly cohorts (24m) + daily cohorts (90d)",
        "dims": "finance L1/L2, category L1/L2, region",
        "caveats": "Cohorts appear only once their 28-day window has closed (monthly) / 7-day (daily).",
    },
    "liquid_listers_7d_1r": {
        "label": "Liquid listers 7d (1 reply)",
        "definition": "Distinct listers whose new listing got ≥1 reply within 7 days.",
        "formula": "COUNT(DISTINCT user_sk) WHERE num_replies_wk > 0",
        "source": "eu_bi.fact_listings_liquidity_success + fact_listings",
        "grain": "monthly cohorts",
        "dims": "finance L1/L2, category L1/L2, region",
        "caveats": "",
    },
    "liquid_listings_7d_3r": {
        "label": "Liquid listings 7d (3 replies)",
        "definition": "Net-new listings with at least 3 replies within 7 days.",
        "formula": "COUNT(listing) WHERE num_replies_wk > 2",
        "source": "eu_bi.fact_listings_liquidity_success + fact_listings",
        "grain": "monthly + daily cohorts",
        "dims": "finance L1/L2, category L1/L2, region",
        "caveats": "",
    },
    "liquidity_rate_7d_1r": {
        "label": "Liquidity 7d rate",
        "definition": "Share of the NNL cohort that got at least 1 reply within 7 days.",
        "formula": "liquid_listings_7d_1r ÷ NNL, same cohort month",
        "source": "derived in the dashboard",
        "grain": "monthly cohorts",
        "dims": "follows the two source metrics",
        "caveats": "NNL is keyed on first-active month; both series use the same cohort basis.",
    },
    "ftl_success_listings_14d_3r": {
        "label": "First-time listers' listings success",
        "definition": "New listings by first-time listers that got ≥3 replies within 14 days.",
        "formula": "COUNT(listing) WHERE num_replies_2wk > 2 AND first_time",
        "source": "+ eu_bi.fact_listings_user_segments",
        "grain": "monthly cohorts (14d maturity)",
        "dims": "total only",
        "caveats": "",
    },
    "ftl_success_listers_14d_3r": {
        "label": "First-time listers success",
        "definition": "Distinct first-time listers whose listing got ≥3 replies within 14 days.",
        "formula": "COUNT(DISTINCT user_sk), same filters",
        "source": "+ eu_bi.fact_listings_user_segments",
        "grain": "monthly cohorts",
        "dims": "total only",
        "caveats": "",
    },
    "unique_repliers": {
        "label": "Unique repliers",
        "definition": "Distinct visitor sessions that sent at least one successful reply "
                      "(buyer side) — session-based, so anonymous repliers (calls, "
                      "phone reveals) are included.",
        "formula": "COUNT(DISTINCT session_long_sk) over successful reply events",
        "source": "eu_bi.fact_replies_success_legacy",
        "grain": "monthly (24m) + daily (90d)",
        "dims": CAT_DIMS + ", region",
        "caveats": "Session-based since 2026-08-11 (previously logged-in users only, ~2x lower). Table renamed from fact_replies_success.",
    },
    "replies": {
        "label": "Replies",
        "definition": "Successful reply events (chat, call, SMS) from buyers to listings.",
        "formula": "COUNT(*) of successful reply events",
        "source": "eu_bi.fact_replies_success_legacy",
        "grain": "monthly + daily",
        "dims": CAT_DIMS + ", region",
        "caveats": "",
    },
    "pmul": {
        "label": "Paying listers (PMUL)",
        "definition": "Distinct users who paid for anything revenue-relevant in the period.",
        "formula": "COUNT(DISTINCT user_sk) WHERE revenue_stream != 'Not Revenue' AND trans_value_net != 0",
        "source": "eu_bi.fact_payments + dim_products",
        "grain": "monthly (24m) + daily (90d, total only)",
        "dims": FULL_DIMS,
        "caveats": "",
    },
    "revenue_gross": {
        "label": "Revenue gross (UZS)",
        "definition": "Gross transactional revenue in local currency, 'Not Revenue' excluded.",
        "formula": "SUM(trans_value_gross)",
        "source": "eu_bi.fact_payments + dim_products",
        "grain": "monthly (24m)",
        "dims": FULL_DIMS + ", revenue stream",
        "caveats": "Local currency (UZS), not USD.",
    },
    "revenue_net": {
        "label": "Revenue net (UZS)",
        "definition": "Net transactional revenue in local currency (after fees/tax components).",
        "formula": "SUM(trans_value_net)",
        "source": "eu_bi.fact_payments + dim_products",
        "grain": "monthly (24m) + daily (90d, total only)",
        "dims": FULL_DIMS + ", revenue stream",
        "caveats": "Local currency (UZS).",
    },
    "service_fee": {
        "label": "Service fee (UZS)",
        "definition": "Service-fee component of transactions.",
        "formula": "SUM(trans_value_service_fee)",
        "source": "eu_bi.fact_payments",
        "grain": "monthly",
        "dims": FULL_DIMS + ", revenue stream",
        "caveats": "",
    },
    "tax": {
        "label": "Tax (UZS)",
        "definition": "Tax component of transactions.",
        "formula": "SUM(trans_value_tax)",
        "source": "eu_bi.fact_payments",
        "grain": "monthly",
        "dims": FULL_DIMS + ", revenue stream",
        "caveats": "",
    },
    "bonus_gross": {
        "label": "Bonus gross (UZS)",
        "definition": "Value paid with bonus balance.",
        "formula": "SUM(bonus_value_gross)",
        "source": "eu_bi.fact_payments",
        "grain": "monthly",
        "dims": FULL_DIMS + ", revenue stream",
        "caveats": "",
    },
    "refund_gross": {
        "label": "Refunds gross (UZS)",
        "definition": "Refunded value.",
        "formula": "SUM(refund_value_gross)",
        "source": "eu_bi.fact_payments",
        "grain": "monthly",
        "dims": FULL_DIMS + ", revenue stream",
        "caveats": "",
    },
    "transactions": {
        "label": "Transactions",
        "definition": "Distinct revenue transactions.",
        "formula": "COUNT(DISTINCT transaction_sk)",
        "source": "eu_bi.fact_payments",
        "grain": "monthly",
        "dims": FULL_DIMS + ", revenue stream",
        "caveats": "",
    },
    "payments": {
        "label": "Payments",
        "definition": "Distinct payment operations.",
        "formula": "COUNT(DISTINCT payment_sk)",
        "source": "eu_bi.fact_payments",
        "grain": "monthly",
        "dims": FULL_DIMS + ", revenue stream",
        "caveats": "",
    },
    "cash_flows": {
        "label": "Cash flows",
        "definition": "Cash-flow operations (wallet top-ups etc., not revenue).",
        "formula": "COUNT(DISTINCT payment_sk) WHERE cash_value_gross != 0",
        "source": "eu_bi.fact_payments",
        "grain": "monthly",
        "dims": "total only",
        "caveats": "",
    },
    "cash_flow_payers": {
        "label": "Cash-flow payers",
        "definition": "Distinct users with cash-flow operations.",
        "formula": "COUNT(DISTINCT user_sk) WHERE cash_value_gross != 0",
        "source": "eu_bi.fact_payments",
        "grain": "monthly",
        "dims": "total only",
        "caveats": "",
    },
    "new_users": {
        "label": "New users",
        "definition": "Accounts created in the period that aren't banned/deleted.",
        "formula": "COUNT(DISTINCT user_sk) by time_created, status not in (banned, deleted, pending_deletion)",
        "source": "eu_bi.dim_users",
        "grain": "monthly (24m)",
        "dims": "total only (no category/geography on accounts)",
        "caveats": "",
    },
    "confirmed_new_users": {
        "label": "Confirmed new users",
        "definition": "New accounts that completed confirmation.",
        "formula": "COUNT(DISTINCT user_sk) WHERE user_status = 'confirmed'",
        "source": "eu_bi.dim_users",
        "grain": "monthly (24m)",
        "dims": "total only",
        "caveats": "",
    },
}

METRIC_DEFS["liquid_listings_1d_1r"] = {
    "label": "Liquid listings 1d (1 reply)",
    "definition": "Net-new listings that received at least 1 reply within their FIRST DAY.",
    "formula": "COUNT(listing) WHERE num_replies_day > 0, by posting cohort",
    "source": "eu_bi.fact_listings_liquidity_success + fact_listings",
    "grain": "monthly + weekly cohorts",
    "dims": "finance L1/L2, category L1/L2, region",
    "caveats": "Top of the liquidity speed funnel.",
}
METRIC_DEFS["liquid_listings_1d_3r"] = {
    "label": "Liquid listings 1d (3 replies)",
    "definition": "Net-new listings that reached 3 replies within their first day.",
    "formula": "COUNT(listing) WHERE num_replies_day > 2, by posting cohort",
    "source": "eu_bi.fact_listings_liquidity_success + fact_listings",
    "grain": "monthly + weekly cohorts",
    "dims": "finance L1/L2, category L1/L2, region",
    "caveats": "",
}

# variants share the base definition
for _w, _r in (("7d", "3r"), ("14d", "1r"), ("14d", "3r"), ("28d", "1r"), ("28d", "3r")):
    for _kind in ("listings", "listers"):
        key = "liquid_%s_%s_%s" % (_kind, _w, _r)
        if key not in METRIC_DEFS:
            base = dict(METRIC_DEFS["liquid_%s_7d_1r" % _kind])
            days = _w.rstrip("d")
            reps = "1 reply" if _r == "1r" else "3 replies"
            base["label"] = "Liquid %s %s (%s)" % (_kind, _w, reps)
            base["definition"] = (
                "Net-new %s with at least %s within %s days of posting."
                % (_kind, reps.replace("reply", "reply received").replace("replies", "replies received"), days))
            METRIC_DEFS[key] = base


def info(metric):
    """Short info text for the ⓘ popover."""
    d = METRIC_DEFS.get(metric)
    if not d:
        return None
    parts = [d["definition"], "Formula: " + d["formula"], "Source: " + d["source"]]
    if d.get("caveats"):
        parts.append("Note: " + d["caveats"])
    return "\n".join(parts)
