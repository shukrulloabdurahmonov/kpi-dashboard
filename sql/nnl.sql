-- Net New Listings (NNL), monthly. Source: ariadne Net_New_Listings.sql
-- (listing_net_sk = 'net' on first_active_date_nk).
-- Template: run once per dim; every slice is its own exact aggregate.
SELECT {period_expr} AS period,
       '{dim_name}' AS dim_name,
       {dim_value} AS dim_value,
       COUNT(DISTINCT fl.listing_sk) AS nnl
FROM eu_bi.fact_listings fl
{dim_join}
WHERE fl.site_sk = %(site)s
  AND fl.listing_net_sk = 'net'
  AND {date_expr} >= %(start)s
  AND {date_expr} < %(end)s
GROUP BY {group_by}
