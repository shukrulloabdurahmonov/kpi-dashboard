"""Local persistent SQLite store for extracted metrics.

Same schema as the shipped snapshot: the snapshot is simply a backup copy
of this store plus a fresh snapshot_info stamp.
"""

import sqlite3
from datetime import datetime, timezone

DDL = """
CREATE TABLE IF NOT EXISTS metrics (
  metric    TEXT NOT NULL,
  grain     TEXT NOT NULL,
  period    TEXT NOT NULL,
  dim_name  TEXT NOT NULL DEFAULT 'total',
  dim_value TEXT NOT NULL DEFAULT '- Total -',
  value     REAL NOT NULL,
  PRIMARY KEY (metric, grain, period, dim_name, dim_value)
);
CREATE TABLE IF NOT EXISTS meta (
  metric           TEXT PRIMARY KEY,
  status           TEXT NOT NULL,
  extracted_at_utc TEXT,
  latest_period    TEXT,
  rows             INTEGER,
  error            TEXT
);
CREATE TABLE IF NOT EXISTS snapshot_info (
  key   TEXT PRIMARY KEY,
  value TEXT
);
CREATE TABLE IF NOT EXISTS category_tree (
  finance_l1  TEXT NOT NULL,
  finance_l2  TEXT NOT NULL,
  category_l1 TEXT NOT NULL,
  category_l2 TEXT NOT NULL,
  category_l3 TEXT NOT NULL,
  category_l4 TEXT NOT NULL,
  PRIMARY KEY (finance_l1, finance_l2, category_l1, category_l2, category_l3, category_l4)
);
CREATE TABLE IF NOT EXISTS search_keywords (
  platform    TEXT NOT NULL,
  keyword     TEXT NOT NULL,
  filtered    INTEGER NOT NULL,  -- 1 = narrowed searches, 0 = bare query
  searches    INTEGER NOT NULL,
  zsr         INTEGER NOT NULL,
  low         INTEGER NOT NULL,
  avg_results REAL,
  PRIMARY KEY (platform, keyword, filtered)
);
"""


def replace_category_tree(conn, rows):
    with conn:
        conn.execute("DELETE FROM category_tree")
        conn.executemany(
            "INSERT OR REPLACE INTO category_tree VALUES (?, ?, ?, ?, ?, ?)", rows)


def utcnow():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def open_store(path):
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.executescript(DDL)
    # migration: keywords went from one row per (platform, keyword) to one
    # per (platform, keyword, filtered). Old-shape tables are dropped and
    # recreated empty — the next payload merge repopulates them fully.
    cols = {r[1] for r in conn.execute("PRAGMA table_info(search_keywords)")}
    if cols and "filtered" not in cols:
        conn.execute("DROP TABLE search_keywords")
        conn.executescript(DDL)
    conn.commit()
    return conn


def replace_range(conn, metric_names, grain, dim_names, start_key, end_key, rows):
    """Atomically replace the given metrics' rows for the given dims within
    [start_key, end_key). Dim-scoped so two specs may own different dim
    slices of the same metric name (e.g. total vs per-category pageviews)."""
    with conn:
        m_marks = ",".join("?" * len(metric_names))
        d_marks = ",".join("?" * len(dim_names))
        conn.execute(
            "DELETE FROM metrics WHERE metric IN (%s) AND grain = ? "
            "AND dim_name IN (%s) AND period >= ? AND period < ?"
            % (m_marks, d_marks),
            list(metric_names) + [grain] + list(dim_names) + [start_key, end_key],
        )
        conn.executemany(
            "INSERT OR REPLACE INTO metrics "
            "(metric, grain, period, dim_name, dim_value, value) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            rows,
        )


def present_periods(conn, metric, grain, dim_name, start_key, end_key):
    cur = conn.execute(
        "SELECT DISTINCT period FROM metrics WHERE metric = ? AND grain = ? "
        "AND dim_name = ? AND period >= ? AND period < ?",
        (metric, grain, dim_name, start_key, end_key),
    )
    return {r[0] for r in cur.fetchall()}


def spec_stats(conn, metric_names, dim_names=None):
    """(row count, latest period) across all grains of the given metrics,
    optionally scoped to the dim slices a spec owns (shared metric names —
    e.g. 'pageviews' — are written by two specs at different dims).
    Daily/weekly keys ('YYYY-MM-DD') sort after the same month's 'YYYY-MM',
    so MAX(period) reflects the most granular freshness."""
    m_marks = ",".join("?" * len(metric_names))
    sql = "SELECT COUNT(*), MAX(period) FROM metrics WHERE metric IN (%s)" % m_marks
    params = list(metric_names)
    if dim_names:
        d_marks = ",".join("?" * len(dim_names))
        sql += " AND dim_name IN (%s)" % d_marks
        params += list(dim_names)
    n, latest = conn.execute(sql, params).fetchone()
    return n or 0, latest


def prune_old_periods(conn, grain, min_key, exempt_prefixes=("search_", "wbr_")):
    """Enforce the retention window: drop periods older than min_key at a
    grain (extraction only bounds what is ADDED; this bounds what is kept).
    Metrics under exempt_prefixes are owned by updater.search_merge /
    updater.wbr_merge, which manage their own history — the registry windows
    must not touch them."""
    clause = " AND ".join("metric NOT LIKE ?" for _ in exempt_prefixes)
    with conn:
        cur = conn.execute(
            "DELETE FROM metrics WHERE grain = ? AND period < ? AND " + clause,
            [grain, min_key] + [p + "%" for p in exempt_prefixes])
    return cur.rowcount


def replace_search_keywords(conn, rows, window_start, window_end):
    """Full replace of the top-keywords table (small, one 28d window)."""
    with conn:
        conn.execute("DELETE FROM search_keywords")
        conn.executemany(
            "INSERT OR REPLACE INTO search_keywords "
            "(platform, keyword, filtered, searches, zsr, low, avg_results) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
        conn.execute("INSERT OR REPLACE INTO snapshot_info (key, value) "
                     "VALUES ('search_kw_start', ?)", (window_start,))
        conn.execute("INSERT OR REPLACE INTO snapshot_info (key, value) "
                     "VALUES ('search_kw_end', ?)", (window_end,))


def prune_meta(conn, keep_names):
    """Drop meta rows for spec names that no longer exist in the registry."""
    qmarks = ",".join("?" * len(keep_names))
    with conn:
        conn.execute(
            "DELETE FROM meta WHERE metric NOT IN (%s)" % qmarks, list(keep_names))


def set_meta_ok(conn, spec_name, latest_period, rows):
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO meta "
            "(metric, status, extracted_at_utc, latest_period, rows, error) "
            "VALUES (?, 'ok', ?, ?, ?, NULL)",
            (spec_name, utcnow(), latest_period, rows),
        )


def set_meta_failed(conn, spec_name, error):
    """Mark a spec stale (has old data) or failed (never extracted),
    preserving the last successful extraction details."""
    with conn:
        cur = conn.execute(
            "SELECT extracted_at_utc, latest_period, rows FROM meta WHERE metric = ?",
            (spec_name,),
        )
        prev = cur.fetchone()
        status = "stale" if prev and prev[0] else "failed"
        conn.execute(
            "INSERT OR REPLACE INTO meta "
            "(metric, status, extracted_at_utc, latest_period, rows, error) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                spec_name, status,
                prev[0] if prev else None,
                prev[1] if prev else None,
                prev[2] if prev else None,
                str(error)[:500],
            ),
        )


def set_info(conn, key, value):
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO snapshot_info (key, value) VALUES (?, ?)",
            (key, value),
        )
