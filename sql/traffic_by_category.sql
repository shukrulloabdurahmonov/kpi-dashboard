-- Per-category traffic metrics, monthly (crawler-filtered).
-- Uses per-category session metrics (session_mt1_pvs_cat / session_eq1_pvs_cat)
-- per ariadne Monthly_Traffic_MAU.sql — meaningful only per category, so this
-- spec never runs a 'total' dim (include_total=False in the registry).
SELECT {period_expr} AS period,
       '{dim_name}' AS dim_name,
       {dim_value} AS dim_value,
       COUNT(DISTINCT fac.session_mt1_pvs_cat) AS visits,
       COUNT(DISTINCT fac.session_eq1_pvs_cat) AS bounces_per_category,
       SUM(fac.num_pageviews) AS pageviews,
       COUNT(DISTINCT fac.session_mt1_pvs_cat)
         - COUNT(DISTINCT fac.session_eq1_pvs_cat) AS entering_visits
FROM eu_bi.fact_audience_categories fac
{dim_join}
WHERE fac.site_sk = %(site)s
  AND {date_expr} >= %(start)s
  AND {date_expr} < %(end)s
  AND fac.crawler_type IS NULL
GROUP BY {group_by}
