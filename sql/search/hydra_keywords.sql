-- Top search keywords by supply gap for OLX UZ, one platform per run
-- (yamato Redshift, hydra clickstream), 28-day window. Pattern proven in
-- Scripts/zsr_dashboard/zsr_build.py, but ranked by search volume (the ZSR
-- dashboard already owns the supply-gap ranking); zsr/low/avg_results ride
-- along as columns. Placeholders filled by updater/search_extract.py.
SELECT '{platform}' AS platform,
       LOWER(BTRIM(e.keyword)) AS keyword,
       COUNT(*) AS searches,
       SUM(CASE WHEN e.result_count = 0 THEN 1 ELSE 0 END) AS zsr,
       SUM(CASE WHEN e.result_count BETWEEN {low_min} AND {low_max} THEN 1 ELSE 0 END) AS low,
       AVG(e.result_count) AS avg_results
FROM hydra.{table} e
LEFT JOIN eu_bi.map_ip_blacklist bl ON e.ip_address = bl.crawler_ip_hash
WHERE e.country_code = 'UZ'
  AND e.server_date_day BETWEEN '{d1}' AND '{d2}'
  AND {serp_predicate}
  AND COALESCE(e.page_nb, 1) = 1
  AND e.result_count IS NOT NULL
  AND e.result_count < {sentinel}
{bot_filter}
GROUP BY 1, 2
HAVING COUNT(*) >= {min_searches}
ORDER BY COUNT(*) DESC
LIMIT {top_n}
