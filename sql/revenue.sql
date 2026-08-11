-- Financial metrics, monthly, local currency (UZS), 'Not Revenue' excluded.
-- Source: ariadne Financial_queries.sql. dim_products (pdc) always joined —
-- the WHERE needs it and the revenue_stream dim reads from it.
SELECT {period_expr} AS period,
       '{dim_name}' AS dim_name,
       {dim_value} AS dim_value,
       SUM(fp.trans_value_gross) AS revenue_gross,
       SUM(fp.trans_value_net) AS revenue_net,
       SUM(fp.trans_value_service_fee) AS service_fee,
       SUM(fp.trans_value_tax) AS tax,
       SUM(fp.bonus_value_gross) AS bonus_gross,
       SUM(fp.refund_value_gross) AS refund_gross,
       COUNT(DISTINCT fp.transaction_sk) AS transactions,
       COUNT(DISTINCT fp.payment_sk) AS payments
FROM eu_bi.fact_payments fp
LEFT JOIN eu_bi.dim_products pdc ON pdc.product_sk = fp.product_sk
{dim_join}
WHERE fp.site_sk = %(site)s
  AND pdc.revenue_stream != 'Not Revenue'
  AND fp.trans_value_net != 0
  AND {date_expr} >= %(start)s
  AND {date_expr} < %(end)s
GROUP BY {group_by}
