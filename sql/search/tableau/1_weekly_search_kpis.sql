WITH daily AS (
    SELECT
        u.date_day,
        u.country,
        u.platform,
        u.search_method,
        u.finance_category_l2,
        u.users_search,
        u.users_adview,
        u.users_lead,
        u.users_tgv,
        v.volume_search,
        v.volume_adview,
        v.volume_lead,
        v.volume_tgv
    FROM glue.odyn_search_and_ad_ranking.daily_search_users_kpis u
    JOIN glue.odyn_search_and_ad_ranking.daily_search_volume_kpis v
        ON  v.country = u.country
        AND v.platform = u.platform
        AND v.search_method = u.search_method
        AND v.finance_category_l2 = u.finance_category_l2
        AND v.date_day = u.date_day
    WHERE u.country = 'Uzbekistan'
      AND u.finance_category_l2 = 'All'
      AND u.date_day >= DATE '2024-12-30'
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
),
weekly AS (
    SELECT
        date_trunc('week', date_day)                                          AS week_start,
        country,
        platform,
        search_method,
        finance_category_l2,
        count(DISTINCT date_day)                                              AS days_in_week,
        sum(users_search)  / count(DISTINCT date_day)                         AS users_search,
        sum(users_adview)  / count(DISTINCT date_day)                         AS users_adview,
        sum(users_lead)    / count(DISTINCT date_day)                         AS users_lead,
        sum(users_tgv)     / count(DISTINCT date_day)                         AS users_tgv,
        sum(volume_search) / count(DISTINCT date_day)                         AS volume_search,
        sum(volume_adview) / count(DISTINCT date_day)                         AS volume_adview,
        sum(volume_lead)   / count(DISTINCT date_day)                         AS volume_lead,
        sum(volume_tgv)    / count(DISTINCT date_day)                         AS volume_tgv,
        round(avg(CAST(users_adview  AS double) / NULLIF(users_search, 0)), 5) AS ssu_adview,
        round(avg(CAST(users_lead    AS double) / NULLIF(users_search, 0)), 5) AS ssu_lead,
        round(avg(CAST(users_tgv     AS double) / NULLIF(users_search, 0)), 5) AS ssu_tgv,
        round(avg(CAST(volume_adview AS double) / NULLIF(users_search, 0)), 5) AS avg_adview_su,
        round(avg(CAST(volume_lead   AS double) / NULLIF(users_search, 0)), 5) AS avg_lead_su,
        round(avg(CAST(volume_tgv    AS double) / NULLIF(users_search, 0)), 5) AS avg_tgv_su,
        round(avg(daily_method_share), 5)                                     AS share_search_on_platform
    FROM daily_with_share
    GROUP BY 1, 2, 3, 4, 5
)
SELECT
    CAST(round(w.avg_adview_su, 5) AS decimal(38,17))                         AS "avg_adview_su",
    CAST(round(w.avg_lead_su,   5) AS decimal(38,17))                         AS "avg_lead_su",
    CAST(round(w.avg_tgv_su,    5) AS decimal(38,17))                         AS "avg_tgv_su",
    w.country                                                                 AS "country",
    w.days_in_week                                                            AS "days_in_week",
    w.finance_category_l2                                                     AS "finance_category_l2",
    CAST(round(w.ssu_adview, 5) AS decimal(38,17))                            AS "funnel_stage_adview_pct",
    CASE WHEN w.search_method = 'All' THEN NULL
         ELSE CAST(round(CAST(w.users_search AS double)
                         / NULLIF(wa.users_search, 0), 5) AS decimal(38,17))
    END                                                                       AS "funnel_stage_method_share_of_search",
    CASE WHEN w.search_method = 'All' THEN CAST(1 AS decimal(6,5)) END        AS "funnel_stage_platform_pct",
    CAST(round(w.ssu_lead, 5) AS decimal(38,17))                              AS "funnel_stage_reply_pct",
    CASE WHEN w.search_method = 'All'
         THEN CAST(1 AS decimal(38,17)) END                                   AS "funnel_stage_search_pct",
    w.platform                                                                AS "platform",
    w.search_method                                                           AS "search_method",
    CAST(round(w.share_search_on_platform, 5) AS decimal(38,17))              AS "share_search_on_platform",
    CAST(round(w.ssu_adview, 5) AS decimal(38,17))                            AS "ssu_adview",
    CAST(round(w.ssu_lead,   5) AS decimal(38,17))                            AS "ssu_lead",
    CAST(round(w.ssu_tgv,    5) AS decimal(38,17))                            AS "ssu_tgv",
    w.users_adview                                                            AS "users_adview",
    w.users_lead                                                              AS "users_lead",
    w.users_search                                                            AS "users_search",
    w.users_tgv                                                               AS "users_tgv",
    w.volume_adview                                                           AS "volume_adview",
    w.volume_lead                                                             AS "volume_lead",
    w.volume_search                                                           AS "volume_search",
    w.volume_tgv                                                              AS "volume_tgv",
    week(w.week_start)                                                        AS "week_index",
    'W' || lpad(CAST(week(w.week_start) AS varchar), 2, '0')                  AS "week_label",
    w.week_start                                                              AS "week_start",
    year_of_week(w.week_start)                                                AS "year"
FROM weekly w
JOIN weekly wa
    ON  wa.week_start = w.week_start
    AND wa.platform = w.platform
    AND wa.search_method = 'All'
