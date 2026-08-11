-- Category hierarchy lookup for cascading filters: every distinct
-- finance L1/L2 × category L1..L4 path for the site. Small (~1k rows).
SELECT DISTINCT
       COALESCE(finance_category_l1_name_en, 'unknown') AS finance_l1,
       COALESCE(finance_category_l2_name_en, 'unknown') AS finance_l2,
       COALESCE(category_l1_name_en, 'unknown') AS category_l1,
       COALESCE(category_l2_name_en, 'unknown') AS category_l2,
       COALESCE(category_l3_name_en, 'unknown') AS category_l3,
       COALESCE(category_l4_name_en, 'unknown') AS category_l4
FROM eu_bi.dim_categories
WHERE site_sk = %(site)s
