-- Insertions (all / renewed / free), monthly. Source: ariadne Insertions.sql.
-- fact_listings joined for category/region dims; seller dim joins on fii.user_sk.
SELECT {period_expr} AS period,
       '{dim_name}' AS dim_name,
       {dim_value} AS dim_value,
       COUNT(fii.listing_sk) AS insertions_all,
       SUM(fii.is_renewal) AS insertions_renewed,
       SUM(fii.is_free) AS insertions_free
FROM eu_bi.fact_listings_insertions fii
LEFT JOIN eu_bi.fact_listings fl ON fl.listing_sk = fii.listing_sk
{dim_join}
WHERE fii.site_sk = %(site)s
  AND {date_expr} >= %(start)s
  AND {date_expr} < %(end)s
  AND fii.is_removed = 0
GROUP BY {group_by}
