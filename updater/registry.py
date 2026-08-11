"""Registry of metric extraction specs and filter dimensions.

One spec = one SQL template = one warehouse query family, extracted at up to
three grains (monthly / weekly / daily). A spec can produce several stored
metrics (one per value column). Each grain × dimension slice is its own exact
Redshift aggregate: distinct counts can never be re-aggregated client-side,
so every filterable slice at every grain is computed in the warehouse.

SQL templates carry these placeholders, filled by extract.render_sql():
    {period_expr} grain-specific period key built from the spec's date_expr
    {date_expr}   the spec's date expression (used in WHERE range filters)
    {dim_name}    literal dim name written into the dim_name column
    {dim_value}   SQL expression for the dim value
    {dim_join}    extra JOIN clause the dim needs ('' for total)
    {group_by}    '1' for total, '1, 3' for real dims
    {value_col}   first value column name (for per-grain aliased templates)
"""

from dataclasses import dataclass
from typing import Dict, List

from config import settings


@dataclass(frozen=True)
class Dim:
    name: str        # dim_name stored in the snapshot ('finance_l2', 'region', ...)
    value_expr: str  # SQL expression producing the dim value
    join: str = ""   # JOIN clause required by value_expr ('' if none)


TOTAL_DIM = Dim("total", "'- Total -'")

GRAINS = ("monthly", "weekly", "daily")


@dataclass(frozen=True)
class GrainPlan:
    window: int                # retention, in periods of this grain
    chunk: int                 # periods per query chunk
    dims: tuple = ()           # slices at this grain (beyond total)
    include_total: bool = True # False when another spec owns the total rows
    value_columns: tuple = ()  # override of the spec's value columns
    sql_file: str = ""         # override of the spec's template
    maturity_days: int = -1    # override of the spec's maturity (-1 = inherit)


@dataclass
class MetricSpec:
    name: str                  # spec name, keys the meta table
    sql_file: str              # unified template, relative to sql/
    date_expr: str             # the metric's date column/expression
    value_columns: List[str]   # SQL value columns; each becomes a metric
    grains: Dict[str, GrainPlan]
    maturity_days: int = 0     # cohort metrics: only extract periods at least
                               # this many days old (0 = no guard)

    def plan(self, grain):
        return self.grains[grain]

    def cols(self, grain):
        return list(self.grains[grain].value_columns or self.value_columns)

    def file_for(self, grain):
        return self.grains[grain].sql_file or self.sql_file

    def maturity_for(self, grain):
        m = self.grains[grain].maturity_days
        return self.maturity_days if m < 0 else m


def cat_dims(alias, levels=4):
    """Finance L1/L2 + category L1..L<levels> via eu_bi.dim_categories.
    `alias` must expose category_sk."""
    join = ("LEFT JOIN eu_bi.dim_categories dcat "
            "ON dcat.category_sk = %s.category_sk" % alias)

    def mk(name, col):
        return Dim(name, "COALESCE(dcat.%s, 'unknown')" % col, join)

    dims = [mk("finance_l1", "finance_category_l1_name_en"),
            mk("finance_l2", "finance_category_l2_name_en")]
    for lvl in range(1, levels + 1):
        dims.append(mk("category_l%d" % lvl, "category_l%d_name_en" % lvl))
    return dims


def region_dim(alias):
    """UZ regions (13 viloyats) = geography level 1. `alias` must expose
    geography_sk — fact_audience_categories and fact_listings_traffic_agg
    don't have it, so audience metrics carry no region slice."""
    return [Dim("region", "COALESCE(geo.geography_l1_name_en, 'unknown')",
                "LEFT JOIN eu_bi.dim_geographies geo "
                "ON geo.geography_sk = %s.geography_sk" % alias)]


def seller_dim(alias):
    """B2C/C2C via eu_bi.dim_users. `alias` must expose user_sk."""
    return [Dim("seller_type", "COALESCE(urs.seller_type, 'unknown')",
                "LEFT JOIN eu_bi.dim_users urs ON urs.user_sk = %s.user_sk" % alias)]


REVENUE_STREAM_DIM = Dim(
    "revenue_stream", "COALESCE(pdc.revenue_stream, 'unknown')")  # pdc joined in base SQL

PAIR_SEP = "|"


def pair(dim_a, dim_b):
    """Cross-dimension slice: dim_name 'a|b', dim_value 'a_val|b_val'.
    Precomputed in Redshift so filtering by two dimensions at once stays
    exact even for distinct counts."""
    return Dim(
        dim_a.name + PAIR_SEP + dim_b.name,
        "%s || '%s' || %s" % (dim_a.value_expr, PAIR_SEP, dim_b.value_expr),
        (dim_a.join + "\n" + dim_b.join).strip(),
    )


def region_pairs(cat_alias, region_alias, levels=("finance_l1", "finance_l2",
                                                  "category_l1", "category_l2")):
    """The precomputed two-dim combos: coarse category levels × region."""
    cats = {d.name: d for d in cat_dims(cat_alias)}
    reg = region_dim(region_alias)[0]
    return [pair(cats[lvl], reg) for lvl in levels]


def seller_pairs(cat_alias, seller_alias, levels=("finance_l2",)):
    cats = {d.name: d for d in cat_dims(cat_alias)}
    sel = seller_dim(seller_alias)[0]
    return [pair(cats[lvl], sel) for lvl in levels]


def std_plans(dims=(), chunks=(12, 26, 90), include_total=True, **daily_overrides):
    """The common three-grain layout with shared dims (full grain parity).
    chunks = (monthly, weekly, daily) periods per query. Keyword overrides
    (value_columns / sql_file / maturity_days) apply to the DAILY plan only."""
    dims = tuple(dims)
    return {
        "monthly": GrainPlan(settings.MONTHLY_WINDOW_MONTHS, chunks[0], dims, include_total),
        "weekly": GrainPlan(settings.WEEKLY_WINDOW_WEEKS, chunks[1], dims, include_total),
        "daily": GrainPlan(settings.DAILY_WINDOW_DAYS, chunks[2], dims, include_total,
                           **daily_overrides),
    }


# Shared dim sets (aliases must match each spec's SQL template)
def listings_dims(alias, seller_alias=None):
    sa = seller_alias or alias
    return (cat_dims(alias) + region_dim(alias) + seller_dim(sa)
            + region_pairs(alias, alias) + seller_pairs(alias, sa))


PAYMENT_DIMS = tuple(cat_dims("fp") + region_dim("fp") + seller_dim("fp")
                     + region_pairs("fp", "fp") + seller_pairs("fp", "fp"))
REPLIER_DIMS = tuple(cat_dims("fr") + region_dim("fr") + region_pairs("fr", "fr"))
LIQUIDITY_DIMS = tuple(cat_dims("b", levels=2) + region_dim("b")
                       + region_pairs("b", "b", levels=("finance_l2", "category_l2")))
AUDIENCE_DIMS = tuple(cat_dims("fac"))


METRICS = [
    # -- cheap dimension-free families --------------------------------------
    MetricSpec("new_users", "new_users.sql", "t.time_created",
               ["new_users", "confirmed_new_users"], std_plans()),
    MetricSpec("ad_impressions", "ad_impressions.sql", "t.date_event_local",
               ["ad_impressions", "ad_views"], std_plans()),
    MetricSpec("cash_flows", "cash_flows.sql", "fp.payment_date",
               ["cash_flows", "cash_flow_payers"], std_plans()),
    # -- payments -------------------------------------------------------------
    MetricSpec("revenue", "revenue.sql", "fp.payment_date",
               ["revenue_gross", "revenue_net", "service_fee", "tax",
                "bonus_gross", "refund_gross", "transactions", "payments"],
               std_plans(PAYMENT_DIMS + (REVENUE_STREAM_DIM,), chunks=(6, 13, 30))),
    MetricSpec("pmul", "pmul.sql", "fp.transaction_date", ["pmul"],
               std_plans(PAYMENT_DIMS, chunks=(6, 13, 30))),
    # -- replies ----------------------------------------------------------------
    MetricSpec("repliers", "repliers.sql", "fr.date_sent_nk",
               ["unique_repliers", "replies"],
               std_plans(REPLIER_DIMS, chunks=(6, 13, 30))),
    # -- listings ----------------------------------------------------------------
    MetricSpec("insertions", "insertions.sql", "fii.event_date_nk",
               ["insertions_all", "insertions_renewed", "insertions_free"],
               std_plans(listings_dims("fl", "fii"), chunks=(6, 13, 30))),
    MetricSpec("nnl", "nnl.sql", "fl.first_active_date_nk", ["nnl"],
               std_plans(listings_dims("fl"), chunks=(6, 13, 30))),
    MetricSpec("gnl", "gnl.sql",
               "COALESCE(fl.first_active_date_local::DATE, fl.date_posted_nk)",
               ["gnl"], std_plans(listings_dims("fl"), chunks=(6, 13, 30))),
    MetricSpec("unique_listers", "unique_listers.sql",
               "COALESCE(fl.first_active_date_local::DATE, fl.date_posted_nk)",
               ["unique_listers"], std_plans(listings_dims("fl"), chunks=(6, 13, 30))),
    # -- liquidity (posting-cohort metrics with maturity guards) -----------------
    MetricSpec("liquidity", "liquidity.sql", "a.first_active_date_nk",
               ["liquid_listings_1d_1r", "liquid_listings_1d_3r",
                "liquid_listings_7d_1r", "liquid_listers_7d_1r",
                "liquid_listings_7d_3r", "liquid_listers_7d_3r",
                "liquid_listings_14d_1r", "liquid_listers_14d_1r",
                "liquid_listings_14d_3r", "liquid_listers_14d_3r",
                "liquid_listings_28d_1r", "liquid_listers_28d_1r",
                "liquid_listings_28d_3r", "liquid_listers_28d_3r"],
               std_plans(LIQUIDITY_DIMS, chunks=(6, 13, 30),
                         value_columns=("liquid_listings_7d_1r",
                                        "liquid_listers_7d_1r",
                                        "liquid_listings_7d_3r"),
                         sql_file="liquidity_daily.sql",
                         maturity_days=8),
               maturity_days=29),
    MetricSpec("first_time_listers", "first_time_listers.sql",
               "a.first_active_date_nk",
               ["ftl_success_listings_14d_3r", "ftl_success_listers_14d_3r"],
               std_plans(chunks=(6, 13, 30)), maturity_days=15),
    # -- audience (no geography_sk on the source → no region slice) ----------------
    MetricSpec("traffic", "traffic.sql", "fac.date_event_local",
               ["pageviews", "bounces"], std_plans(chunks=(3, 6, 30))),
    MetricSpec("traffic_by_category", "traffic_by_category.sql",
               "fac.date_event_local",
               ["visits", "bounces_per_category", "pageviews", "entering_visits"],
               std_plans(AUDIENCE_DIMS, chunks=(1, 4, 15), include_total=False)),
    MetricSpec("active_users", "active_users.sql", "fac.date_event_local",
               ["mau"],
               {"monthly": GrainPlan(settings.MONTHLY_WINDOW_MONTHS, 1, AUDIENCE_DIMS),
                "weekly": GrainPlan(settings.WEEKLY_WINDOW_WEEKS, 4, AUDIENCE_DIMS,
                                    value_columns=("wau",)),
                "daily": GrainPlan(settings.DAILY_WINDOW_DAYS, 30, AUDIENCE_DIMS,
                                   value_columns=("dau",))}),
    MetricSpec("active_listings", "active_listings.sql", "fal.date_nk",
               ["active_listings", "active_listers"],
               std_plans(listings_dims("fl"), chunks=(1, 4, 15))),
]

SPEC_BY_NAME = {s.name: s for s in METRICS}

# Human labels for the webapp's filter UI (order = display order).
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
