"""Search-dashboard extractor — runs ONLY on the Mac.

    /usr/bin/python3 -m updater.search_extract                # rolling refresh
    /usr/bin/python3 -m updater.search_extract --backfill     # full history
    /usr/bin/python3 -m updater.search_extract --local-only   # don't push to box
    /usr/bin/python3 -m updater.search_extract --no-hydra     # Trino sources only
    /usr/bin/python3 -m updater.search_extract --no-trino     # hydra sources only

Pulls the Search dashboard's metrics from two sources the box can't reach:

  * Trino (presto.data.olx.org, LDAP password in the macOS Keychain under
    service 'presto-ldap') — the glue search-KPI tables behind
    Scripts/uz_search_kpis/. Weekly funnel + monthly category rollups are
    AVERAGE-DAILY values; search_searches is a true event count.
  * hydra clickstream on yamato (VPN, reusing updater.db.Warehouse) —
    region-split SERP/ZSR counts and the top-keywords table, query patterns
    proven in Scripts/zsr_dashboard/zsr_build.py.

Rows are written to a small payload sqlite, merged into the local store, and
(unless --local-only) scp'd to the box where updater/search_merge.py merges
them into the box's store and republishes the snapshot. All metric names
carry the `search_` prefix (see updater/search_common.py).

Exit codes: 0 ok · 1 fatal · 2 partial (one source failed, payload still
built and pushed with the other).
"""

import argparse
import logging
import subprocess
import sys
import time
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import settings                                     # noqa: E402
from updater import search_merge, store                         # noqa: E402
from updater.db import Warehouse, warehouse_reachable           # noqa: E402
from updater.periods import month_key                           # noqa: E402
from updater.search_common import (                             # noqa: E402
    SEARCH_DAILY_RETENTION_DAYS,
)

log = logging.getLogger("updater.search_extract")

SQL_DIR = settings.PROJECT_DIR / "sql" / "search"
PAYLOAD_PATH = settings.PROJECT_DIR / "updater" / "store" / "search_payload.sqlite"

TOTAL_DIM = ("total", "- Total -")

# --- Trino ------------------------------------------------------------------
TRINO_HOST = "presto.data.olx.org"
TRINO_USER = "s.abdurahmonov@olx.uz"
KEYCHAIN_SERVICE = "presto-ldap"

# history starts (from uz_search_kpis/README.md)
TRINO_WEEKLY_START = "2024-12-30"    # ISO W01 2025, matches production table
TRINO_MONTHLY_START = "2025-01-01"

# platform spellings in the glue rollup tables → display names
PLATFORM_DISPLAY = {
    "android": "Android", "ios": "iOS",
    "desktop": "Web Desktop", "mobile_html5": "Web Mobile",
}

# --- hydra (yamato) ----------------------------------------------------------
HYDRA_START = "2025-07-02"           # UZ hydra retention start
LOW_MIN, LOW_MAX = 1, 10
SENTINEL = 999999
KEYWORD_WINDOW_DAYS = 28
KEYWORD_TOP_N = 500                  # per platform
KEYWORD_MIN_SEARCHES = 30

# SERP predicates per platform (validated in zsr_dashboard)
HYDRA_PLATFORMS = {
    "web":     "e.eventname = 'listing' AND e.keyword <> ''",
    "android": "e.eventname IS NULL AND e.trackpage = 'listing' AND e.keyword <> ''",
    "ios":     "e.eventname IS NULL AND e.trackpage = 'listing' AND e.keyword <> ''",
}
# same pages WITHOUT the keyword requirement — includes category browsing
# ("navigation") result pages, the CTR denominator
HYDRA_LISTING = {
    "web":     "e.eventname = 'listing'",
    "android": "e.eventname IS NULL AND e.trackpage = 'listing'",
    "ios":     "e.eventname IS NULL AND e.trackpage = 'listing'",
}
HYDRA_PLATFORM_DISPLAY = {"web": "Web", "android": "Android", "ios": "iOS"}

# Bot filter copied from colibri impressions_daily.py (the house standard).
BOT_FILTER = """\
  AND bl.crawler_ip_hash IS NULL
  AND e.user_agent NOT ILIKE '%bot%'
  AND e.user_agent NOT ILIKE '%spider%'
  AND e.user_agent NOT ILIKE '%crawler%'
  AND e.user_agent NOT ILIKE '%Google-AdSense-Auto%'
  AND e.user_agent NOT ILIKE '%Google Web Preview%'
  AND e.user_agent NOT ILIKE '%facebookexternalhit%'
  AND e.user_agent NOT ILIKE '%HeadlessChrome%'
  AND e.user_agent NOT ILIKE '%phantomjs%'
  AND e.user_agent NOT ILIKE '%pingdom%'
  AND e.user_agent NOT ILIKE '%bingpreview%'
  AND e.user_agent NOT ILIKE '%statuscake%'
  AND e.user_agent NOT ILIKE '%scrapy%'
  AND e.user_agent <> 'Go-http-client/1.1'
  AND e.user_agent NOT ILIKE '%guzzle%'
  AND e.user_agent NOT ILIKE '%node-fetch%'
  AND e.user_agent NOT ILIKE '%chromeless%'"""

# livesync.cee_olxuz_regions name_ru → uz_regions.json properties.name (the
# spelling every other region dim in the store already uses)
REGION_RU_TO_UZ = {
    "Андижанская область": "Andijon viloyati",
    "Бухарская область": "Buxoro viloyati",
    "Джизакская область": "Jizzax viloyati",
    "Каракалпакстан": "Qoraqalpog‘iston",
    "Кашкадарьинская область": "Qashqadaryo viloyati",
    "Навоийская область": "Navoiy viloyati",
    "Наманганская область": "Namangan viloyati",
    "Самаркандская область": "Samarqand viloyati",
    "Сурхандарьинская область": "Surxondaryo viloyati",
    "Сырдарьинская область": "Sirdaryo viloyati",
    "Ташкентская область": "Toshkent viloyati",
    "Ферганская область": "Farg‘ona viloyati",
    "Хорезмская область": "Xorazm viloyati",
}


def read_sql(name):
    return (SQL_DIR / name).read_text()


# --- Trino access -------------------------------------------------------------

def keychain_password():
    out = subprocess.run(
        ["security", "find-generic-password", "-a", TRINO_USER,
         "-s", KEYCHAIN_SERVICE, "-w"],
        capture_output=True, text=True,
    )
    pw = out.stdout.strip()
    if not pw:
        raise RuntimeError(
            "Trino LDAP password not found in Keychain (service '%s')"
            % KEYCHAIN_SERVICE)
    return pw


def trino_connect():
    import trino
    from trino.auth import BasicAuthentication
    return trino.dbapi.connect(
        host=TRINO_HOST, port=443, user=TRINO_USER,
        catalog="awsdatacatalog", http_scheme="https",
        auth=BasicAuthentication(TRINO_USER, keychain_password()),
        request_timeout=600,
    )


def trino_query(tc, sql, retries=3):
    last = None
    for attempt in range(1, retries + 1):
        try:
            cur = tc.cursor()
            cur.execute(sql)
            cols = [d[0] for d in cur.description]
            return cols, cur.fetchall()
        except Exception as exc:                       # noqa: BLE001
            last = exc
            log.warning("Trino attempt %d/%d failed: %s", attempt, retries, exc)
            time.sleep(15 * attempt)
    raise RuntimeError("Trino query failed after %d attempts: %s" % (retries, last))


def dictrows(cols, rows):
    return [dict(zip(cols, r)) for r in rows]


def display_platform(p):
    return PLATFORM_DISPLAY.get(str(p).lower(), str(p))


def base_dims(platform, method):
    """(dim_name, dim_value) for a platform × search_method rollup row."""
    p_all, m_all = platform == "All", method == "All"
    if p_all and m_all:
        return TOTAL_DIM
    if m_all:
        return ("platform", display_platform(platform))
    if p_all:
        return ("method", method)
    return ("platform|method", display_platform(platform) + "|" + method)


def pct(v):
    return None if v is None else round(float(v) * 100.0, 2)


# --- Trino pulls (each returns [(metric, grain, period, dim_name, dim_value, value)])

FUNNEL_VALUE_MAP = [
    ("search_users", "users_search", None),
    ("search_users_adview", "users_adview", None),
    ("search_users_lead", "users_lead", None),
    ("search_volume", "volume_search", None),
    ("search_volume_adview", "volume_adview", None),
    ("search_volume_lead", "volume_lead", None),
    ("search_ssu_adview", "ssu_adview", pct),
    ("search_ssu_lead", "ssu_lead", pct),
    ("search_avg_adview_su", "avg_adview_su", float),
    ("search_avg_lead_su", "avg_lead_su", float),
    ("search_share_on_platform", "share_search_on_platform", pct),
]


def _pull_funnel(tc, sql_name, period_col, grain, start):
    cols, rows = trino_query(tc, read_sql(sql_name).format(start=start))
    out = []
    for r in dictrows(cols, rows):
        period = str(r[period_col])[:10]
        dim_name, dim_value = base_dims(r["platform"], r["search_method"])
        for metric, col, conv in FUNNEL_VALUE_MAP:
            v = r.get(col)
            if v is None:
                continue
            out.append((metric, grain, period, dim_name, dim_value,
                        conv(v) if conv else float(v)))
    return out


def pull_weekly_funnel(tc, start):
    return _pull_funnel(tc, "weekly_funnel.sql", "week_start", "weekly", start)


def pull_daily_funnel(tc, start):
    return _pull_funnel(tc, "daily_funnel.sql", "period_day", "daily", start)


def pull_monthly_by_category(tc, start):
    cols, rows = trino_query(tc,
                             read_sql("monthly_by_category.sql").format(start=start))
    out = []
    rollup_map = [
        ("search_users", "users_search", None),
        ("search_users_adview", "users_adview", None),
        ("search_users_lead", "users_lead", None),
        ("search_volume", "volume_search", None),
        ("search_volume_adview", "volume_adview", None),
        ("search_volume_lead", "volume_lead", None),
        ("search_ssu_adview", "ssu_adview", pct),
        ("search_ssu_lead", "ssu_lead", pct),
    ]
    cat_map = [
        ("search_users", "users_search", None),
        ("search_volume", "volume_search", None),
        ("search_ssu_adview", "ssu_adview", pct),
        ("search_ssu_lead", "ssu_lead", pct),
    ]
    for r in dictrows(cols, rows):
        period = str(r["month"])[:7]
        cat = r["finance_category_l2"]
        if cat == "All":
            dim_name, dim_value = base_dims(r["platform"], r["search_method"])
            value_map = rollup_map
        elif r["platform"] == "All" and r["search_method"] == "All":
            dim_name, dim_value = ("search_cat", cat)
            value_map = cat_map
        else:
            continue  # per-category × platform × method slices: not stored
        for metric, col, conv in value_map:
            v = r.get(col)
            if v is None:
                continue
            out.append((metric, "monthly", period, dim_name, dim_value,
                        conv(v) if conv else float(v)))
    return out


def _searches_rows(records, grain, period_of):
    """Aggregate raw search-volume records into search_searches rows across
    the dims the page uses. searches are per-event counts, so sums are exact."""
    agg = {}

    def add(period, dim_name, dim_value, v):
        key = (period, dim_name, dim_value)
        agg[key] = agg.get(key, 0) + v

    for r in records:
        period = period_of(r)
        v = float(r["searches"])
        p = display_platform(r["platform"])
        m = r["search_method"]
        add(period, *TOTAL_DIM, v=v)
        add(period, "platform", p, v)
        add(period, "method", m, v)
        add(period, "platform|method", p + "|" + m, v)
        add(period, "category_usage", r["category_usage"], v)
        if grain == "monthly":
            l1, l2 = r["finance_l1"], r["finance_l2"]
            add(period, "search_cat_l1", l1, v)
            add(period, "search_cat", l2, v)
            add(period, "search_cat_l1|search_cat", l1 + "|" + l2, v)
    return [("search_searches", grain, p, dn, dv, v)
            for (p, dn, dv), v in agg.items()]


def pull_daily_searches(tc, start):
    cols, rows = trino_query(tc, read_sql("daily_by_category.sql").format(start=start))
    return _searches_rows(dictrows(cols, rows), "daily",
                          lambda r: str(r["date_day"])[:10])


def pull_monthly_searches(tc, start):
    cols, rows = trino_query(tc, read_sql("monthly_from_daily.sql").format(start=start))
    return _searches_rows(dictrows(cols, rows), "monthly",
                          lambda r: str(r["month"])[:7])


# --- hydra pulls ---------------------------------------------------------------

def region_names(wh):
    _, rows = wh.query("SELECT id, name_ru FROM livesync.cee_olxuz_regions", None)
    out = {}
    for rid, name_ru in rows:
        uz = REGION_RU_TO_UZ.get(str(name_ru).strip())
        if uz:
            out[int(rid)] = uz
    return out


def month_chunks(d1, d2):
    """[(iso_start, iso_end)] month-aligned inclusive chunks covering [d1, d2]
    — hydra scans over the full history get killed by WLM; per-month they
    finish in minutes (same approach as zsr_dashboard)."""
    chunks = []
    cur = date.fromisoformat(d1)
    end = date.fromisoformat(d2)
    while cur <= end:
        nxt = (cur.replace(day=1) + timedelta(days=32)).replace(day=1)
        chunks.append((cur.isoformat(),
                       min(nxt - timedelta(days=1), end).isoformat()))
        cur = nxt
    return chunks


def pull_hydra_regions(wh, d1, d2, daily_from):
    """search_serp / search_zsr / search_zsr_low rows: monthly region splits
    (map + per-region bars) and daily platform trends."""
    regions = region_names(wh)
    template = read_sql("hydra_regions_daily.sql")
    agg = {}

    def add(metric, grain, period, dim_name, dim_value, v):
        key = (metric, grain, period, dim_name, dim_value)
        agg[key] = agg.get(key, 0) + v

    chunks = month_chunks(d1, d2)
    for platform, predicate in HYDRA_PLATFORMS.items():
        disp = HYDRA_PLATFORM_DISPLAY[platform]
        rows_dicts = []
        for c1, c2 in chunks:
            sql = template.format(platform=platform, table=platform,
                                  serp_predicate=predicate, d1=c1, d2=c2,
                                  low_min=LOW_MIN, low_max=LOW_MAX,
                                  sentinel=SENTINEL, bot_filter=BOT_FILTER)
            cols, rows = wh.query(sql, None)
            rows_dicts.extend(dictrows(cols, rows))
            log.info("[regions@%s] chunk %s..%s: %d rows",
                     platform, c1, c2, len(rows))
        for r in rows_dicts:
            day = str(r["day"])[:10]
            month = month_key(date.fromisoformat(day))
            region = regions.get(int(r["region_id"] or 0))
            for metric, col in (("search_serp", "searches"),
                                ("search_zsr", "zsr"),
                                ("search_zsr_low", "low")):
                v = float(r[col])
                add(metric, "monthly", month, *TOTAL_DIM, v=v)
                add(metric, "monthly", month, "platform", disp, v=v)
                if region:
                    add(metric, "monthly", month, "region", region, v=v)
                    add(metric, "monthly", month, "platform|region",
                        disp + "|" + region, v=v)
                if day >= daily_from:
                    add(metric, "daily", day, *TOTAL_DIM, v=v)
                    add(metric, "daily", day, "platform", disp, v=v)
    return [(m, g, p, dn, dv, v) for (m, g, p, dn, dv), v in agg.items()]


DEPTH_LABELS = {0: "No filters", 1: "1 filter", 2: "2 filters", 3: "3+ filters"}

# hydra.web names the price-filter columns differently from android/ios
PRICE_COLS = {
    "web": ("filters_price_from", "filters_price_to"),
    "android": ("price_from", "price_to"),
    "ios": ("price_from", "price_to"),
}


def pull_hydra_filters(wh, d1, d2):
    """How keyword searches are narrowed, monthly, all platforms summed:
       search_filter_depth      dim filter_depth  — search counts (additive)
       search_filter_avg_results dim filter_depth — weighted avg result count
       search_filter_use        dim filter_type   — searches using each
                                                    criterion (OVERLAPPING)"""
    template = read_sql("hydra_filters_monthly.sql")
    counts, rc_sums, use = {}, {}, {}
    for platform, predicate in HYDRA_PLATFORMS.items():
        for c1, c2 in month_chunks(d1, d2):
            pf, pt = PRICE_COLS[platform]
            sql = template.format(platform=platform, table=platform,
                                  serp_predicate=predicate, d1=c1, d2=c2,
                                  sentinel=SENTINEL, bot_filter=BOT_FILTER,
                                  price_from=pf, price_to=pt)
            cols, rows = wh.query(sql, None)
            for r in dictrows(cols, rows):
                month = month_key(date.fromisoformat(str(r["month"])[:10]))
                depth = DEPTH_LABELS[int(r["depth"])]
                n = float(r["searches"])
                counts[(month, depth)] = counts.get((month, depth), 0) + n
                rc_sums[(month, depth)] = (rc_sums.get((month, depth), 0)
                                           + float(r["avg_results"] or 0) * n)
                for ftype, col in (("Category", "w_category"),
                                   ("Region", "w_region"),
                                   ("Price", "w_price"),
                                   ("Attribute filters", "w_attr")):
                    key = (month, ftype)
                    use[key] = use.get(key, 0) + float(r[col])
            log.info("[filters@%s] chunk %s..%s: %d rows",
                     platform, c1, c2, len(rows))
    out = []
    for (month, depth), n in counts.items():
        out.append(("search_filter_depth", "monthly", month,
                    "filter_depth", depth, n))
        out.append(("search_filter_avg_results", "monthly", month,
                    "filter_depth", depth, round(rc_sums[(month, depth)] / n, 1)))
    for (month, ftype), n in use.items():
        out.append(("search_filter_use", "monthly", month,
                    "filter_type", ftype, n))
    return out


FILTER_SLICES = {
    1: "COALESCE(e.filters_count, 0) > 0",   # narrowed searches
    0: "COALESCE(e.filters_count, 0) = 0",   # bare queries
}


def pull_hydra_zsr_categories(wh, d1, d2):
    """Monthly zero-result / low-supply split by category L1:
       search_serp / search_zsr / search_zsr_low under dims
       'zsr_category' (all platforms summed) and 'platform|zsr_category'."""
    template = read_sql("hydra_zsr_categories.sql")
    agg = {}

    def add(metric, period, dim_name, dim_value, v):
        key = (metric, period, dim_name, dim_value)
        agg[key] = agg.get(key, 0) + v

    for platform, predicate in HYDRA_PLATFORMS.items():
        disp = HYDRA_PLATFORM_DISPLAY[platform]
        for c1, c2 in month_chunks(d1, d2):
            sql = template.format(platform=platform, table=platform,
                                  serp_predicate=predicate, d1=c1, d2=c2,
                                  low_min=LOW_MIN, low_max=LOW_MAX,
                                  sentinel=SENTINEL, bot_filter=BOT_FILTER)
            cols, rows = wh.query(sql, None)
            for r in dictrows(cols, rows):
                month = month_key(date.fromisoformat(str(r["month"])[:10]))
                cat = str(r["category"])
                for metric, col in (("search_serp", "searches"),
                                    ("search_zsr", "zsr"),
                                    ("search_zsr_low", "low")):
                    v = float(r[col])
                    add(metric, month, "zsr_category", cat, v)
                    add(metric, month, "platform|zsr_category",
                        disp + "|" + cat, v)
            log.info("[zsr_cats@%s] chunk %s..%s: %d rows",
                     platform, c1, c2, len(rows))
    return [(m, "monthly", p, dn, dv, v)
            for (m, p, dn, dv), v in agg.items()]


def pull_hydra_ctr(wh, d1, d2, daily_from):
    """CTR inputs at daily + monthly grain: result-page views (Search vs
    Navigation) and ad clicks with position buckets. Metrics (all counts,
    additive): search_ctr_serps, search_ctr_clicks, search_ctr_clicks_p1/
    _p3/_p40; dims: 'search_mode', 'platform|search_mode'."""
    template = read_sql("hydra_ctr_monthly.sql")
    agg = {}

    def add(metric, grain, period, dim_name, dim_value, v):
        key = (metric, grain, period, dim_name, dim_value)
        agg[key] = agg.get(key, 0) + v

    for platform in HYDRA_PLATFORMS:
        disp = HYDRA_PLATFORM_DISPLAY[platform]
        for c1, c2 in month_chunks(d1, d2):
            sql = template.format(table=platform, d1=c1, d2=c2,
                                  listing_predicate=HYDRA_LISTING[platform],
                                  bot_filter=BOT_FILTER)
            cols, rows = wh.query(sql, None)
            for r in dictrows(cols, rows):
                day = str(r["day"])[:10]
                month = month_key(date.fromisoformat(day))
                mode = r["mode"]
                if r["row_kind"] == "serps":
                    values = (("search_ctr_serps", float(r["n"])),)
                else:
                    values = (("search_ctr_clicks", float(r["n"])),
                              ("search_ctr_clicks_p1", float(r["p1"])),
                              ("search_ctr_clicks_p3", float(r["p3"])),
                              ("search_ctr_clicks_p40", float(r["p40"])))
                for metric, v in values:
                    for grain, period in (("monthly", month), ("daily", day)):
                        if grain == "daily" and day < daily_from:
                            continue
                        add(metric, grain, period, "search_mode", mode, v)
                        add(metric, grain, period, "platform|search_mode",
                            disp + "|" + mode, v)
            log.info("[ctr@%s] chunk %s..%s: %d rows",
                     platform, c1, c2, len(rows))
    return [(m, g, p, dn, dv, v) for (m, g, p, dn, dv), v in agg.items()]


def pull_hydra_keywords(wh, d1, d2):
    """Top keywords per platform, ranked separately for filtered and bare
    searches — each slice carries its own zsr/low/avg figures."""
    template = read_sql("hydra_keywords.sql")
    out = []
    for platform, predicate in HYDRA_PLATFORMS.items():
        disp = HYDRA_PLATFORM_DISPLAY[platform]
        for flag, fpred in FILTER_SLICES.items():
            sql = template.format(platform=platform, table=platform,
                                  serp_predicate=predicate,
                                  filter_predicate=fpred, d1=d1, d2=d2,
                                  low_min=LOW_MIN, low_max=LOW_MAX,
                                  sentinel=SENTINEL, bot_filter=BOT_FILTER,
                                  top_n=KEYWORD_TOP_N,
                                  min_searches=KEYWORD_MIN_SEARCHES)
            _, rows = wh.query(sql, None)
            out.extend((disp, kw, flag, int(s), int(z), int(lo),
                        round(float(a), 1) if a is not None else None)
                       for _p, kw, s, z, lo, a in rows)
            log.info("[keywords@%s/%s] %d keywords",
                     platform, "filtered" if flag else "bare", len(rows))
    return out


# --- payload / orchestration ----------------------------------------------------

def build_payload(metric_rows, kw_rows, kw_window, meta_counts):
    PAYLOAD_PATH.unlink(missing_ok=True)
    conn = store.open_store(PAYLOAD_PATH)
    try:
        with conn:
            conn.executemany(
                "INSERT OR REPLACE INTO metrics "
                "(metric, grain, period, dim_name, dim_value, value) "
                "VALUES (?, ?, ?, ?, ?, ?)", metric_rows)
        if kw_rows:
            store.replace_search_keywords(conn, kw_rows, *kw_window)
        for name, (latest, nrows) in meta_counts.items():
            store.set_meta_ok(conn, name, latest, nrows)
    finally:
        conn.close()
    log.info("payload built: %s (%d metric rows, %d keywords)",
             PAYLOAD_PATH, len(metric_rows), len(kw_rows))
    return PAYLOAD_PATH


def push_to_box(payload_path):
    """scp the payload to the box and merge+republish there. Needs
    config/deploy.env with DROPLET_HOST / DROPLET_USER / REMOTE_DIR (the
    repo checkout on the box) / DROPLET_SSH_KEY."""
    import os
    env = settings.load_deploy_env()
    for k in ("DROPLET_HOST", "DROPLET_USER", "REMOTE_DIR"):
        if not env.get(k):
            raise RuntimeError("config/deploy.env missing %s — cannot push" % k)
    key = os.path.expanduser(env.get("DROPLET_SSH_KEY", "~/.ssh/id_ed25519"))
    ssh_base = ["-i", key, "-o", "BatchMode=yes", "-o", "ConnectTimeout=15"]
    target = "%s@%s" % (env["DROPLET_USER"], env["DROPLET_HOST"])
    remote = "%s/updater/store/search_payload.sqlite" % env["REMOTE_DIR"]
    subprocess.run(["scp"] + ssh_base + [str(payload_path),
                                         "%s:%s" % (target, remote)],
                   check=True, timeout=300)
    subprocess.run(["ssh"] + ssh_base +
                   [target, "cd %s && python3 -m updater.search_merge "
                            "updater/store/search_payload.sqlite --republish"
                            % env["REMOTE_DIR"]],
                   check=True, timeout=600)
    log.info("payload pushed and merged on %s", target)


def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--backfill", action="store_true",
                    help="pull full history instead of the rolling window")
    ap.add_argument("--local-only", action="store_true",
                    help="build + merge locally, don't push to the box")
    ap.add_argument("--no-trino", action="store_true")
    ap.add_argument("--no-hydra", action="store_true")
    ap.add_argument("--republish", action="store_true",
                    help="also republish the LOCAL snapshot after merging")
    args = ap.parse_args()

    today = date.today()
    # Trino is always pulled full-history: the queries are cheap (~2 min
    # total) and a full payload is self-healing — merging it rebuilds the
    # box's Trino metrics from scratch even into an empty store. Only the
    # hydra scans are windowed (--backfill widens them to full history;
    # merge keeps the box's older hydra months either way).
    weekly_start = TRINO_WEEKLY_START
    monthly_start = TRINO_MONTHLY_START
    daily_start = (today - timedelta(days=SEARCH_DAILY_RETENTION_DAYS - 1)
                   ).isoformat()
    if args.backfill:
        hydra_start = HYDRA_START
    else:
        hydra_start = (today.replace(day=1) - timedelta(days=32)
                       ).replace(day=1).isoformat()

    yesterday = (today - timedelta(days=1)).isoformat()
    metric_rows, kw_rows, meta_counts = [], [], {}
    failures = 0

    if not args.no_trino:
        try:
            tc = trino_connect()
            rows = []
            rows += pull_weekly_funnel(tc, weekly_start)
            log.info("[trino] weekly funnel: %d rows total", len(rows))
            rows += pull_daily_funnel(tc, daily_start)
            log.info("[trino] + daily funnel: %d rows total", len(rows))
            rows += pull_monthly_by_category(tc, monthly_start)
            rows += pull_monthly_searches(tc, monthly_start)
            rows += pull_daily_searches(tc, daily_start)
            log.info("[trino] all pulls done: %d rows", len(rows))
            metric_rows += rows
            latest = max((r[2] for r in rows), default=None)
            meta_counts["search_trino"] = (latest, len(rows))
        except Exception:
            log.exception("[trino] extraction FAILED")
            failures += 1

    if not args.no_hydra:
        if not warehouse_reachable():
            log.error("[hydra] warehouse unreachable (VPN down?) — skipping")
            failures += 1
        else:
            wh = Warehouse()
            try:
                kw_d2 = yesterday
                kw_d1 = (today - timedelta(days=KEYWORD_WINDOW_DAYS)).isoformat()
                rows = pull_hydra_regions(wh, hydra_start, yesterday,
                                          daily_from=daily_start)
                rows += pull_hydra_filters(wh, hydra_start, yesterday)
                rows += pull_hydra_zsr_categories(wh, hydra_start, yesterday)
                rows += pull_hydra_ctr(wh, hydra_start, yesterday,
                                       daily_from=daily_start)
                kw_rows = pull_hydra_keywords(wh, kw_d1, kw_d2)
                metric_rows += rows
                latest = max((r[2] for r in rows), default=None)
                meta_counts["search_hydra"] = (latest, len(rows))
            except Exception:
                log.exception("[hydra] extraction FAILED")
                failures += 1
            finally:
                wh.close()

    if not metric_rows:
        log.error("nothing extracted — aborting")
        sys.exit(1)

    kw_window = ((today - timedelta(days=KEYWORD_WINDOW_DAYS)).isoformat(),
                 yesterday)
    payload = build_payload(metric_rows, kw_rows, kw_window, meta_counts)

    search_merge.merge(payload, settings.STORE_PATH)
    log.info("merged into local store %s", settings.STORE_PATH)
    if args.republish:
        search_merge.republish()

    if not args.local_only:
        try:
            push_to_box(payload)
        except Exception:
            log.exception("push to box FAILED — payload kept at %s", payload)
            sys.exit(1)

    sys.exit(2 if failures else 0)


if __name__ == "__main__":
    main()
