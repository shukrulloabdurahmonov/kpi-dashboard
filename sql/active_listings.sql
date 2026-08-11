-- Active listings + active listers, monthly (daily snapshots deduped per month).
-- Source: ariadne Active_Listings.sql (its missing-comma syntax bug fixed here).
-- Heaviest query in the set — one month per chunk.
SELECT {period_expr} AS period,
       '{dim_name}' AS dim_name,
       {dim_value} AS dim_value,
       COUNT(DISTINCT fal.listing_sk) AS active_listings,
       COUNT(DISTINCT fl.user_sk) AS active_listers
FROM eu_bi.fact_active_listings fal
LEFT JOIN eu_bi.fact_listings fl ON fl.listing_sk = fal.listing_sk
{dim_join}
WHERE fl.site_sk = %(site)s
  AND {date_expr} >= %(start)s
  AND {date_expr} < %(end)s
  AND fal.is_active = 1
GROUP BY {group_by}
