-- First-time listers success (14 days / 3 replies), monthly posting cohorts.
-- Source: ariadne "FIRST TIME LISTERS SUCCESS". Registry caps %(end)s to
-- 14d-mature cohorts.
SELECT {period_expr} AS period,
       '{dim_name}' AS dim_name,
       {dim_value} AS dim_value,
       COUNT(a.listing_sk) AS ftl_success_listings_14d_3r,
       COUNT(DISTINCT b.user_sk) AS ftl_success_listers_14d_3r
FROM eu_bi.fact_listings_liquidity_success a
JOIN eu_bi.fact_listings b ON a.listing_sk = b.listing_sk
JOIN eu_bi.fact_listings_user_segments c
  ON a.listing_sk = c.listing_sk AND a.first_active_date_nk = c.first_active_date_nk
{dim_join}
WHERE b.site_sk = %(site)s
  AND b.listing_net_sk = 'net'
  AND a.num_replies_2wk > 2
  AND c.user_first_time_returning_nk = 'first_time'
  AND {date_expr} >= %(start)s
  AND {date_expr} < %(end)s
GROUP BY {group_by}
