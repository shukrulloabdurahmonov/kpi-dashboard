# OLX UZ Search KPIs — Tableau custom SQL queries

UZ-specific versions of the search KPI data sources (originally BG/PL/PT/RO/UA only).
All queries run on Trino (`presto.data.olx.org`) and are ready to paste into
Tableau as **New Custom SQL** (no trailing semicolons). Built 2026-08-19.

| File | Replaces / purpose | Grain |
|------|--------------------|-------|
| `1_weekly_search_kpis.sql` | Drop-in replacement for `odyn_search_and_ad_ranking.weekly_search_kpis_tableau` (which has no UZ). Identical 29 columns; validated cell-perfect against production for Poland. | week × platform × search_method |
| `2_monthly_search_kpis_by_category.sql` | Monthly KPIs with finance_category_l2 breakdown (users, volumes, SSU/avg ratios). | month × platform × method × finance_category_l2 |
| `3_daily_searches_by_category.sql` | Daily search volume by keyword/browsing and finance category (L1–L3) via `bi_dim_categories`. | day × platform × method × category |
| `4_monthly_search_users.sql` | Simple monthly avg daily search/adview/lead/tgv users. | month × platform × method × finance_category_l2 |

Full table/column reference: see [TABLES.md](TABLES.md).

## Conventions to remember

- **Country encoding differs by table**: `daily_search_users_kpis` /
  `daily_search_volume_kpis` use full names (`'Uzbekistan'`);
  `daily_user_searches` uses ISO codes (`'UZ'`, and country is a partition key
  there — always filter it).
- **Semantics**: `users_*` / `volume_*` in the weekly/monthly rollups are
  **average daily values**, not period totals or distincts. `ssu_*` and
  `avg_*_su` are unweighted averages of daily ratios, rounded to 5 dp.
- **TGV is not tracked for UZ** — all `*_tgv` columns are legitimately 0.
- Kazakhstan is also available in all source tables (`'Kazakhstan'` / `'KZ'`).
- Data starts 2024-12-28; queries filter from 2025-01-01 (weekly from
  2024-12-30 = ISO W01 2025, matching the production table).
- These aggregate ~600 days per refresh — prefer Tableau **extracts** on a
  schedule over live connections.
