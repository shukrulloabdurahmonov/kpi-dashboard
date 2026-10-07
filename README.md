# OLX UZ KPI Dashboard

Web dashboard of OLX UZ health metrics (Ariadne KPI definitions), live on a
DigitalOcean droplet in Docker. The Redshift warehouse (`yamato`) is only
reachable from this Mac over VPN, so data flows **push-style**:

```
Mac (07:00 cron, VPN) ──queries──▶ yamato Redshift
        │ builds updater/store/metrics_store.sqlite
        │ snapshot + integrity check
        └─scp──▶ droplet:/srv/kpi/data/snapshot.sqlite (atomic mv)
                        │
              docker compose: web (Flask/gunicorn, read-only)
                             caddy (auto-HTTPS on DuckDNS domain)
```

The droplet never touches the warehouse. If the Mac/VPN is down, the
dashboard simply serves yesterday's snapshot and the freshness badge ages.

## Layout

- `sql/` — ONE unified query template per metric family (`%(site)s`,
  `%(start)s`, `%(end)s` end-exclusive; `{period_expr}`/`{date_expr}`/`{dim_*}`
  placeholders filled per grain × dimension by `updater/extract.py`).
  Adapted from `../ariadne-kpis-definitions-master/` with fixes
  (site filters added, `Active_Listings.sql` syntax bug, real warehouse
  schema: `fact_replies_success_legacy`, liquidity cohorts keyed on
  `first_active_date_nk`).

### Time grains

Every metric family extracts at three grains — **monthly (24 months),
weekly (26 weeks, Monday-keyed), daily (90 days)** — each an exact Redshift
aggregate (weekly distinct counts like WAU cannot be summed from dailies).
Active users is `mau` / `wau` / `dau` per grain. Cohort maturity guards apply
per grain (a week appears once its Sunday is mature). Retention windows are
pruned after every run. The webapp's **Day | Week | Month** toggle (filter
panel, `?grain=`) re-buckets all cards and trend charts with grain-appropriate
deltas (MoM/YoY · WoW/vs-4w · DoD/vs-7d); snapshot charts (breakdowns, map,
matrix, MoM growth) stay monthly and say so.

### Filter dimensions

Monthly metrics are extracted per dimension slice (each an independent exact
Redshift aggregate — distinct counts are never re-aggregated client-side):
finance category L1/L2 and category L1–L4 (`eu_bi.dim_categories`), region
(13 UZ viloyats, `dim_geographies.geography_l1_name_en`), seller type
(B2C/C2C), revenue stream (payments only). Cross-dimension **pair slices**
(finance/category L1–L2 × region, finance L2 × seller type) are also
precomputed so two-dimensional filters stay exact. Player metrics
additionally carry daily slices for finance/category L1–L2. Audience metrics
carry no region — `fact_audience_categories` has no geography.
`category_tree` (distinct hierarchy paths from `dim_categories`) refreshes
every run and powers the cascading filter lists.

The webapp's **collapsible filter panel** has dedicated rows (Category L1–L4
cascading, Finance L1–L2, Region, More) — multi-value chips, empty = all,
rows shown only when the page's metrics carry that dimension. The constraint
engine collapses each family to its deepest level, uses pair slices for
two-dim combinations, sums additive metrics across multiple values exactly,
and marks unavoidable compromises visibly ("≈ summed" for distinct counts,
"<dim> n/a", "site total").

The `/dictionary` tab documents every metric (definition, formula, source,
grain, filterable dims, caveats) — same content as the per-card ⓘ popovers,
maintained in `webapp/definitions.py`.

### Visualizations

Stat tiles with MoM/YoY + sparklines; line/area trends with crosshair
tooltips and end labels; stacked compositions; diverging MoM-growth bars;
horizontal category bars; **Uzbekistan choropleth** (vendored
`webapp/static/uz_regions.json`, geoBoundaries ADM1 simplified); category ×
month **matrix heatmaps**; DAU weekday **calendar heatmap**; instant hover
tooltips on all map/heatmap cells; per-chart **fullscreen** toggle (⤢/Esc).

### Timeline Player (`/player`)

Animates metric composition over time: metric selector, dimension
(fin L1/L2, cat L1/L2), day|month grain, bar|sunburst (two-ring: L1 inner,
L2 outer, exact L1 slices — never client-side sums), date slider,
play/pause with 0.5×/1×/2× speed, grain-snap. Colors are stable per category
across frames and chart types. Data served by `/player/data` from the same
snapshot.
- `updater/` — Mac-side engine. `registry.py` lists every metric spec;
  `main.py` is the cron entry point.
- `webapp/` — Flask app (droplet-side, Dockerized). Reads only the snapshot.
- `deploy/` — droplet setup + deploy scripts, Caddyfile.
- `config/settings.py` — site, windows, retention. `config/deploy.env` —
  droplet host/ssh (copy from `deploy.env.example`, gitignored).

## Updater commands (run from this dir, VPN required)

```bash
/usr/bin/python3 -m updater.probes            # schema + count + triton probes
/usr/bin/python3 -m updater.main --backfill --local-only   # first 24m fill (resumable)
/usr/bin/python3 -m updater.main              # nightly: rolling refresh + build + ship
/usr/bin/python3 -m updater.main --ship-only  # re-ship snapshot without querying
/usr/bin/python3 -m updater.main --only nnl,active_users@weekly  # spec[@grain] subset
```

Exit codes: 0 ok · 1 fatal (VPN down / ship failed) · 2 partial (some metrics
stale — snapshot still ships, dashboard shows amber chips).

Robustness: single reused psycopg2 connection (psycopg2 only — psycopg3
breaks on this warehouse's client_encoding); every result is pre-aggregated
in Redshift (never near the 5M-row WLM limit); 3 retries with backoff per
chunk + reconnect on dead connection; per-metric failure isolation; chunk
writes are atomic (DELETE range + INSERT in one transaction) so interrupted
runs resume safely; cohort metrics (liquidity, first-time listers) only
extract matured cohorts.

## Search dashboard extraction (Mac only)

The `/search` dashboard's metrics come from two sources the box can't reach,
so they bypass the MetricSpec registry (see `updater/search_common.py`):

* **Trino** (`presto.data.olx.org`, LDAP password in the macOS Keychain,
  service `presto-ldap`) — the glue search-KPI rollups adapted from
  `Scripts/uz_search_kpis/` into `sql/search/*.sql`. Weekly/monthly values
  are AVERAGES OF DAILY values; `search_searches` is the only true total.
* **hydra clickstream on yamato** (VPN) — region-split SERP/ZSR counts and
  the 28-day top-keywords table (patterns from `Scripts/zsr_dashboard/`).

```bash
/usr/bin/python3 -m updater.search_extract --backfill --local-only  # first fill
/usr/bin/python3 -m updater.search_extract                          # daily rolling
/usr/bin/python3 -m updater.search_extract --local-only --republish # local only
```

The extractor writes `updater/store/search_payload.sqlite`, merges it into
the local store, then (unless `--local-only`) scp's it to the box where
`python3 -m updater.search_merge <payload> --republish` merges + republishes.

**Git channel (no scp/ssh setup needed):** commit the payload instead —

```bash
cp updater/store/search_payload.sqlite payloads/
git add payloads/search_payload.sqlite && git commit -m "search payload $(date +%F)" && git push
```

The box's `nightly_update.sh` merges `payloads/search_payload.sqlite` into
its store before every refresh (idempotent), so the next nightly publish
carries it; for immediate effect run the merge on the box by hand with
`--republish`. The payload is a few MB — fine for git.
Search rows carry the `search_` metric prefix: the registry's retention
pruning exempts them, and `search_merge` enforces its own 90-day retention on
the daily grain. All `*_tgv` source columns are dropped (always 0 for UZ).
Exit codes: 0 ok · 1 fatal · 2 partial (one source failed).

## WBR Truth Board refresh (`/docs/wbr-truth-board`)

The board compares tteam Trino's WBR marts with Yamato, week by week. Its
data is no longer baked into the HTML: the page fetches
`/api/wbr-truth-board`, which `webapp/wbr.py` builds from the `wbr_*` rows in
the snapshot.

* `updater/wbr_extract.py` pulls daily site totals from **tteam Trino**
  (`iceberg.gold.olxuz_*_daily`, `olxuz_repliers_weekly`; needs NetBird) and
  **Yamato** (`eu_bi` / `cubes`, through `updater.db.Warehouse`). SQL:
  `sql/wbr/`. It writes `payloads/wbr_payload.sqlite` and merges it into the
  local store. Exit 0 ok · 1 fatal · 2 one side failed (the other is kept).
* `updater/wbr_merge.py` merges a payload into any store (stdlib only), per
  metric and source, so a one-sided payload never wipes the other side.
* `nightly_update.sh` merges `payloads/wbr_payload.sqlite` before every
  refresh, like the search payload.

**Refresh on a machine that reaches both warehouses** (needs `trino` and
`psycopg2`; on the Mac use `~/Automations/Tasks/automations_env/bin/python`):

```bash
python3 -m updater.wbr_extract --republish        # extract + publish here
```

**Ship it to the box through git** (when the extract ran elsewhere):

```bash
git add -f payloads/wbr_payload.sqlite             # -f only the first time (*.sqlite is ignored)
git commit -m "wbr payload $(date +%F)" && git push
# on the box: git pull && python3 -m updater.wbr_merge payloads/wbr_payload.sqlite --republish
```

Trino auth: OAuth2 by default (browser login once, then a cached token). On a
headless machine set `TRINO_JWT`. One side only: `--no-trino` / `--no-yamato`.

**Judgement calls live in `webapp/wbr.py`**, not in the data:
`KNOWN_PARTIAL_LOADS` (date ranges dropped for a metric because the tteam load
is short but above the 70% gap line) and `BOARD_ROWS` (each row's status chip,
note and source tables). Update them when a reload lands, a definition
changes in olam, or a gap is explained; no extract is needed for that, just a
deploy/restart.

## Warehouse tunnel for the box's nightly refresh (Mac)

The box's `nightly_update.sh` reads the warehouse through a reverse-SSH
tunnel on ITS `localhost:15432`; this Mac provides it on a daily window:

```bash
cp deploy/com.shukrullo.kpi-tunnel.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.shukrullo.kpi-tunnel.plist
```

`deploy/warehouse_tunnel.sh` opens at 06:30 local, self-terminates at 09:30
(bracketing the box's 07:00 refresh + 3×30-min retries), reads the warehouse
host/port from `~/Automations/credentials/s_abdurahmonov.json` and the box
address from `config/deploy.env`. A dropped tunnel restarts within the
window (launchd KeepAlive on failure). Logs: `~/Library/Logs/kpi-tunnel.log`.
Requires the Mac awake and on VPN during the window.

## Local webapp dev

```bash
KPI_SNAPSHOT=updater/store/snapshot.sqlite.building \
  /usr/bin/python3 -m flask --app webapp.app run --port 5050 --debug
# password 'dev' when KPI_PASSWORD_HASH is unset
```

Or the full Docker stack: `docker build webapp/`, run with `-v <dir>:/data:ro`.

## Droplet deployment

**Step-by-step guide: [`deploy/DEPLOY.md`](deploy/DEPLOY.md)** — follow it
when deploying on the VPS. Summary below.

## Droplet (one-time)

1. DuckDNS: register a subdomain → droplet IP, keep the token.
2. Copy `deploy/setup_droplet.sh` to the droplet and run as root:
   ```bash
   DOMAIN=<sub>.duckdns.org DUCKDNS_SUBDOMAIN=<sub> DUCKDNS_TOKEN=<token> \
   KPI_PASSWORD_HASH='<from deploy/make_password_hash.py>' ./setup_droplet.sh
   ```
   (installs Docker, creates /srv/kpi/{data,.env,kpi.env}, DuckDNS cron)
3. Fill `config/deploy.env` on the Mac; make sure the Mac's ssh key is on the
   droplet.

## Deploy + ship

```bash
deploy/deploy.sh                              # rsync + docker compose up -d --build
/usr/bin/python3 -m updater.main --ship-only  # push current snapshot
curl https://<sub>.duckdns.org/health
```

## Nightly cron (Mac)

`~/Automations/Tasks/kpi_dashboard_update/` (task.conf `0 7 * * *` + run.sh).
Register by rerunning `~/Automations/update_workflow.sh`; logs land in
`Tasks/kpi_dashboard_update/logs/execution.log`.

## Notes

- Every dimension level (total / category / seller_type / revenue_stream) is
  deduplicated independently in Redshift. Rows are display-only — never sum
  them across periods or dimension values.
- Monthly charts drop the current partial month; KPI cards compare the last
  full month (MoM / YoY). Liquidity rate = liquid listings ÷ NNL per posting
  cohort month, mature cohorts only.
- `meta` table drives the per-metric stale chips; `snapshot_info.built_at_utc`
  drives the header freshness badge (green <36h, amber <72h, red after).
