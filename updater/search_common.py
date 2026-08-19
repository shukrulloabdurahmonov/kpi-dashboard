"""Shared constants for the Search dashboard's out-of-registry metrics.

Search metrics come from Trino (glue search KPI tables) and hydra clickstream,
neither reachable from the box, so they bypass the MetricSpec registry:
updater/search_extract.py (Mac-only) pulls them, updater/search_merge.py
merges the payload into any store. Everything here must stay stdlib-only —
the box imports this module.

The SEARCH_PREFIX convention drives three behaviors:
  * store.prune_old_periods() exempts these rows from registry retention;
  * search_merge deletes exactly these rows before re-inserting a payload;
  * webapp/data.py marks the non-additive ones so slices are never summed.
"""

SEARCH_PREFIX = "search_"

# meta rows (spec-name style) recording extraction freshness per source
SEARCH_META_NAMES = ["search_trino", "search_hydra"]

# Trino-sourced metrics (avg-daily semantics unless noted)
TRINO_METRICS = [
    "search_users",           # avg daily users with >=1 search
    "search_users_adview",    # avg daily searchers who same-day viewed an ad
    "search_users_lead",      # avg daily searchers who same-day replied
    "search_volume",          # avg daily searches
    "search_volume_adview",   # avg daily ad views by searchers
    "search_volume_lead",     # avg daily replies by searchers
    "search_ssu_adview",      # % of search users reaching an ad view
    "search_ssu_lead",        # % of search users reaching a reply
    "search_avg_adview_su",   # ad views per search user per day
    "search_avg_lead_su",     # replies per search user per day
    "search_share_on_platform",  # % of platform users searching / method share
    "search_searches",        # TRUE daily/monthly totals (event counts)
]

# hydra-sourced metrics (true event counts)
HYDRA_METRICS = [
    "search_serp",     # first-page keyword SERP views, bot-filtered
    "search_zsr",      # SERP views with 0 results
    "search_zsr_low",  # SERP views with 1-10 results (low supply)
]

# ratio/average metrics whose slices must never be summed
SEARCH_NON_ADDITIVE = {
    "search_users", "search_users_adview", "search_users_lead",
    "search_volume", "search_volume_adview", "search_volume_lead",
    "search_ssu_adview", "search_ssu_lead",
    "search_avg_adview_su", "search_avg_lead_su",
    "search_share_on_platform",
}

# daily-grain search rows older than this are pruned by search_merge
SEARCH_DAILY_RETENTION_DAYS = 90
