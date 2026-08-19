# 📊 KPI Dashboard — Project Overview

## What it is

A self-hosted web dashboard of Ariadne KPIs for OLX Uzbekistan (`olx|eu|uz`) — active users, listings, new listers, replies, liquidity funnels, demand vs supply, and more — with day/week/month views, faceted filters, and interactive visualizations.

## Why

Fast, always-available access to core UZ marketplace KPIs without waiting on Tableau or running warehouse queries by hand — with metric definitions that follow Ariadne exactly and a built-in Dictionary tab documenting each one.

## How it works (push architecture)

1. A scheduled job (daily, 07:00) queries Redshift over VPN from a trusted machine
2. Results are compacted into a SQLite snapshot (~1.3 GB, ~7M rows)
3. The snapshot is pushed to a DigitalOcean droplet (atomic swap — no downtime)
4. The droplet serves the dashboard (Flask + Caddy, HTTPS, password-protected) and **never touches the warehouse**

## Key features

- 8 tabs incl. Timeline Player (animated composition charts), Demand vs Supply, liquidity funnels, and a metric Dictionary
- Day | Week | Month toggle with matching deltas (DoD / WoW / MoM / YoY)
- Cascading category filters (L1–L4), region, finance, seller-type — with exact precomputed slices
- Uzbekistan choropleth map, calendar & matrix heatmaps, per-chart fullscreen
- Validated against the warehouse (MAU digit-exact); known Tableau discrepancies root-caused and documented

## Status

Dashboard is feature-complete and validated on real data.

> ⚠️ **Note:** the live version does **not** yet include the August 9–11 updates of this build — the Day|Week|Month time grains, session-based unique repliers (with re-extracted history), liquidity funnels, the Demand vs Supply tab, and the performance/caching improvements. It will be brought up to date with the next deploy.
