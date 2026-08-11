-- Site-total traffic metrics, monthly (crawler-filtered).
-- Total bounces use session_eq1_pvs_sess — a whole-session metric that is
-- only meaningful at site level; per-category traffic lives in
-- traffic_by_category_monthly.sql. Runs with the 'total' dim only.
SELECT {period_expr} AS period,
       '{dim_name}' AS dim_name,
       {dim_value} AS dim_value,
       SUM(fac.num_pageviews) AS pageviews,
       COUNT(DISTINCT fac.session_eq1_pvs_sess) AS bounces
FROM eu_bi.fact_audience_categories fac
{dim_join}
WHERE fac.site_sk = %(site)s
  AND {date_expr} >= %(start)s
  AND {date_expr} < %(end)s
  AND fac.crawler_type IS NULL
GROUP BY {group_by}
