-- Ad impressions + ad views, monthly. Source: ariadne Ad_impressions
-- (which had no site filter — added here).
SELECT {period_expr} AS period,
       '{dim_name}' AS dim_name,
       {dim_value} AS dim_value,
       SUM(t.num_impressions) AS ad_impressions,
       SUM(t.num_ad_page) AS ad_views
FROM eu_bi.fact_listings_traffic_agg t
{dim_join}
WHERE t.site_sk = %(site)s
  AND {date_expr} >= %(start)s
  AND {date_expr} < %(end)s
GROUP BY {group_by}
