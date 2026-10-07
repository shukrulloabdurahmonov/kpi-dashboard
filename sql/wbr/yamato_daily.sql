-- WBR truth board, Yamato side: daily values under the same definitions as
-- the tteam marts. One statement per metric, separated by ';;' and named by
-- the first-line comment "-- metric: <keys>". Params: %(site)s, %(start)s
-- inclusive, %(end)s exclusive.

-- metric: nnl
select first_active_date_nk::varchar as d, count(distinct listing_sk) as nnl
from eu_bi.fact_listings
where site_sk = %(site)s and listing_net_sk = 'net'
  and first_active_date_nk >= %(start)s and first_active_date_nk < %(end)s
group by 1
;;
-- metric: st re
select date_nk::varchar as d, sum(new_insertions) as st, sum(renewal_insertions) as re
from cubes.fact_listings_insertions_cube
where site_sk = %(site)s and date_nk >= %(start)s and date_nk < %(end)s
group by 1
;;
-- metric: se rd
select date_sent_nk::varchar as d, count(*) as se, count(distinct session_long_sk) as rd
from eu_bi.fact_replies_success_legacy
where site_sk = %(site)s and date_sent_nk >= %(start)s and date_sent_nk < %(end)s
group by 1
;;
-- metric: rv tr
select p.payment_date::varchar as d, sum(p.trans_value_net) as rv, count(distinct p.transaction_sk) as tr
from eu_bi.fact_payments p
join eu_bi.dim_products dp on p.site_sk = dp.site_sk and p.product_sk = dp.product_sk
where p.site_sk = %(site)s and p.payment_date >= %(start)s and p.payment_date < %(end)s
  and dp.revenue_stream <> 'Not Revenue' and p.trans_value_net <> 0
group by 1
;;
-- metric: py
-- payers = sum over day x L1 x product of distinct users (tteam olxuz_revenue_daily since 2026-10-02)
select d, sum(c) as py from (
  select p.payment_date::varchar as d, split_part(p.category_sk, '|', 4) as l1, p.product_nk, count(distinct p.user_sk) as c
  from eu_bi.fact_payments p
  join eu_bi.dim_products dp on p.site_sk = dp.site_sk and p.product_sk = dp.product_sk
  where p.site_sk = %(site)s and p.payment_date >= %(start)s and p.payment_date < %(end)s
    and dp.revenue_stream <> 'Not Revenue' and p.trans_value_net <> 0
  group by 1, 2, 3
) x group by 1
;;
-- metric: al
select date_nk::varchar as d, count(distinct listing_sk) as al
from eu_bi.fact_active_listings
where site_sk = %(site)s and date_nk >= %(start)s and date_nk < %(end)s
group by 1
;;
-- metric: dau
select date_event_local::varchar as d,
       count(distinct case when applicable_to_active_users and not is_outlier then session_long_sk end) as dau
from eu_bi.fact_audience_categories
where site_sk = %(site)s and date_event_local >= %(start)s and date_event_local < %(end)s
group by 1
;;
-- metric: vw
select date_sent_nk::varchar as d, count(*) as vw from (
  select distinct date_sent_nk, listing_sk, session_long_sk
  from eu_bi.fact_listings_traffic
  where site_sk = %(site)s and date_sent_nk >= %(start)s and date_sent_nk < %(end)s
    and action_sk like 'pv|ad_page|%%'
) x group by 1
