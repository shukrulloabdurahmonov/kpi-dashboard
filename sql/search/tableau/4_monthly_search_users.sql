-- Monthly average daily search users for OLX UZ (simple rollup).
-- Tableau custom SQL. daily_search_users_kpis stores full country names.
select
  date_trunc('month', date_day) AS month
  , country
  , platform
  , search_method
  , finance_category_l2
  , cast(avg(users_search) as bigint) as users_search
  , cast(avg(users_adview) as bigint) as users_adview
  , cast(avg(users_lead) as bigint) as users_lead
  , cast(avg(users_tgv) as bigint) as users_tgv
from glue.odyn_search_and_ad_ranking.daily_search_users_kpis
where date_day >= date('2025-01-01')
  and country = 'Uzbekistan'
group by 1, 2, 3, 4, 5
