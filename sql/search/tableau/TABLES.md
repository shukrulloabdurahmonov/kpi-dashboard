# Source tables & search columns — reference

Everything runs on Trino (`presto.data.olx.org`, catalog `glue` in Tableau /
`awsdatacatalog` via presto_query.py — same tables). Documented 2026-08-21.

## What the search metrics mean (all tables)

The pipeline is built from hydra `listing` events (a search-results page view):

| Concept | Definition |
|---|---|
| **search** | One `listing` event — a view of search/browse results. Pagination counts again. |
| **user** | A `session_long` (long-lived device session), not a logged-in account. |
| **search_method** | `keyword <> 'No keyword'` → **Keyword**, else **Browsing** (category/filter navigation without a query). |
| **adview** | An ad page opened *from search* (search-attributed `ad_page`). |
| **lead / SE** | A "Successful Event" from search — reply/contact (chat message, phone reveal/call). `_ses` in table names = SEs, not sessions. |
| **TGV** | Transaction (delivery purchase) attributed to search. **Not tracked for UZ or KZ — always 0.** |

## Aggregated KPI tables (feed queries 1, 2, 4)

### `odyn_search_and_ad_ranking.daily_search_users_kpis`
- Grain: `date_day` (DATE, **partition key**) × `country` × `platform` × `search_method` × `finance_category_l2`.
- Country = **full names**: `'Uzbekistan'`, `'Kazakhstan'`, `'Poland'`, …
- Platform: `Android`, `iOS`, `Web Mobile`, `Web Desktop`, `All`. Method: `Keyword`, `Browsing`, `All`.
- `finance_category_l2`: 9 values incl. `All`, `For sale`, `Cars&Bikes`, `Real estate`, `Jobs`, `Services`, `Parts`, `Heavy Machinery`, `No category`.
- Columns: `users_search`, `users_adview`, `users_lead`, `users_tgv` — **distinct session_long per day** at that dimension combo. The `All` rows are deduplicated rollups, NOT sums of the detail rows (a user can be in both Keyword and Browsing).
- Data from 2024-12-28, updated daily (~2-day lag).

### `odyn_search_and_ad_ranking.daily_search_volume_kpis`
- Same grain/dimensions/encoding as above; columns `volume_search`, `volume_adview`, `volume_lead`, `volume_tgv` = **event counts** per day.
- Always joined 1:1 to `daily_search_users_kpis` on all five dimension columns.

### `odyn_search_and_ad_ranking.daily_search_unique_kpis`
- Same grain; per-**search** (not per-user) funnel: `unique_searches`, `unique_searches_w_adview` + `share_…`, same for `_w_lead`, `_w_tgv`. Not used by our queries yet — useful for "what share of searches convert".

### `odyn_search_and_ad_ranking.weekly_search_kpis_tableau`
- The original production weekly table — **no UZ/KZ** (only BG/PL/PT/RO/UA); our `1_weekly_search_kpis.sql` replicates it. Semantics (replicated exactly):
  - `users_*`, `volume_*` = floor(sum of daily ÷ `days_in_week`) → **average daily value**, not a weekly total.
  - `ssu_*`, `avg_*_su`, `funnel_stage_adview_pct` (=ssu_adview), `funnel_stage_reply_pct` (=ssu_lead), `share_search_on_platform` = **unweighted avg of daily ratios**, round 5 dp.
  - `funnel_stage_method_share_of_search` = ratio of the floored weekly user counts (method ÷ All); NULL on method='All' rows.
  - `funnel_stage_search_pct` / `funnel_stage_platform_pct` = 1.0 on method='All' rows, else NULL.
  - `year` / `week_index` = ISO (`year_of_week()` / `week()`); `week_label` = `'W' || lpad(week,2,'0')`; weeks start Monday.

## Session-grain tables (feed query 3)

### `odyn_search_and_ad_ranking.daily_user_searches`
- Grain: `platform` × `keyword` × `category_l1/l2/l3` × `session_long`, partitioned by **`country` (ISO codes: `'UZ'`)** and `date_day` (**varchar** `'YYYY-MM-DD'`). Always filter both partitions.
- `platform` here is raw: `android`, `ios`, `mobile_html5`, `desktop`.
- `keyword`: the actual query string, or literal `'No keyword'` for browsing.
- `category_l1/l2/l3`: **category IDs as strings** (e.g. `'3'`, `'108'`, `'1578'`) or `'No category'`. L1 can be set with L2/L3 empty (top-level browse).
- `searches` = count of listing events for that combo.
- ⚠️ Don't sum this to compare with the KPI tables 1:1 — grain differences (a session appears in many rows) mean naive rollups overcount users ~3.5× and searches ~7×. Use the pre-aggregated KPI tables for user counts.
- Siblings at the same grain: `daily_user_search_adviews` (`search_adviews`), `daily_user_search_ses` (`search_ses` = leads/SEs), `daily_user_search_tgv` (no UZ rows).

### `olxgroup_reservoir_ares.reservoirs_olxgroup_reservoir_eu_bi_eu_bi_dim_categories`
- Category dimension. Join key: `category_nk` (natural key = category ID as string) + `site_sk = 'olx|eu|' || lower(country)` — UZ site is **`'olx|eu|uz'`** (923 rows), KZ is `'olx|eu|kz'`.
- Join on the deepest available level: L3 if set, else L2, else L1 (as done in `3_daily_searches_by_category.sql`).
- Useful columns: `category_name_en` / `category_name_lc` (English / local name), `category_level`, `category_l1..l5_name_en` (full path), `finance_category_l1/l2/l3_name_en` (the finance rollup used in the KPI tables), `category_url`.
- `category_nk = '-1'` → Unknown. NULL after left join → search had no resolvable category → label as `'No category'`.

## Raw events (only if you need something the aggregates lack)

### `hydra.cee_hydra_android` / `cee_hydra_ios` / `cee_hydra_frontend`
- Raw hydra event streams (CEE platform incl. UZ). Partitioned by `year`/`month`/`day`/`hour` (varchars, zero-padded) — **always filter all of them**, an unfiltered scan is ~60M rows/hour on android alone.
- `params_cc` = country (`'UZ'`), `params_en` = event name (`listing`, `ad_page`, `search_click`, `reply_*`, …), `params_keyword`/`params_search_keyword`, `params_search_id`, `params_touch_point_page`, `meta_session_long`, `params_user_id`/`params_user_uuid`.
- `hydra.cee_hydra_frontend` covers both web platforms (desktop + mobile_html5).

### `odyn_search_and_ad_ranking.fact_search_events`
- ⚠️ Looks tempting (one row per listing event with `has_adview`/`has_lead`/`has_tgv`) but contains **only a single day (2026-06-01)** for all countries — a test snapshot. Do not build on it.
