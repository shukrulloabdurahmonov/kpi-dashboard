-- Query Success Rate + Likes Card Rate inputs, one platform per run
-- (yamato, hydra clickstream). Distinct-count metrics, so each grain is
-- grouped natively via {period_expr} (day or month) — never summed from
-- finer grains. Two result sets stitched by row_kind:
--   queries  — distinct keyword searches (search_id, 100% populated on
--              SERP views) and how many of them got >=1 SERP ad click
--              (clicks attributed via search_id; only ~2/3 of clicks carry
--              it, so 'hit' — and QSR — is a floor, not an exact value)
--   sessions — distinct sessions (session_long) that ran >=1 keyword
--              search, and how many of them saved >=1 ad to favourites
--              (favourite_ad_click anywhere in the session, same period)
-- Same SERP definition / bot filter as every other hydra query.
WITH serps AS (
    SELECT {period_expr} AS period,
           NULLIF(CAST(e.search_id AS varchar), '') AS sid,
           NULLIF(CAST(e.session_long AS varchar), '') AS sess
    FROM hydra.{table} e
    LEFT JOIN eu_bi.map_ip_blacklist bl ON e.ip_address = bl.crawler_ip_hash
    WHERE e.country_code = 'UZ'
      AND e.server_date_day BETWEEN '{d1}' AND '{d2}'
      AND {serp_predicate}
      AND COALESCE(e.page_nb, 1) = 1
      AND e.result_count IS NOT NULL
      AND e.result_count < {sentinel}
    {bot_filter}
),
clicks AS (
    SELECT DISTINCT NULLIF(CAST(e.search_id AS varchar), '') AS sid
    FROM hydra.{table} e
    LEFT JOIN eu_bi.map_ip_blacklist bl ON e.ip_address = bl.crawler_ip_hash
    WHERE e.country_code = 'UZ'
      AND e.server_date_day BETWEEN '{d1}' AND '{d2}'
      AND e.eventname = 'ad_click'
      AND e.ad_position IS NOT NULL
      AND NULLIF(CAST(e.search_id AS varchar), '') IS NOT NULL
    {bot_filter}
),
likes AS (
    SELECT DISTINCT {period_expr} AS period,
           NULLIF(CAST(e.session_long AS varchar), '') AS sess
    FROM hydra.{table} e
    LEFT JOIN eu_bi.map_ip_blacklist bl ON e.ip_address = bl.crawler_ip_hash
    WHERE e.country_code = 'UZ'
      AND e.server_date_day BETWEEN '{d1}' AND '{d2}'
      AND e.eventname = 'favourite_ad_click'
    {bot_filter}
)
SELECT s.period AS period, 'queries' AS row_kind,
       COUNT(DISTINCT s.sid) AS total,
       COUNT(DISTINCT CASE WHEN c.sid IS NOT NULL THEN s.sid END) AS hit
FROM serps s
LEFT JOIN clicks c ON c.sid = s.sid
GROUP BY 1
UNION ALL
SELECT s.period AS period, 'sessions' AS row_kind,
       COUNT(DISTINCT s.sess) AS total,
       COUNT(DISTINCT CASE WHEN l.sess IS NOT NULL THEN s.sess END) AS hit
FROM (SELECT DISTINCT period, sess FROM serps) s
LEFT JOIN likes l ON l.sess = s.sess AND l.period = s.period
GROUP BY 1
