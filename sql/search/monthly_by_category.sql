-- Monthly search KPIs for OLX UZ by finance category L2 (Trino).
-- Adapted from uz_search_kpis/2_monthly_search_kpis_by_category.sql (tgv
-- columns dropped). CAVEAT preserved from the original: per-category ssu_*
-- ratios are shares of TOTAL search users (same-level 'All' denominator),
-- NOT of that category's searchers.
-- {start} = ISO date (first of month) injected by updater/search_extract.py.
WITH daily_kpis AS (
  SELECT
    CAST(u.date_day AS DATE) AS date_day,
    u.platform,
    u.search_method,
    u.finance_category_l2,
    u.users_search,
    u.users_adview,
    u.users_lead,
    v.volume_search,
    v.volume_adview,
    v.volume_lead
  FROM glue.odyn_search_and_ad_ranking.daily_search_users_kpis u
  INNER JOIN glue.odyn_search_and_ad_ranking.daily_search_volume_kpis v
    ON CAST(u.date_day AS DATE) = CAST(v.date_day AS DATE)
   AND u.country = v.country
   AND u.platform = v.platform
   AND u.search_method = v.search_method
   AND u.finance_category_l2 = v.finance_category_l2
  WHERE CAST(u.date_day AS DATE) >= DATE '{start}'
    AND u.country = 'Uzbekistan'
),
daily_with_denominators AS (
  SELECT
    *,
    DATE_TRUNC('month', date_day) AS month,
    MAX(CASE WHEN finance_category_l2 = 'All' THEN users_search END)
      OVER (PARTITION BY date_day, platform, search_method) AS denominator_same_level,
    MAX(CASE
          WHEN finance_category_l2 = 'All'
           AND platform = 'All'
           AND search_method = 'All'
          THEN users_search
        END)
      OVER (PARTITION BY date_day) AS denominator_aggregated
  FROM daily_kpis
),
daily_with_shares AS (
  SELECT
    *,
    1.00 * users_adview / NULLIF(COALESCE(denominator_same_level, denominator_aggregated), 0) AS daily_ssu_adview,
    1.00 * users_lead   / NULLIF(COALESCE(denominator_same_level, denominator_aggregated), 0) AS daily_ssu_lead
  FROM daily_with_denominators
  WHERE volume_search > 0 OR users_search > 0
)
SELECT
  month,
  platform,
  search_method,
  finance_category_l2,
  CAST(SUM(users_search)  / COUNT(DISTINCT date_day) AS BIGINT) AS users_search,
  CAST(SUM(users_adview)  / COUNT(DISTINCT date_day) AS BIGINT) AS users_adview,
  CAST(SUM(users_lead)    / COUNT(DISTINCT date_day) AS BIGINT) AS users_lead,
  CAST(SUM(volume_search) / COUNT(DISTINCT date_day) AS BIGINT) AS volume_search,
  CAST(SUM(volume_adview) / COUNT(DISTINCT date_day) AS BIGINT) AS volume_adview,
  CAST(SUM(volume_lead)   / COUNT(DISTINCT date_day) AS BIGINT) AS volume_lead,
  ROUND(AVG(daily_ssu_adview), 5) AS ssu_adview,
  ROUND(AVG(daily_ssu_lead), 5)   AS ssu_lead
FROM daily_with_shares
GROUP BY 1, 2, 3, 4
