-- WBR truth board, tteam Trino side: week-distinct repliers (what the WBR divides SE by).
select cast(week_start as varchar) as wk, cast(replier_days as double) as value
from iceberg.gold.olxuz_repliers_weekly
where category_l1_ru = 'ALL' and week_start >= date '{start}' and week_start < date '{end}'
