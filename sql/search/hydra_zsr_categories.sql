-- Zero-result / low-supply split by category L1 for OLX UZ, monthly, one
-- platform per run (yamato, hydra clickstream). Category names via
-- eu_bi.dim_categories (zsr_dashboard pattern); unknown ids fall out as ids.
-- Same SERP definition / bot filter as the other hydra queries.
SELECT DATE_TRUNC('month', e.server_date_day)::date AS month,
       '{platform}' AS platform,
       COALESCE(c.category_l1_name_lc, CAST(e.cat_l1_id AS varchar), 'unknown') AS category,
       COUNT(*) AS searches,
       SUM(CASE WHEN e.result_count = 0 THEN 1 ELSE 0 END) AS zsr,
       SUM(CASE WHEN e.result_count BETWEEN {low_min} AND {low_max} THEN 1 ELSE 0 END) AS low
FROM hydra.{table} e
LEFT JOIN eu_bi.map_ip_blacklist bl ON e.ip_address = bl.crawler_ip_hash
LEFT JOIN (SELECT DISTINCT category_l1_nk, category_l1_name_lc
           FROM eu_bi.dim_categories
           WHERE site_sk = 'olx|eu|uz' AND category_l1_nk <> 'unknown') c
       ON CAST(e.cat_l1_id AS varchar) = c.category_l1_nk
WHERE e.country_code = 'UZ'
  AND e.server_date_day BETWEEN '{d1}' AND '{d2}'
  AND {serp_predicate}
  AND COALESCE(e.page_nb, 1) = 1
  AND e.result_count IS NOT NULL
  AND e.result_count < {sentinel}
{bot_filter}
GROUP BY 1, 2, 3
