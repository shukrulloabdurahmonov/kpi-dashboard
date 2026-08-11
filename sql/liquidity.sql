-- Liquidity by posting cohort month, all window/threshold variants in one scan.
-- Source: ariadne Liquidity_7_days.sql; cohorts keyed on first_active_date_nk
-- (the library's date_posted_nk no longer exists on this table).
-- Only run for months whose last day is 28d-mature (registry caps %(end)s).
SELECT {period_expr} AS period,
       '{dim_name}' AS dim_name,
       {dim_value} AS dim_value,
       COUNT(CASE WHEN a.num_replies_wk > 0 THEN a.listing_sk END) AS liquid_listings_7d_1r,
       COUNT(DISTINCT CASE WHEN a.num_replies_wk > 0 THEN b.user_sk END) AS liquid_listers_7d_1r,
       COUNT(CASE WHEN a.num_replies_wk > 2 THEN a.listing_sk END) AS liquid_listings_7d_3r,
       COUNT(DISTINCT CASE WHEN a.num_replies_wk > 2 THEN b.user_sk END) AS liquid_listers_7d_3r,
       COUNT(CASE WHEN a.num_replies_2wk > 0 THEN a.listing_sk END) AS liquid_listings_14d_1r,
       COUNT(DISTINCT CASE WHEN a.num_replies_2wk > 0 THEN b.user_sk END) AS liquid_listers_14d_1r,
       COUNT(CASE WHEN a.num_replies_2wk > 2 THEN a.listing_sk END) AS liquid_listings_14d_3r,
       COUNT(DISTINCT CASE WHEN a.num_replies_2wk > 2 THEN b.user_sk END) AS liquid_listers_14d_3r,
       COUNT(CASE WHEN a.num_replies_4wk > 0 THEN a.listing_sk END) AS liquid_listings_28d_1r,
       COUNT(DISTINCT CASE WHEN a.num_replies_4wk > 0 THEN b.user_sk END) AS liquid_listers_28d_1r,
       COUNT(CASE WHEN a.num_replies_4wk > 2 THEN a.listing_sk END) AS liquid_listings_28d_3r,
       COUNT(DISTINCT CASE WHEN a.num_replies_4wk > 2 THEN b.user_sk END) AS liquid_listers_28d_3r
FROM eu_bi.fact_listings_liquidity_success a
JOIN eu_bi.fact_listings b ON a.listing_sk = b.listing_sk
{dim_join}
WHERE b.site_sk = %(site)s
  AND b.listing_net_sk = 'net'
  AND {date_expr} >= %(start)s
  AND {date_expr} < %(end)s
GROUP BY {group_by}
