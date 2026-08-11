-- Monthly Active Users, NEW methodology (April 2026):
-- COUNT(DISTINCT session_long_sk) WHERE applicable_to_active_users AND NOT is_outlier.
-- Matches Cube Unload/unload_active_users_agg.py. One month per chunk.
-- No geography on fact_audience_categories → no region slice.
SELECT {period_expr} AS period,
       '{dim_name}' AS dim_name,
       {dim_value} AS dim_value,
       COUNT(DISTINCT fac.session_long_sk) AS {value_col}
FROM eu_bi.fact_audience_categories fac
{dim_join}
WHERE fac.site_sk = %(site)s
  AND {date_expr} >= %(start)s
  AND {date_expr} < %(end)s
  AND fac.applicable_to_active_users IS TRUE
  AND fac.is_outlier IS NOT TRUE
GROUP BY {group_by}
