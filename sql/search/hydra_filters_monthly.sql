-- How keyword searches are narrowed (OLX UZ, one platform per run):
-- filters_count counts ALL narrowing criteria on the search — category
-- selection, region, price band, and named attribute filters (rooms, year,
-- condition, ...). Depth is capped at 3+ for display. The w_* flags count
-- searches using each criterion TYPE (overlapping — one search can use
-- several). Same SERP definition/bot filter as the other hydra queries.
-- CASTs guard against dtype drift between the three platform tables.
SELECT DATE_TRUNC('month', e.server_date_day)::date AS month,
       '{platform}' AS platform,
       LEAST(COALESCE(e.filters_count, 0), 3) AS depth,
       COUNT(*) AS searches,
       AVG(e.result_count) AS avg_results,
       SUM(CASE WHEN NULLIF(CAST(e.cat_l1_id AS varchar), '') IS NOT NULL
                 AND CAST(e.cat_l1_id AS varchar) <> '0' THEN 1 ELSE 0 END) AS w_category,
       SUM(CASE WHEN NULLIF(CAST(e.region_id AS varchar), '') IS NOT NULL
                 AND CAST(e.region_id AS varchar) <> '0' THEN 1 ELSE 0 END) AS w_region,
       SUM(CASE WHEN NULLIF(CAST(e.{price_from} AS varchar), '') IS NOT NULL
                 OR NULLIF(CAST(e.{price_to} AS varchar), '') IS NOT NULL THEN 1 ELSE 0 END) AS w_price,
       SUM(CASE WHEN NULLIF(CAST(e.filters AS varchar), '') IS NOT NULL THEN 1 ELSE 0 END) AS w_attr
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
