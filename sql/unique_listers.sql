-- Monthly Unique Listers (MUL). Source: ariadne Unique_Listers.sql
-- (net listings, period = COALESCE(first_active_date_local::DATE, date_posted_nk)).
SELECT {period_expr} AS period,
       '{dim_name}' AS dim_name,
       {dim_value} AS dim_value,
       COUNT(DISTINCT fl.user_sk) AS unique_listers
FROM eu_bi.fact_listings fl
{dim_join}
WHERE fl.site_sk = %(site)s
  AND fl.listing_net_sk = 'net'
  AND {date_expr} >= %(start)s
  AND {date_expr} < %(end)s
GROUP BY {group_by}
