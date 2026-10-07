-- WBR truth board, Yamato side: week-distinct sessions with a successful reply.
select date_trunc('week', date_sent_nk)::date::varchar as wk, count(distinct session_long_sk) as value
from eu_bi.fact_replies_success_legacy
where site_sk = %(site)s and date_sent_nk >= %(start)s and date_sent_nk < %(end)s
group by 1
