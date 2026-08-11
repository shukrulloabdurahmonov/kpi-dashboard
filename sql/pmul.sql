-- Paying Monthly Unique Listers (PMUL).
-- Source: ariadne Paying_Monthly_Unique_Listers.sql.
SELECT {period_expr} AS period,
       '{dim_name}' AS dim_name,
       {dim_value} AS dim_value,
       COUNT(DISTINCT fp.user_sk) AS pmul
FROM eu_bi.fact_payments fp
LEFT JOIN eu_bi.dim_products pdc ON pdc.product_sk = fp.product_sk
{dim_join}
WHERE fp.site_sk = %(site)s
  AND pdc.revenue_stream != 'Not Revenue'
  AND fp.trans_value_net != 0
  AND {date_expr} >= %(start)s
  AND {date_expr} < %(end)s
GROUP BY {group_by}
