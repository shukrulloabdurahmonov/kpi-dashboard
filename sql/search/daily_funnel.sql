-- Daily search funnel KPIs for OLX UZ (Trino) — the same measures as
-- weekly_funnel.sql at day grain (the source tables are daily; the weekly
-- file just rolls them up). Ratios are that day's ratio; share is the
-- method's share of the platform's search users that day.
-- {start} = ISO date injected by updater/search_extract.py (90d window).
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
)
SELECT
    d.date_day                                                             AS period_day,
    d.platform,
    d.search_method,
    d.users_search,
    d.users_adview,
    d.users_lead,
    d.volume_search,
    d.volume_adview,
    d.volume_lead,
    round(CAST(d.users_adview  AS double) / NULLIF(d.users_search, 0), 5)  AS ssu_adview,
    round(CAST(d.users_lead    AS double) / NULLIF(d.users_search, 0), 5)  AS ssu_lead,
    round(CAST(d.volume_adview AS double) / NULLIF(d.users_search, 0), 5)  AS avg_adview_su,
    round(CAST(d.volume_lead   AS double) / NULLIF(d.users_search, 0), 5)  AS avg_lead_su,
    round(CAST(d.users_search  AS double) / NULLIF(a.users_search, 0), 5)  AS share_search_on_platform
FROM daily d
JOIN daily a
    ON  a.date_day = d.date_day
    AND a.platform = d.platform
    AND a.search_method = 'All'
