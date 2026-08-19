# July 14 duplicate-load reports

Two drafts, Slack-formatted (bold via asterisks, no headers). The first is for
the data engineering / warehouse team; the second is a heads-up for
`#project-kpi-dashboard`.

---

## 1. Report to Data Engineering / Warehouse team

🐛 **Duplicate load in `eu_bi.fact_audience_categories` on 2026-07-14 — all sites**

Android traffic was loaded twice that day: a malformed bare `android` channel_sk exactly mirrors `mobile_app|android` (46,726,920 rows each, all 14 sites, existing on no other date) with *different* `session_long_sk` values — so every site's DAU/WAU/MAU and pageviews for that date are inflated (e.g. UZ DAU reads 882K vs a true ~602K). Likely from the batch reprocessing at `time_inserted = 2026-07-15 15:20`. Repro:

```sql
SELECT channel_sk, COUNT(*) FROM eu_bi.fact_audience_categories
WHERE date_event_local = '2026-07-14' GROUP BY 1;
```

Could you remove the `channel_sk = 'android'` rows for that date? We'll re-extract once clean. 🙏

---

## 2. Heads-up for #project-kpi-dashboard

⚠️ **Known data issue: the July 14 DAU spike is a warehouse artifact, not real traffic**

If you noticed DAU jumping to ~882K on July 14 (vs a ~520–565K baseline) across *all* categories — that's a duplicate load in the warehouse, not a real event.

**Root cause:** the warehouse batch for July 14 was reprocessed the next afternoon, and the rerun loaded the entire Android channel **twice** (the second copy under a malformed channel name, with fresh session keys, so dedup doesn't catch it).

**What's affected:**
• DAU on July 14 (true value ≈ **602K**, not 882K)
• Pageviews on July 14 (~9.5M of the 25.4M is duplicate)
• WAU for the week of July 13 and MAU for July (each ~280K too high)
• Per-category visits/bounces on July 14 (mildly, ~+14%)

**Not affected:** replies, listings, listers, new users, payments, revenue — all verified clean.

We've reported it to the data engineering team; once the source table is fixed (or we add a defensive filter), the affected points will be re-extracted and the dashboard will show corrected numbers. Until then, please read July 14 (and the containing week/month) with this in mind.
