# Deploying the dashboard to the DigitalOcean droplet

Follow top to bottom. Steps marked **[droplet]** run in an SSH session on the
VPS; steps marked **[mac]** run on this Mac in
`~/Automations/Scripts/olx_uz_kpi_dashboard/`.

The droplet only ever runs the webapp (Docker) and receives a SQLite snapshot
file — it never touches the warehouse or VPN.

## 0. What you need

- Droplet IP, and root (or sudoer) SSH access from this Mac.
- A free DuckDNS subdomain: log in at https://www.duckdns.org, create e.g.
  `olx-uz-kpi`, point it at the droplet IP, and copy your **token**.
- ~2 GB free disk on the droplet (snapshot is a few hundred MB, plus Docker).

## 1. [mac] Generate the dashboard password hash

```bash
/usr/bin/python3 deploy/make_password_hash.py
# type the password twice; copy the pbkdf2:sha256:... output
```

## 2. [droplet] One-time setup

Copy the setup script over and run it (fill in the four values):

```bash
# from the mac:
scp deploy/setup_droplet.sh root@<DROPLET_IP>:/root/

# on the droplet:
DOMAIN=<sub>.duckdns.org \
DUCKDNS_SUBDOMAIN=<sub> \
DUCKDNS_TOKEN=<token> \
KPI_PASSWORD_HASH='<hash from step 1>' \
bash /root/setup_droplet.sh
```

This installs Docker (if missing), creates `/srv/kpi/{data,.env,kpi.env}`,
registers a cron that refreshes the DuckDNS record every 5 minutes, and does
one immediate DNS update. `kpi.env` holds the SECRET_KEY and password hash —
it is chmod 600; don't commit or copy it anywhere.

Ports 80 and 443 must be reachable (default DO droplets are open; if you use
a cloud firewall, allow 80+443 inbound).

## 3. [mac] Point the Mac at the droplet

```bash
cp config/deploy.env.example config/deploy.env
# edit config/deploy.env:
#   DROPLET_HOST=<DROPLET_IP>
#   DROPLET_USER=root            (or your sudoer user)
#   DROPLET_SSH_KEY=~/.ssh/<key used for ssh>
#   REMOTE_DIR=/srv/kpi
#   DUCKDNS_SUBDOMAIN=<sub>
```

Check the key works non-interactively: `ssh -i ~/.ssh/<key> root@<IP> true`

## 4. [mac] Deploy the webapp

```bash
deploy/deploy.sh
```

This rsyncs `webapp/`, `docker-compose.yml` and the Caddyfile to
`/srv/kpi/`, then runs `docker compose up -d --build` there. Caddy obtains
the Let's Encrypt certificate automatically on first start (needs the DNS
record from step 2 to be live — give it a minute).

## 5. [mac] Ship the first data snapshot

```bash
/usr/bin/python3 -m updater.main --ship-only
```

Builds the snapshot from the local store and scp's it to
`/srv/kpi/data/snapshot.sqlite` with an atomic rename. (Requires the local
store to be populated — it already is after the backfill.)

## 6. Verify

```bash
curl https://<sub>.duckdns.org/health
# → {"status":"ok","built_at_utc":"...","freshness":"green",...}
```

Open `https://<sub>.duckdns.org` in a browser, log in with the password from
step 1, click through the tabs, try a filter and the Timeline Player.

Optional resilience check: `reboot` the droplet once — Docker's
`restart: unless-stopped` brings the stack back on boot; the page should
answer again within a minute or two.

## 7. Turn on the nightly refresh (Mac cron)

When you want daily updates (07:00, warehouse → snapshot → droplet):

```bash
bash ~/Automations/update_workflow.sh     # registers Tasks/kpi_dashboard_update
crontab -l | grep kpi                     # confirm the entry
```

Logs land in `~/Automations/Tasks/kpi_dashboard_update/logs/execution.log`.
If the VPN is down at 07:00 the run exits early and the droplet simply keeps
yesterday's snapshot (header badge turns amber after 36h, red after 72h).

## Day-2 operations

| Task | Command |
|---|---|
| Redeploy after code changes | **[mac]** `deploy/deploy.sh` |
| Re-ship snapshot manually | **[mac]** `/usr/bin/python3 -m updater.main --ship-only` |
| Full refresh + ship now | **[mac]** `/usr/bin/python3 -m updater.main` |
| Webapp logs | **[droplet]** `cd /srv/kpi && docker compose logs -f web` |
| Caddy / TLS logs | **[droplet]** `docker compose logs -f caddy` |
| Restart the stack | **[droplet]** `cd /srv/kpi && docker compose restart` |
| Change the password | step 1 → edit `/srv/kpi/kpi.env` → `docker compose restart web` |

## Troubleshooting

- **Browser shows certificate error / Caddy loops**: DNS not propagated yet or
  port 80/443 blocked. `dig <sub>.duckdns.org` must return the droplet IP;
  check the DO cloud firewall.
- **`/health` returns `no-snapshot`**: step 5 hasn't run, or scp failed —
  check `/srv/kpi/data/` on the droplet and ssh key restrictions.
- **Login always rejected**: `KPI_PASSWORD_HASH` in `/srv/kpi/kpi.env` must be
  a single line starting `pbkdf2:sha256:`; quote it when editing, then
  `docker compose restart web`.
- **Dashboard shows stale/amber**: the Mac cron didn't run (VPN down, Mac
  asleep). Run `/usr/bin/python3 -m updater.main` manually; see the task log.
- **`docker compose` not found**: old Docker on the droplet — rerun
  `curl -fsSL https://get.docker.com | sh`.
