-- New users and confirmed new users, monthly. Source: ariadne "Users" file.
SELECT {period_expr} AS period,
       '{dim_name}' AS dim_name,
       {dim_value} AS dim_value,
       COUNT(DISTINCT CASE WHEN t.user_status NOT IN ('banned', 'deleted', 'pending_deletion')
                           THEN t.user_sk END) AS new_users,
       COUNT(DISTINCT CASE WHEN t.user_status = 'confirmed'
                           THEN t.user_sk END) AS confirmed_new_users
FROM eu_bi.dim_users t
{dim_join}
WHERE t.site_sk = %(site)s
  AND {date_expr} >= %(start)s
  AND {date_expr} < %(end)s
GROUP BY {group_by}
