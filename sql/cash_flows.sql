-- Cash-flow operations, monthly. Source: ariadne Financial_queries.sql.
SELECT {period_expr} AS period,
       '{dim_name}' AS dim_name,
       {dim_value} AS dim_value,
       COUNT(DISTINCT fp.payment_sk) AS cash_flows,
       COUNT(DISTINCT fp.user_sk) AS cash_flow_payers
FROM eu_bi.fact_payments fp
{dim_join}
WHERE fp.site_sk = %(site)s
  AND fp.cash_value_gross != 0
  AND {date_expr} >= %(start)s
  AND {date_expr} < %(end)s
GROUP BY {group_by}
