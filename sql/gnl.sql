-- Gross New Listings (GNL), monthly — NNL with the 'net' filter switched off.
-- Period per ariadne: COALESCE(first_active_date_local::DATE, date_posted_nk).
SELECT {period_expr} AS period,
       '{dim_name}' AS dim_name,
       {dim_value} AS dim_value,
       COUNT(DISTINCT fl.listing_sk) AS gnl
FROM eu_bi.fact_listings fl
{dim_join}
WHERE fl.site_sk = %(site)s
  AND {date_expr} >= %(start)s
  AND {date_expr} < %(end)s
GROUP BY {group_by}
