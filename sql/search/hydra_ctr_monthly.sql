-- Result-page views and ad clicks for CTR@1/3/40, monthly, one platform per
-- run (yamato, hydra clickstream). Two result sets stitched by row_kind:
--   serps  — first-page listing views, split Search (typed keyword) vs
--            Navigation (category browsing, no keyword)
--   clicks — ad_click events on the listing page, same mode split, with
--            position buckets (<=1, <=3, <=40; 40 ~ one page)
-- CTR@N is computed downstream as clicks_pN / serps per mode — an
-- event-level rate ("ad clicks per result-page view"), not a per-session
-- deduplicated rate. Same bot filter as every other hydra query.
SELECT month, mode, 'serps' AS row_kind,
       COUNT(*) AS n, 0 AS p1, 0 AS p3, 0 AS p40
FROM (
    SELECT DATE_TRUNC('month', e.server_date_day)::date AS month,
           CASE WHEN COALESCE(e.keyword, '') NOT IN ('', 'No keyword')
                THEN 'Search' ELSE 'Navigation' END AS mode
    FROM hydra.{table} e
    LEFT JOIN eu_bi.map_ip_blacklist bl ON e.ip_address = bl.crawler_ip_hash
    WHERE e.country_code = 'UZ'
      AND e.server_date_day BETWEEN '{d1}' AND '{d2}'
      AND {listing_predicate}
      AND COALESCE(e.page_nb, 1) = 1
    {bot_filter}
)
GROUP BY 1, 2
UNION ALL
SELECT month, mode, 'clicks' AS row_kind,
       COUNT(*) AS n,
       SUM(CASE WHEN pos <= 1 THEN 1 ELSE 0 END) AS p1,
       SUM(CASE WHEN pos <= 3 THEN 1 ELSE 0 END) AS p3,
       SUM(CASE WHEN pos <= 40 THEN 1 ELSE 0 END) AS p40
FROM (
    SELECT DATE_TRUNC('month', e.server_date_day)::date AS month,
           CASE WHEN COALESCE(e.keyword, '') NOT IN ('', 'No keyword')
                THEN 'Search' ELSE 'Navigation' END AS mode,
           e.ad_position AS pos
    FROM hydra.{table} e
    LEFT JOIN eu_bi.map_ip_blacklist bl ON e.ip_address = bl.crawler_ip_hash
    WHERE e.country_code = 'UZ'
      AND e.server_date_day BETWEEN '{d1}' AND '{d2}'
      AND e.eventname = 'ad_click'
      AND e.trackpage = 'listing'
    {bot_filter}
)
GROUP BY 1, 2
