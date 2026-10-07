"""Shared constants for the WBR Truth Board's out-of-registry metrics.

The board compares tteam Trino's WBR marts with Yamato, week by week. Its
inputs bypass the MetricSpec registry: updater/wbr_extract.py pulls them on a
machine that can reach the warehouses, updater/wbr_merge.py merges the payload
into any store, and webapp/wbr.py turns the daily rows into the weekly grid.
Everything here must stay stdlib-only — the box imports this module.

Row shape in the `metrics` table:
  metric    = 'wbr_<key>' (keys below)
  grain     = 'daily' (period YYYY-MM-DD) or 'weekly' (period = Monday)
  dim_name  = 'source'
  dim_value = 'trino' | 'yamato'

The WBR_PREFIX convention drives:
  * store.prune_old_periods() exempts these rows from registry retention
    (the board keeps the whole year);
  * wbr_merge replaces exactly these rows, per source, when a payload lands.
"""

WBR_PREFIX = "wbr_"

# meta rows recording extraction freshness per source
WBR_META_NAMES = ["wbr_trino", "wbr_yamato"]

SOURCE_DIM = "source"
SOURCES = ("trino", "yamato")

# first day the board covers (tteam Trino WBR marts start in Jan 2026)
WBR_START = "2026-01-01"

# daily metric keys; both sides deliver every key
DAILY_KEYS = ["nnl", "st", "re", "se", "rd", "rv", "tr", "py", "al", "dau", "vw"]
# weekly metric keys (week-distinct counts that cannot be summed from days)
WEEKLY_KEYS = ["repwk"]


def metric_name(key):
    return WBR_PREFIX + key
