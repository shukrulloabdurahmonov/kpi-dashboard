-- Liquidity 7d by daily posting cohort (1-reply and 3-replies variants).
-- Only run for cohort days at least 8 days old (registry caps %(end)s).
SELECT {period_expr} AS period,
       '{dim_name}' AS dim_name,
       {dim_value} AS dim_value,
       COUNT(CASE WHEN a.num_replies_wk > 0 THEN a.listing_sk END) AS liquid_listings_7d_1r,
       COUNT(DISTINCT CASE WHEN a.num_replies_wk > 0 THEN b.user_sk END) AS liquid_listers_7d_1r,
       COUNT(CASE WHEN a.num_replies_wk > 2 THEN a.listing_sk END) AS liquid_listings_7d_3r
FROM eu_bi.fact_listings_liquidity_success a
JOIN eu_bi.fact_listings b ON a.listing_sk = b.listing_sk
{dim_join}
WHERE b.site_sk = %(site)s
  AND b.listing_net_sk = 'net'
  AND {date_expr} >= %(start)s
  AND {date_expr} < %(end)s
GROUP BY {group_by}
