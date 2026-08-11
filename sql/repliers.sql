-- Unique repliers (buyers, SESSION-based — includes anonymous
-- callers/phone-reveals) + successful reply events.
-- Source: ariadne Unique_Replies_Repliers.sql. The warehouse renamed
-- fact_replies_success to fact_replies_success_legacy (verified fresh);
-- definition unchanged. Restricted to one site.
SELECT {period_expr} AS period,
       '{dim_name}' AS dim_name,
       {dim_value} AS dim_value,
       COUNT(DISTINCT fr.session_long_sk) AS unique_repliers,
       COUNT(*) AS replies
FROM eu_bi.fact_replies_success_legacy fr
{dim_join}
WHERE fr.site_sk = %(site)s
  AND {date_expr} >= %(start)s
  AND {date_expr} < %(end)s
GROUP BY {group_by}
