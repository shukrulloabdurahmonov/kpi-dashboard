-- First-page keyword SERP views by day × region for OLX UZ, one platform per
-- run (yamato Redshift, hydra clickstream). Pattern proven in
-- Scripts/zsr_dashboard/zsr_build.py. zsr = 0 results; low = 1-10 results;
-- result_count >= {sentinel} is an app sentinel, excluded. Bot filter is the
-- colibri house standard. CAVEAT: web/iOS auto-extend empty searches, so
-- their zsr is near zero — only Android reports true zeros. Never blend
-- platform rates. UZ hydra retention starts 2025-07-02.
-- Placeholders filled by updater/search_extract.py (dates/idents it builds).
SELECT e.server_date_day::date AS day,
       '{platform}' AS platform,
       e.region_id,
       COUNT(*) AS searches,
       SUM(CASE WHEN e.result_count = 0 THEN 1 ELSE 0 END) AS zsr,
       SUM(CASE WHEN e.result_count BETWEEN {low_min} AND {low_max} THEN 1 ELSE 0 END) AS low
FROM hydra.{table} e
LEFT JOIN eu_bi.map_ip_blacklist bl ON e.ip_address = bl.crawler_ip_hash
WHERE e.country_code = 'UZ'
  AND e.server_date_day BETWEEN '{d1}' AND '{d2}'
  AND {serp_predicate}
  AND COALESCE(e.page_nb, 1) = 1
  AND e.result_count IS NOT NULL
  AND e.result_count < {sentinel}
{bot_filter}
GROUP BY 1, 2, 3
