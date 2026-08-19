-- Weekly search funnel KPIs for OLX UZ (Trino, presto.data.olx.org).
-- Adapted from uz_search_kpis/1_weekly_search_kpis.sql: drops the always-0
-- *_tgv columns and the Tableau funnel_stage_* casts (reshaped in Python).
-- users_*/volume_* are AVERAGE DAILY values; ssu_*/avg_*_su are unweighted
-- averages of daily ratios. share_search_on_platform = the method's share of
-- the platform's search users (1.0 on the method='All' rows).
-- {start} = ISO date (Monday) injected by updater/search_extract.py.
WITH daily AS (
    SELECT
        u.date_day,
        u.platform,
        u.search_method,
        u.users_search,
        u.users_adview,
        u.users_lead,
        v.volume_search,
        v.volume_adview,
        v.volume_lead
    FROM glue.odyn_search_and_ad_ranking.daily_search_users_kpis u
    JOIN glue.odyn_search_and_ad_ranking.daily_search_volume_kpis v
        ON  v.country = u.country
        AND v.platform = u.platform
        AND v.search_method = u.search_method
        AND v.finance_category_l2 = u.finance_category_l2
        AND v.date_day = u.date_day
    WHERE u.country = 'Uzbekistan'
      AND u.finance_category_l2 = 'All'
      AND u.date_day >= DATE '{start}'
),
daily_with_share AS (
    SELECT
        d.*,
        CAST(d.users_search AS double) / NULLIF(a.users_search, 0) AS daily_method_share
    FROM daily d
    JOIN daily a
        ON  a.date_day = d.date_day
        AND a.platform = d.platform
        AND a.search_method = 'All'
)
SELECT
    date_trunc('week', date_day)                                           AS week_start,
    platform,
    search_method,
    count(DISTINCT date_day)                                               AS days_in_week,
    sum(users_search)  / count(DISTINCT date_day)                          AS users_search,
    sum(users_adview)  / count(DISTINCT date_day)                          AS users_adview,
    sum(users_lead)    / count(DISTINCT date_day)                          AS users_lead,
    sum(volume_search) / count(DISTINCT date_day)                          AS volume_search,
    sum(volume_adview) / count(DISTINCT date_day)                          AS volume_adview,
    sum(volume_lead)   / count(DISTINCT date_day)                          AS volume_lead,
    round(avg(CAST(users_adview  AS double) / NULLIF(users_search, 0)), 5) AS ssu_adview,
    round(avg(CAST(users_lead    AS double) / NULLIF(users_search, 0)), 5) AS ssu_lead,
    round(avg(CAST(volume_adview AS double) / NULLIF(users_search, 0)), 5) AS avg_adview_su,
    round(avg(CAST(volume_lead   AS double) / NULLIF(users_search, 0)), 5) AS avg_lead_su,
    round(avg(daily_method_share), 5)                                      AS share_search_on_platform
FROM daily_with_share
GROUP BY 1, 2, 3
