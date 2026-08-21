-- Daily search volume for OLX UZ by keyword/browsing, category usage, and
-- finance category (via bi_dim_categories, site_sk 'olx|eu|uz').
-- Tableau custom SQL. daily_user_searches stores ISO codes ('UZ') and country
-- is a PARTITION KEY there, so the WHERE filter prunes 6/7 of the scan.
select
  l.date_day
  , CASE
      WHEN l.country = 'PL' THEN 'Poland'
      WHEN l.country = 'PT' THEN 'Portugal'
      WHEN l.country = 'UA' THEN 'Ukraine'
      WHEN l.country = 'RO' THEN 'Romania'
      WHEN l.country = 'BG' THEN 'Bulgaria'
      WHEN l.country = 'UZ' THEN 'Uzbekistan'
      WHEN l.country = 'KZ' THEN 'Kazakhstan'
      ELSE 'Unknown'
    END AS country
  , CASE
      WHEN l.platform = 'android' THEN 'Android'
      WHEN l.platform = 'ios' THEN 'iOS'
      WHEN l.platform = 'desktop' THEN 'Web Desktop'
      WHEN l.platform = 'mobile_html5' THEN 'Web Mobile'
      ELSE 'Unknown'
    END AS platform
  , CASE
      WHEN l.keyword <> 'No keyword' THEN 'Keyword'
      WHEN l.keyword = 'No keyword' THEN 'Browsing'
      ELSE 'Unknown'
    END AS search_method
  , CASE
      WHEN l.category_l1 <> 'No category' THEN 'With category'
      WHEN l.category_l1 = 'No category' THEN 'Without category'
      ELSE 'Unknown category'
   END as category_usage
  , CASE WHEN c.finance_category_l1_name_en IS NULL THEN 'No category' ELSE c.finance_category_l1_name_en END AS finance_category_l1
  , CASE WHEN c.finance_category_l2_name_en IS NULL THEN 'No category' ELSE c.finance_category_l2_name_en END AS finance_category_l2
  , CASE WHEN c.finance_category_l3_name_en IS NULL THEN 'No category' ELSE c.finance_category_l3_name_en END AS finance_category_l3
  , sum(searches) as searches
from glue.odyn_search_and_ad_ranking.daily_user_searches as l
left join glue.olxgroup_reservoir_ares.reservoirs_olxgroup_reservoir_eu_bi_eu_bi_dim_categories as c
ON c.category_nk = CASE WHEN category_l3 <> 'No category' THEN category_l3
  WHEN category_l2 <> 'No category' THEN category_l2
  WHEN category_l1 <> 'No category' THEN category_l1
 END
AND c.site_sk = 'olx|eu|' || LOWER(l.country)
where date_day >= '2025-01-01'
  and l.country = 'UZ'
group by 1,2,3,4,5,6,7,8
