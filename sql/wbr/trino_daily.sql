-- WBR truth board, tteam Trino side: site-total (ALL) daily values from the
-- gold daily marts that feed iceberg.gold.olxuz_wbr_weekly.
-- Params (Python str.format, ISO dates): {start} inclusive, {end} exclusive.
select cast(metric_date as varchar) as d, t as metric, v as value from (
    select metric_date, 'nnl' as t, cast(nnl as double) as v from iceberg.gold.olxuz_nnl_daily where category_l1_ru = 'ALL'
    union all select metric_date, 'se', se from iceberg.gold.olxuz_se_daily where category_l1_ru = 'ALL'
    union all select metric_date, 'rd', replier_days from iceberg.gold.olxuz_replier_days_daily where category_l1_ru = 'ALL'
    union all select metric_date, 'rv', revenue_net from iceberg.gold.olxuz_revenue_daily where category_l1_ru = 'ALL'
    union all select metric_date, 'tr', n_transactions from iceberg.gold.olxuz_revenue_daily where category_l1_ru = 'ALL'
    union all select metric_date, 'py', n_payers from iceberg.gold.olxuz_revenue_daily where category_l1_ru = 'ALL'
    union all select metric_date, 'st', n_started from iceberg.gold.olxuz_started_react_daily where category_l1_ru = 'ALL'
    union all select metric_date, 're', n_react from iceberg.gold.olxuz_started_react_daily where category_l1_ru = 'ALL'
    union all select metric_date, 'vw', views from iceberg.gold.olxuz_views_daily where category_l1_ru = 'ALL'
    union all select metric_date, 'al', active_listings from iceberg.gold.olxuz_active_listings_daily where category_l1_ru = 'ALL'
    union all select metric_date, 'dau', dau_site from iceberg.gold.olxuz_dau_site_daily
) x
where metric_date >= date '{start}' and metric_date < date '{end}' and v is not null
