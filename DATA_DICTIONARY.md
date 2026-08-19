# OLX UZ KPI Dashboard — Data Dictionary

**Scope:** all metrics shown on the OLX UZ KPI dashboard.
**Warehouse:** Redshift, schema `eu_bi`, always filtered to `site_sk = 'olx|eu|uz'`.
**Definitions:** follow the Ariadne KPI definitions library, with the schema corrections listed in the Change Log at the bottom.
**Refresh:** extracted daily at 07:00 (Tashkent) and pushed to the dashboard as a snapshot.

---

## Conventions

**Time grains.** Every metric family is extracted at up to three grains:

| Grain | Window | Period key |
|---|---|---|
| Monthly | last 24 months | `LEFT(<date>, 7)` → `YYYY-MM` |
| Weekly | last 26 weeks | `DATE_TRUNC('week', <date>)` → Monday of the week |
| Daily | last 90 days | `LEFT(<date>, 10)` → `YYYY-MM-DD` |

**Example queries.** The SQL below shows the **monthly, site-total** form of each query. `:start` / `:end` are the date-range bounds (half-open: `>= :start AND < :end`). To slice by a dimension, join the relevant dim table and add the dim column to `SELECT` and `GROUP BY` — the dashboard pre-computes every slice this way (finance L1/L2, category L1–L4, region, seller type, revenue stream).

**Non-additive metrics.** Distinct-count metrics (users, sessions, listers, repliers) **cannot be summed** across slices — a user active in two categories counts once in the total but once *per category*. The dashboard marks summed slices of these metrics with ≈.

**Cohort maturity.** Liquidity and first-time-lister metrics are keyed on the **posting cohort** (`first_active_date_nk`) and only published once the measurement window has fully closed: 28-day metrics after 29 days, 14-day after 15, 7-day daily cohorts after 8.

---

## 1. Listings & Supply

| Metric | Definition | Formula | Source | Grain | Dimensions |
|---|---|---|---|---|---|
| **Net new listings (NNL)** | Listings that became active for the first time in the period; renewals and reposts excluded. | `COUNT(DISTINCT listing_sk)` where `listing_net_sk = 'net'`, by `first_active_date_nk` | `eu_bi.fact_listings` | M + D | finance, category L1–L4, region, seller type |
| **Gross new listings (GNL)** | All listings posted in the period, including renewals and reposts (the NNL `net` filter switched off). | `COUNT(DISTINCT listing_sk)`, by `COALESCE(first_active_date, date_posted)` | `eu_bi.fact_listings` | M | same |
| **Unique listers (MUL)** | Distinct users who posted at least one net-new listing in the period. | `COUNT(DISTINCT user_sk)` where `listing_net_sk = 'net'` | `eu_bi.fact_listings` | M + D | same |
| **Active listings** | Distinct listings live at any point during the month (deduplicated within the month — not an end-of-month stock figure). | `COUNT(DISTINCT listing_sk)` over daily active-listing snapshots, `is_active = 1` | `eu_bi.fact_active_listings` + `fact_listings` | M | same |
| **Active listers** | Distinct users with at least one live listing during the month. | `COUNT(DISTINCT user_sk)` over the same snapshots | `eu_bi.fact_active_listings` + `fact_listings` | M | same |
| **Insertions (all)** | Every insertion event — new posts and renewals — excluding removed ones. Event-level: one listing can insert several times per month. | `COUNT(listing_sk)` insertion events, `is_removed = 0` | `eu_bi.fact_listings_insertions` | M | same |
| **Renewed insertions** | Insertion events that were renewals of an existing listing. | `SUM(is_renewal)`, `is_removed = 0` | `eu_bi.fact_listings_insertions` | M | same |
| **Free insertions** | Insertion events that used a free quota rather than a paid product. | `SUM(is_free)`, `is_removed = 0` | `eu_bi.fact_listings_insertions` | M | same |

### SQL — Net new listings

```sql
SELECT LEFT(fl.first_active_date_nk, 7) AS month,
       COUNT(DISTINCT fl.listing_sk)    AS nnl
FROM eu_bi.fact_listings fl
WHERE fl.site_sk = 'olx|eu|uz'
  AND fl.listing_net_sk = 'net'
  AND fl.first_active_date_nk >= :start
  AND fl.first_active_date_nk <  :end
GROUP BY 1
ORDER BY 1;
```

### SQL — Gross new listings

```sql
SELECT LEFT(COALESCE(fl.first_active_date_local::DATE, fl.date_posted_nk), 7) AS month,
       COUNT(DISTINCT fl.listing_sk) AS gnl
FROM eu_bi.fact_listings fl
WHERE fl.site_sk = 'olx|eu|uz'
  AND COALESCE(fl.first_active_date_local::DATE, fl.date_posted_nk) >= :start
  AND COALESCE(fl.first_active_date_local::DATE, fl.date_posted_nk) <  :end
GROUP BY 1
ORDER BY 1;
```

### SQL — Unique listers (MUL)

```sql
SELECT LEFT(COALESCE(fl.first_active_date_local::DATE, fl.date_posted_nk), 7) AS month,
       COUNT(DISTINCT fl.user_sk) AS unique_listers
FROM eu_bi.fact_listings fl
WHERE fl.site_sk = 'olx|eu|uz'
  AND fl.listing_net_sk = 'net'
  AND COALESCE(fl.first_active_date_local::DATE, fl.date_posted_nk) >= :start
  AND COALESCE(fl.first_active_date_local::DATE, fl.date_posted_nk) <  :end
GROUP BY 1
ORDER BY 1;
```

### SQL — Active listings & active listers

```sql
-- Heaviest query in the set — run one month at a time.
SELECT LEFT(fal.date_nk, 7)              AS month,
       COUNT(DISTINCT fal.listing_sk)    AS active_listings,
       COUNT(DISTINCT fl.user_sk)        AS active_listers
FROM eu_bi.fact_active_listings fal
LEFT JOIN eu_bi.fact_listings fl ON fl.listing_sk = fal.listing_sk
WHERE fl.site_sk = 'olx|eu|uz'
  AND fal.date_nk >= :start
  AND fal.date_nk <  :end
  AND fal.is_active = 1
GROUP BY 1
ORDER BY 1;
```

### SQL — Insertions

```sql
SELECT LEFT(fii.event_date_nk, 7) AS month,
       COUNT(fii.listing_sk)      AS insertions_all,
       SUM(fii.is_renewal)        AS insertions_renewed,
       SUM(fii.is_free)           AS insertions_free
FROM eu_bi.fact_listings_insertions fii
LEFT JOIN eu_bi.fact_listings fl ON fl.listing_sk = fii.listing_sk
WHERE fii.site_sk = 'olx|eu|uz'
  AND fii.event_date_nk >= :start
  AND fii.event_date_nk <  :end
  AND fii.is_removed = 0
GROUP BY 1
ORDER BY 1;
```

---

## 2. Audience & Traffic

> `eu_bi.fact_audience_categories` has **no geography** column, so audience metrics have no region slice.

| Metric | Definition | Formula | Source | Grain | Dimensions |
|---|---|---|---|---|---|
| **MAU** | Distinct visitors active during the month — **new methodology (April 2026)**, based on qualified sessions. Not comparable with the pre-2026 `user_sk`-based MAU. | `COUNT(DISTINCT session_long_sk)` where `applicable_to_active_users AND NOT is_outlier` | `eu_bi.fact_audience_categories` | M | finance, category L1–L4 |
| **WAU** | Same definition at week grain (Mon–Sun). | same, per week | same | W | same |
| **DAU** | Same definition at day grain. | same, per day | same | D | total only |
| **Pageviews** | Total pages viewed, crawler traffic excluded. | `SUM(num_pageviews)` where `crawler_type IS NULL` | same | M | total + categories |
| **Bounces (site)** | Sessions that saw exactly one page in the whole visit. Whole-session metric — only meaningful at site level. | `COUNT(DISTINCT session_eq1_pvs_sess)` | same | M | total only |
| **Visits (per category)** | Sessions that viewed more than one page *within a category*. One session can visit several categories — no site total. | `COUNT(DISTINCT session_mt1_pvs_cat)` | same | M | categories only |
| **Bounces per category** | Sessions that saw exactly one page within a category. | `COUNT(DISTINCT session_eq1_pvs_cat)` | same | M | categories only |
| **Entering visits** | Category visits minus category bounces. | `visits − bounces_per_category` | same | M | categories only |
| **Ad impressions** | Times listings were shown in list/search results. | `SUM(num_impressions)` | `eu_bi.fact_listings_traffic_agg` | M | total only (source has no category/geography) |
| **Ad views** | Listing detail-page opens. | `SUM(num_ad_page)` | `eu_bi.fact_listings_traffic_agg` | M | total only |

### SQL — Active users (MAU; change the period key for WAU/DAU)

```sql
SELECT LEFT(fac.date_event_local, 7)          AS month,
       COUNT(DISTINCT fac.session_long_sk)    AS mau
FROM eu_bi.fact_audience_categories fac
WHERE fac.site_sk = 'olx|eu|uz'
  AND fac.date_event_local >= :start
  AND fac.date_event_local <  :end
  AND fac.applicable_to_active_users IS TRUE
  AND fac.is_outlier IS NOT TRUE
GROUP BY 1
ORDER BY 1;
-- WAU: period key TO_CHAR(DATE_TRUNC('week', CAST(fac.date_event_local AS DATE)), 'YYYY-MM-DD')
-- DAU: period key LEFT(fac.date_event_local, 10)
```

### SQL — Site-total traffic (pageviews & bounces)

```sql
SELECT LEFT(fac.date_event_local, 7)               AS month,
       SUM(fac.num_pageviews)                      AS pageviews,
       COUNT(DISTINCT fac.session_eq1_pvs_sess)    AS bounces
FROM eu_bi.fact_audience_categories fac
WHERE fac.site_sk = 'olx|eu|uz'
  AND fac.date_event_local >= :start
  AND fac.date_event_local <  :end
  AND fac.crawler_type IS NULL
GROUP BY 1
ORDER BY 1;
```

### SQL — Per-category traffic (visits, category bounces, entering visits)

```sql
-- Meaningful only per category — always slice by a category dim, never site-total.
SELECT LEFT(fac.date_event_local, 7)            AS month,
       dc.category_l1_name_en                   AS category_l1,
       COUNT(DISTINCT fac.session_mt1_pvs_cat)  AS visits,
       COUNT(DISTINCT fac.session_eq1_pvs_cat)  AS bounces_per_category,
       SUM(fac.num_pageviews)                   AS pageviews,
       COUNT(DISTINCT fac.session_mt1_pvs_cat)
         - COUNT(DISTINCT fac.session_eq1_pvs_cat) AS entering_visits
FROM eu_bi.fact_audience_categories fac
LEFT JOIN eu_bi.dim_categories dc ON dc.category_sk = fac.category_sk
WHERE fac.site_sk = 'olx|eu|uz'
  AND fac.date_event_local >= :start
  AND fac.date_event_local <  :end
  AND fac.crawler_type IS NULL
GROUP BY 1, 2
ORDER BY 1, 2;
```

### SQL — Ad impressions & ad views

```sql
SELECT LEFT(t.date_event_local, 7) AS month,
       SUM(t.num_impressions)      AS ad_impressions,
       SUM(t.num_ad_page)          AS ad_views
FROM eu_bi.fact_listings_traffic_agg t
WHERE t.site_sk = 'olx|eu|uz'
  AND t.date_event_local >= :start
  AND t.date_event_local <  :end
GROUP BY 1
ORDER BY 1;
```

---

## 3. Demand — Replies

| Metric | Definition | Formula | Source | Grain | Dimensions |
|---|---|---|---|---|---|
| **Replies** | Successful reply events (chat, call, SMS) from buyers to listings. | `COUNT(*)` of successful reply events | `eu_bi.fact_replies_success_legacy` | M + D | finance, category L1–L4, region |
| **Unique repliers** | Distinct visitor **sessions** that sent at least one successful reply (buyer side). Session-based, so anonymous repliers (calls, phone reveals) are included. | `COUNT(DISTINCT session_long_sk)` over successful reply events | `eu_bi.fact_replies_success_legacy` | M + D | same |

> ⚠️ **Unique repliers is session-based since 2026-08-11** (previously logged-in `user_sk` only, ~2× lower). The historical series was re-extracted on the new basis, so the trend is consistent.
> ⚠️ The warehouse renamed `fact_replies_success` → `fact_replies_success_legacy` (same columns, still fresh). The HLL-based alternative is `cubes.fact_replies_success_cube`.

### SQL — Replies & unique repliers

```sql
SELECT LEFT(fr.date_sent_nk, 7)               AS month,
       COUNT(DISTINCT fr.session_long_sk)     AS unique_repliers,
       COUNT(*)                               AS replies
FROM eu_bi.fact_replies_success_legacy fr
WHERE fr.site_sk = 'olx|eu|uz'
  AND fr.date_sent_nk >= :start
  AND fr.date_sent_nk <  :end
GROUP BY 1
ORDER BY 1;
```

---

## 4. Liquidity (posting-cohort metrics)

All liquidity metrics are keyed on the **posting cohort**: the month/day the listing first became active (`first_active_date_nk`). A cohort is only published once its measurement window has fully closed.

| Metric | Definition | Formula | Source | Grain | Dimensions |
|---|---|---|---|---|---|
| **Liquid listings *W* (*R*)** — variants: 1d/7d/14d/28d × ≥1/≥3 replies | Net-new listings that received at least *R* replies within *W* of posting. | `COUNT(listing_sk)` where the window's reply counter > 0 (or > 2), cohort = `first_active_date_nk` | `eu_bi.fact_listings_liquidity_success` + `fact_listings` | M cohorts (7d also D, 1d also W) | finance L1/L2, category L1/L2, region |
| **Liquid listers *W* (*R*)** — 7d/14d/28d × ≥1/≥3 | Distinct listers whose new listing hit the reply threshold within the window. | `COUNT(DISTINCT user_sk)`, same filters | same | M cohorts | same |
| **Liquidity 7d rate** | Share of the NNL cohort that got ≥1 reply within 7 days. | `liquid_listings_7d_1r ÷ NNL`, same cohort month | derived in the dashboard | M cohorts | follows sources |
| **First-time listers' listings success (14d/3r)** | New listings by first-time listers that got ≥3 replies within 14 days. | `COUNT(listing_sk)` where `num_replies_2wk > 2 AND user_first_time_returning_nk = 'first_time'` | + `eu_bi.fact_listings_user_segments` | M cohorts | total only |
| **First-time listers success (14d/3r)** | Distinct first-time listers whose listing got ≥3 replies within 14 days. | `COUNT(DISTINCT user_sk)`, same filters | same | M cohorts | total only |

Reply-window columns on `fact_listings_liquidity_success`: `num_replies_day` (1d), `num_replies_wk` (7d), `num_replies_2wk` (14d), `num_replies_4wk` (28d).

### SQL — Liquidity, all window/threshold variants in one scan

```sql
-- Run only for cohort months whose last day is ≥28 days old.
-- NOTE: cohorts key on first_active_date_nk — the old library's date_posted_nk
-- no longer exists on this table.
SELECT LEFT(a.first_active_date_nk, 7) AS cohort_month,
       COUNT(CASE WHEN a.num_replies_day > 0 THEN a.listing_sk END)                 AS liquid_listings_1d_1r,
       COUNT(CASE WHEN a.num_replies_day > 2 THEN a.listing_sk END)                 AS liquid_listings_1d_3r,
       COUNT(CASE WHEN a.num_replies_wk  > 0 THEN a.listing_sk END)                 AS liquid_listings_7d_1r,
       COUNT(DISTINCT CASE WHEN a.num_replies_wk  > 0 THEN b.user_sk END)           AS liquid_listers_7d_1r,
       COUNT(CASE WHEN a.num_replies_wk  > 2 THEN a.listing_sk END)                 AS liquid_listings_7d_3r,
       COUNT(DISTINCT CASE WHEN a.num_replies_wk  > 2 THEN b.user_sk END)           AS liquid_listers_7d_3r,
       COUNT(CASE WHEN a.num_replies_2wk > 0 THEN a.listing_sk END)                 AS liquid_listings_14d_1r,
       COUNT(DISTINCT CASE WHEN a.num_replies_2wk > 0 THEN b.user_sk END)           AS liquid_listers_14d_1r,
       COUNT(CASE WHEN a.num_replies_2wk > 2 THEN a.listing_sk END)                 AS liquid_listings_14d_3r,
       COUNT(DISTINCT CASE WHEN a.num_replies_2wk > 2 THEN b.user_sk END)           AS liquid_listers_14d_3r,
       COUNT(CASE WHEN a.num_replies_4wk > 0 THEN a.listing_sk END)                 AS liquid_listings_28d_1r,
       COUNT(DISTINCT CASE WHEN a.num_replies_4wk > 0 THEN b.user_sk END)           AS liquid_listers_28d_1r,
       COUNT(CASE WHEN a.num_replies_4wk > 2 THEN a.listing_sk END)                 AS liquid_listings_28d_3r,
       COUNT(DISTINCT CASE WHEN a.num_replies_4wk > 2 THEN b.user_sk END)           AS liquid_listers_28d_3r
FROM eu_bi.fact_listings_liquidity_success a
JOIN eu_bi.fact_listings b ON a.listing_sk = b.listing_sk
WHERE b.site_sk = 'olx|eu|uz'
  AND b.listing_net_sk = 'net'
  AND a.first_active_date_nk >= :start
  AND a.first_active_date_nk <  :end
GROUP BY 1
ORDER BY 1;
```

### SQL — First-time listers success

```sql
-- Run only for cohort months ≥14 days mature.
SELECT LEFT(a.first_active_date_nk, 7) AS cohort_month,
       COUNT(a.listing_sk)             AS ftl_success_listings_14d_3r,
       COUNT(DISTINCT b.user_sk)       AS ftl_success_listers_14d_3r
FROM eu_bi.fact_listings_liquidity_success a
JOIN eu_bi.fact_listings b ON a.listing_sk = b.listing_sk
JOIN eu_bi.fact_listings_user_segments c
  ON a.listing_sk = c.listing_sk AND a.first_active_date_nk = c.first_active_date_nk
WHERE b.site_sk = 'olx|eu|uz'
  AND b.listing_net_sk = 'net'
  AND a.num_replies_2wk > 2
  AND c.user_first_time_returning_nk = 'first_time'
  AND a.first_active_date_nk >= :start
  AND a.first_active_date_nk <  :end
GROUP BY 1
ORDER BY 1;
```

---

## 5. Financial

All money values are **local currency (UZS)**, not USD. Revenue metrics exclude `revenue_stream = 'Not Revenue'` and zero-value transactions.

| Metric | Definition | Formula | Source | Grain | Dimensions |
|---|---|---|---|---|---|
| **Revenue gross (UZS)** | Gross transactional revenue. | `SUM(trans_value_gross)` | `eu_bi.fact_payments` + `dim_products` | M | finance, category L1–L4, region, seller type, revenue stream |
| **Revenue net (UZS)** | Net transactional revenue (after fee/tax components). | `SUM(trans_value_net)` | same | M + D | same |
| **Service fee (UZS)** | Service-fee component of transactions. | `SUM(trans_value_service_fee)` | same | M | same |
| **Tax (UZS)** | Tax component of transactions. | `SUM(trans_value_tax)` | same | M | same |
| **Bonus gross (UZS)** | Value paid with bonus balance. | `SUM(bonus_value_gross)` | same | M | same |
| **Refunds gross (UZS)** | Refunded value. | `SUM(refund_value_gross)` | same | M | same |
| **Transactions** | Distinct revenue transactions. | `COUNT(DISTINCT transaction_sk)` | same | M | same |
| **Payments** | Distinct payment operations. | `COUNT(DISTINCT payment_sk)` | same | M | same |
| **Paying listers (PMUL)** | Distinct users who paid for anything revenue-relevant in the period. | `COUNT(DISTINCT user_sk)` where `revenue_stream != 'Not Revenue' AND trans_value_net != 0` | same | M + D | finance, category, region, seller type |
| **Cash flows** | Cash-flow operations (wallet top-ups etc., not revenue). | `COUNT(DISTINCT payment_sk)` where `cash_value_gross != 0` | `eu_bi.fact_payments` | M | total only |
| **Cash-flow payers** | Distinct users with cash-flow operations. | `COUNT(DISTINCT user_sk)` where `cash_value_gross != 0` | `eu_bi.fact_payments` | M | total only |

### SQL — Revenue family

```sql
SELECT LEFT(fp.payment_date, 7)             AS month,
       SUM(fp.trans_value_gross)            AS revenue_gross,
       SUM(fp.trans_value_net)              AS revenue_net,
       SUM(fp.trans_value_service_fee)      AS service_fee,
       SUM(fp.trans_value_tax)              AS tax,
       SUM(fp.bonus_value_gross)            AS bonus_gross,
       SUM(fp.refund_value_gross)           AS refund_gross,
       COUNT(DISTINCT fp.transaction_sk)    AS transactions,
       COUNT(DISTINCT fp.payment_sk)        AS payments
FROM eu_bi.fact_payments fp
LEFT JOIN eu_bi.dim_products pdc ON pdc.product_sk = fp.product_sk
WHERE fp.site_sk = 'olx|eu|uz'
  AND pdc.revenue_stream != 'Not Revenue'
  AND fp.trans_value_net != 0
  AND fp.payment_date >= :start
  AND fp.payment_date <  :end
GROUP BY 1
ORDER BY 1;
```

### SQL — Paying listers (PMUL)

```sql
SELECT LEFT(fp.transaction_date, 7)  AS month,
       COUNT(DISTINCT fp.user_sk)    AS pmul
FROM eu_bi.fact_payments fp
LEFT JOIN eu_bi.dim_products pdc ON pdc.product_sk = fp.product_sk
WHERE fp.site_sk = 'olx|eu|uz'
  AND pdc.revenue_stream != 'Not Revenue'
  AND fp.trans_value_net != 0
  AND fp.transaction_date >= :start
  AND fp.transaction_date <  :end
GROUP BY 1
ORDER BY 1;
```

### SQL — Cash flows

```sql
SELECT LEFT(fp.payment_date, 7)         AS month,
       COUNT(DISTINCT fp.payment_sk)    AS cash_flows,
       COUNT(DISTINCT fp.user_sk)       AS cash_flow_payers
FROM eu_bi.fact_payments fp
WHERE fp.site_sk = 'olx|eu|uz'
  AND fp.cash_value_gross != 0
  AND fp.payment_date >= :start
  AND fp.payment_date <  :end
GROUP BY 1
ORDER BY 1;
```

---

## 6. Users

| Metric | Definition | Formula | Source | Grain | Dimensions |
|---|---|---|---|---|---|
| **New users** | Accounts created in the period that aren't banned/deleted. | `COUNT(DISTINCT user_sk)` by `time_created`, status not in (banned, deleted, pending_deletion) | `eu_bi.dim_users` | M | total only (no category/geography on accounts) |
| **Confirmed new users** | New accounts that completed confirmation. | `COUNT(DISTINCT user_sk)` where `user_status = 'confirmed'` | `eu_bi.dim_users` | M | total only |

### SQL — New & confirmed new users

```sql
SELECT LEFT(t.time_created, 7) AS month,
       COUNT(DISTINCT CASE WHEN t.user_status NOT IN ('banned', 'deleted', 'pending_deletion')
                           THEN t.user_sk END) AS new_users,
       COUNT(DISTINCT CASE WHEN t.user_status = 'confirmed'
                           THEN t.user_sk END) AS confirmed_new_users
FROM eu_bi.dim_users t
WHERE t.site_sk = 'olx|eu|uz'
  AND t.time_created >= :start
  AND t.time_created <  :end
GROUP BY 1
ORDER BY 1;
```

---

## 7. Search (Search dashboard)

Extracted by `updater/search_extract.py` on the Mac (the box can't reach these
sources); metric names carry the `search_` prefix. Two sources with DIFFERENT
definitions of "a search" — never compare volumes across them:

**Trino rollups** (`glue.odyn_search_and_ad_ranking.daily_search_users_kpis` +
`daily_search_volume_kpis` + `daily_user_searches`, from 2025-01-01, weekly
from 2024-12-30):

| Metric | Meaning |
|---|---|
| `search_users` / `search_users_adview` / `search_users_lead` | **Avg daily** users searching / also viewing an ad / also replying same-day |
| `search_volume` (+`_adview`, `_lead`) | **Avg daily** searches / ad views / replies by searchers |
| `search_ssu_adview`, `search_ssu_lead` | % of search users reaching an ad view / reply (unweighted avg of daily ratios). Per-category values use ALL search users as denominator |
| `search_avg_adview_su`, `search_avg_lead_su` | Ad views / replies per search user per day |
| `search_share_on_platform` | Method's share of the platform's search users |
| `search_searches` | **True event totals** (daily 90d + monthly, by platform/method/category-usage/finance L1–L2) |

All `*_tgv` source columns dropped — TGV is not tracked for UZ (always 0).

**hydra clickstream** (yamato, from 2025-07-02, bot-filtered, first-page
keyword SERPs only, `result_count >= 999999` sentinel excluded):

| Metric | Meaning |
|---|---|
| `search_serp` | Keyword SERP views (monthly by region/platform + daily 90d) |
| `search_zsr` | SERP views with 0 results. **Android reports true zeros (~15%); web/iOS auto-extend empty searches (~0.001%) — rates are never blended across platforms** |
| `search_zsr_low` | SERP views with 1–10 results (low supply) |
| `search_keywords` table | Top 500/platform keywords by 28-day volume with zsr/low/avg_results |

## Change log & known discrepancies

| Date | Change |
|---|---|
| Apr 2026 | **MAU/WAU/DAU new methodology**: `COUNT(DISTINCT session_long_sk)` on `fact_audience_categories` with `applicable_to_active_users AND NOT is_outlier`. Not comparable with the older `user_sk`-based series. Validated digit-exact against the `active_users_agg` cube unload. |
| 2026-08 | Warehouse renamed `eu_bi.fact_replies_success` → `eu_bi.fact_replies_success_legacy` (same columns, still refreshed). HLL alternative: `cubes.fact_replies_success_cube`. |
| 2026-08 | Liquidity and user-segment tables key cohorts on **`first_active_date_nk`** — the `date_posted_nk` column referenced by older Ariadne library queries no longer exists on those tables. |
| 2026-08-11 | **Unique repliers switched to session basis** (`session_long_sk`, includes anonymous callers/phone-reveals) — ~2× the old logged-in `user_sk` basis. The full historical series was re-extracted, so the dashboard trend is consistent. |

**Reconciliation vs the Tableau "Demand vs Supply" board** (verified by query, 2026-08-11):

- **Replies match exactly** — Tableau computes `SUM(successful_event_factor)` on `fact_replies_events`, which reproduces our digits.
- **Listings match**; **active listers and unique repliers are ~2–6× higher in Tableau** because it sums distinct-count cube measures across `category_l3 × geography_l2` rows (double-counting entities present in several rows; reproduced within ~3%). The dashboard follows the Ariadne definitions and deduplicates correctly.
