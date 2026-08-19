-- Monthly search volume for OLX UZ (Trino) — TRUE event totals by category,
-- the treemap/heatmap source. Same shape as daily_by_category.sql at month
-- grain. {start} = ISO date (first of month) injected by search_extract.py.
SELECT
  DATE_TRUNC('month', l.date_day) AS month,
  CASE
    WHEN l.platform = 'android' THEN 'Android'
    WHEN l.platform = 'ios' THEN 'iOS'
    WHEN l.platform = 'desktop' THEN 'Web Desktop'
    WHEN l.platform = 'mobile_html5' THEN 'Web Mobile'
    ELSE 'Unknown'
  END AS platform,
  CASE
    WHEN l.keyword <> 'No keyword' THEN 'Keyword'
    ELSE 'Browsing'
  END AS search_method,
  CASE
    WHEN l.category_l1 <> 'No category' THEN 'With category'
    ELSE 'Without category'
  END AS category_usage,
  COALESCE(c.finance_category_l1_name_en, 'No category') AS finance_l1,
  COALESCE(c.finance_category_l2_name_en, 'No category') AS finance_l2,
  sum(searches) AS searches
FROM glue.odyn_search_and_ad_ranking.daily_user_searches AS l
LEFT JOIN glue.olxgroup_reservoir_ares.reservoirs_olxgroup_reservoir_eu_bi_eu_bi_dim_categories AS c
  ON c.category_nk = CASE WHEN category_l3 <> 'No category' THEN category_l3
                          WHEN category_l2 <> 'No category' THEN category_l2
                          WHEN category_l1 <> 'No category' THEN category_l1
                     END
 AND c.site_sk = 'olx|eu|uz'
WHERE l.date_day >= '{start}'
  AND l.country = 'UZ'
GROUP BY 1, 2, 3, 4, 5, 6
