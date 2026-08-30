# NeuralCast Admin HTTP API

This document covers the current admin API that lets an HTTPS client inspect live station state and trigger selected NeuralCast jobs without SSH.

## What It Does

The admin API is a thin HTTP wrapper around existing repo logic. It:

- validates authenticated requests
- returns the supported stations/archetypes/capabilities from repo truth
- reads live now-playing and queue state through the existing AzuraCast transport helpers
- launches the real `neuralcast.cli.host_orchestrator` and `neuralcast.cli.schedule_generator` subprocesses
- captures stdout/stderr to a per-job log and stores disk-backed job state under `runtime/admin_http/` so job status survives API restarts

The service binds to localhost by default. Expose it publicly through a reverse proxy such as nginx or caddy, and require HTTPS at the proxy layer.

## Environment Variables

Set these in `/root/projects/NeuralCast/.env` on the VPS:

```env
NEURALCAST_ADMIN_HTTP_TOKEN=replace-with-a-long-random-token
AZURACAST_API_KEY=your_azuracast_key
AZURACAST_BASE_URL=https://your-radio-url.example
AZURACAST_STATION=neuralforge
GEMINI_API_KEY=your_gemini_key
```

Optional bind overrides:

```env
NEURALCAST_ADMIN_HTTP_HOST=127.0.0.1
NEURALCAST_ADMIN_HTTP_PORT=8787
```

## Local Start Command

Run from the repository root:

```bash
NEURALCAST_ADMIN_HTTP_TOKEN=test-token \
PYTHONPATH=$(pwd)/src \
python -m neuralcast.cli.admin_api --host 127.0.0.1 --port 8787
```

If you keep your local environment in `.venv/`, replace `python` with `./.venv/bin/python`.

To force a story archetype against the current or next track directly from the CLI, use:

```bash
PYTHONPATH=$(pwd)/src \
python -m neuralcast.cli.host_orchestrator \
  -s neuralforge \
  --force-archetype album_spotlight \
  --force-track-focus next \
  --dry-run
```

## Systemd Usage

Install the unit file:

```bash
sudo cp deployment/systemd/neuralcast-admin-api.service /etc/systemd/system/neuralcast-admin-api.service
sudo systemctl daemon-reload
sudo systemctl enable --now neuralcast-admin-api.service
sudo systemctl status neuralcast-admin-api.service
```

The service listens on `127.0.0.1:8787` and should normally stay behind a reverse proxy.

If you want the service reachable from an AzuraCast Docker web container running on the same VPS, set:

```env
NEURALCAST_ADMIN_HTTP_HOST=172.18.0.1
NEURALCAST_ADMIN_HTTP_PORT=8787
```

That binds the admin API to the Docker bridge gateway on the host instead of only loopback.

## Reverse Proxy Example

### nginx

```nginx
server {
    listen 443 ssl http2;
    server_name admin.your-radio.example;

    ssl_certificate /etc/letsencrypt/live/admin.your-radio.example/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/admin.your-radio.example/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8787;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
    }
}
```

### AzuraCast Docker Web Proxy

If AzuraCast itself owns ports `80` and `443`, the easiest production setup is usually to expose the admin API under a path on the existing HTTPS domain instead of creating a separate hostname. On the current VPS this is the working public URL shape:

```text
https://neuralcast.duckdns.org/admin-http
```

Create a custom include file mounted into `/etc/nginx/azuracast.conf.d/admin-api.conf` with:

```nginx
location = /admin-http {
    return 301 /admin-http/;
}

location /admin-http/ {
    proxy_pass http://172.18.0.1:8787/;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header Authorization $http_authorization;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto https;
    proxy_redirect off;
}
```

With AzuraCast, mount that file via `/var/azuracast/docker-compose.override.yml`:

```yaml
services:
  web:
    volumes:
      - /var/azuracast/custom/nginx/admin-api.conf:/etc/nginx/azuracast.conf.d/admin-api.conf:ro
```

Then run:

```bash
cd /var/azuracast
docker compose up -d web
```

If the VPS firewall uses UFW with default-deny incoming rules, also allow the AzuraCast bridge network to reach the admin API port:

```bash
ufw allow in on br-<azuracast-network-id> from 172.18.0.0/16 to any port 8787 proto tcp
```

Because AzuraCast updates can recreate the Docker network and change the bridge name, the repo now includes an automated repair step for this firewall rule:

- Script: `/root/projects/NeuralCast/deployment/repair_admin_api_bridge_after_azuracast_update.sh`
- Cron template: `/root/projects/NeuralCast/deployment/cron/neuralcast-admin-api-post-azuracast-update`

Install the cron file on the VPS:

```bash
sudo cp /root/projects/NeuralCast/deployment/cron/neuralcast-admin-api-post-azuracast-update /etc/cron.d/neuralcast-admin-api-post-azuracast-update
sudo chmod 644 /etc/cron.d/neuralcast-admin-api-post-azuracast-update
sudo systemctl restart cron
```

It runs every Monday at `04:45` Europe/Zurich time, after the existing AzuraCast `04:00` update window, and it:

- discovers the current `azuracast_default` bridge
- removes stale old UFW bridge rules for port `8787`
- ensures the current bridge can reach the admin API
- verifies backend health from inside the AzuraCast web container
- verifies proxied `healthz` plus authenticated `/admin-http/admin/capabilities`
- verifies the public `https://neuralcast.duckdns.org/admin-http/healthz` endpoint

Cron output is written to:

```text
/root/projects/NeuralCast/runtime/logs/admin_api_bridge_repair.log
```

### Caddy

```caddy
admin.your-radio.example {
    reverse_proxy 127.0.0.1:8787
}
```

## Endpoints

`GET /healthz`

- No authentication required.
- Returns `{"status":"ok"}` when the service is up.

`GET /admin/options`

- Requires `Authorization: Bearer <token>`.
- Returns the supported stations and all real host-orchestrator archetypes.

`GET /admin/capabilities`

- Requires `Authorization: Bearer <token>`.
- Returns the supported stations, archetypes, `track_focus` values, which archetypes support `track_focus`, and the currently supported write operations.
- The `schedule_generator` capability entry also advertises advanced scheduler controls so an admin client can discover reroll and tuning support without hardcoding them.

Example response:

```json
{
  "stations": ["neuralcast", "neuralforge"],
  "archetypes": ["back_sell", "album_spotlight", "deep_dive"],
  "track_focus_values": ["current", "next"],
  "track_focus_archetypes": [
    "short_story",
    "album_spotlight",
    "era_snapshot",
    "deep_dive"
  ],
  "operations": {
    "force_archetype": {
      "dry_run_supported": true,
      "track_focus_supported": true
    },
    "schedule_generator": {
      "dry_run_supported": true,
      "track_focus_supported": false,
      "force_apply_supported": true,
      "week_start_date_supported": true,
      "supported_seed_modes": ["stable_week", "fresh", "custom"],
      "default_seed_mode": "fresh",
      "supported_tuning_fields": [
        "open_ratio_min",
        "open_ratio_max",
        "min_open_slots",
        "max_open_slots",
        "min_block_minutes",
        "max_block_minutes"
      ]
    }
  }
}
```

`GET /admin/stations/{station}/now-playing`

- Requires `Authorization: Bearer <token>`.
- Returns the current track, remaining seconds, and current listener count for `neuralcast` or `neuralforge`.

Example response:

```json
{
  "station": "neuralforge",
  "current_track": {
    "queue_id": "12345",
    "song_id": "6789",
    "artist": "Boards of Canada",
    "title": "Dayvan Cowboy",
    "duration_seconds": 319
  },
  "remaining_seconds": 41,
  "listener_count": 3
}
```

`GET /admin/stations/{station}/queue?limit=4`

- Requires `Authorization: Bearer <token>`.
- Returns the upcoming queue for `neuralcast` or `neuralforge`.
- `limit` is optional and currently accepts `1` through `10`.

Example response:

```json
{
  "station": "neuralforge",
  "items": [
    {
      "queue_id": "23456",
      "song_id": "1357",
      "artist": "Tycho",
      "title": "A Walk",
      "duration_seconds": 291
    }
  ],
  "next_track": {
    "queue_id": "23456",
    "song_id": "1357",
    "artist": "Tycho",
    "title": "A Walk",
    "duration_seconds": 291
  }
}
```

`POST /admin/force-archetype`

- Requires `Authorization: Bearer <token>`.
- Request body:

```json
{
  "station": "neuralforge",
  "archetype": "deep_dive",
  "track_focus": "next",
  "dry_run": true
}
```

- `track_focus` is optional.
- When present, it must be `"current"` or `"next"`.
- It is only valid for `short_story`, `album_spotlight`, `era_snapshot`, and `deep_dive`.

- Returns HTTP `202 Accepted` immediately:

```json
{
  "job_id": "20260314T153012Z-neuralforge-deep_dive",
  "status": "accepted"
}
```

`POST /admin/run-schedule-generator`

- Requires `Authorization: Bearer <token>`.
- Launches the real `neuralcast.cli.schedule_generator` module as an async job.
- Admin API calls default to `seed_mode="fresh"` so an interactive trigger generates a new plan unless you explicitly ask for `stable_week`.
- Request body:

```json
{
  "station": "neuralforge",
  "dry_run": true,
  "seed_mode": "fresh",
  "force_apply": false,
  "week_start_date": "2026-03-16",
  "open_ratio_min": 0.2,
  "open_ratio_max": 0.45,
  "min_open_slots": 1,
  "max_open_slots": 4,
  "min_block_minutes": 60,
  "max_block_minutes": 180
}
```

- Supported scheduler fields:
  - `dry_run`
  - `force_apply`
  - `week_start_date`
  - `seed_mode`
  - `seed_salt`
  - `open_ratio_min`
  - `open_ratio_max`
  - `min_open_slots`
  - `max_open_slots`
  - `min_block_minutes`
  - `max_block_minutes`

- Seed behavior:
  - `stable_week`: deterministic for the same station/week/configuration
  - `fresh`: rerolls a new plan; the server resolves and persists a fresh `seed_salt`
  - `custom`: deterministic reroll keyed by caller-provided `seed_salt`

- Returns HTTP `202 Accepted` immediately:

```json
{
  "job_id": "20260315T090000Z-neuralforge-schedule_generator",
  "status": "accepted"
}
```

`GET /admin/jobs/{job_id}`

- Requires `Authorization: Bearer <token>`.
- Returns the persisted job state including `operation`, timestamps, exit code, optional `archetype`, optional `track_focus`, optional `schedule_options`, log path, and a short log tail.

## curl Examples

Health:

```bash
curl -sS http://127.0.0.1:8787/healthz
```

Options:

```bash
curl -sS \
  -H "Authorization: Bearer test-token" \
  http://127.0.0.1:8787/admin/options
```

Capabilities:

```bash
curl -sS \
  -H "Authorization: Bearer test-token" \
  http://127.0.0.1:8787/admin/capabilities
```

Now playing:

```bash
curl -sS \
  -H "Authorization: Bearer test-token" \
  http://127.0.0.1:8787/admin/stations/neuralforge/now-playing
```

Queue:

```bash
curl -sS \
  -H "Authorization: Bearer test-token" \
  "http://127.0.0.1:8787/admin/stations/neuralforge/queue?limit=3"
```

Create a dry-run job:

```bash
curl -sS \
  -H "Authorization: Bearer test-token" \
  -H "Content-Type: application/json" \
  -d '{"station":"neuralforge","archetype":"deep_dive","track_focus":"next","dry_run":true}' \
  http://127.0.0.1:8787/admin/force-archetype
```

Create a schedule-generator dry-run job:

```bash
curl -sS \
  -H "Authorization: Bearer test-token" \
  -H "Content-Type: application/json" \
  -d '{"station":"neuralforge","dry_run":true,"seed_mode":"fresh","open_ratio_min":0.2,"open_ratio_max":0.45}' \
  http://127.0.0.1:8787/admin/run-schedule-generator
```

Create a deterministic custom reroll:

```bash
curl -sS \
  -H "Authorization: Bearer test-token" \
  -H "Content-Type: application/json" \
  -d '{"station":"neuralforge","dry_run":true,"seed_mode":"custom","seed_salt":"reroll-a"}' \
  http://127.0.0.1:8787/admin/run-schedule-generator
```

Poll a job:

```bash
curl -sS \
  -H "Authorization: Bearer test-token" \
  http://127.0.0.1:8787/admin/jobs/20260314T153012Z-neuralforge-deep_dive
```

## Runtime Update Steps

This repository is the canonical checkout on the VPS. Update it in place:

```bash
cd /root/projects/NeuralCast
git pull --ff-only
```

Install updated requirements in this checkout when dependencies change:

```bash
./.venv/bin/pip install -e .
```

Add `NEURALCAST_ADMIN_HTTP_TOKEN` to `.env`.

If AzuraCast will proxy to the API from Docker on the same host, also add:

```env
NEURALCAST_ADMIN_HTTP_HOST=172.18.0.1
NEURALCAST_ADMIN_HTTP_PORT=8787
```

Install and start the systemd unit:

```bash
cp /root/projects/NeuralCast/deployment/systemd/neuralcast-admin-api.service /etc/systemd/system/neuralcast-admin-api.service
systemctl daemon-reload
systemctl enable --now neuralcast-admin-api.service
systemctl status neuralcast-admin-api.service
```

5. Point nginx or caddy at `http://127.0.0.1:8787`.

If AzuraCast owns `80/443`, use the mounted nginx include approach above and point it at `http://172.18.0.1:8787/`.

## Runtime File Layout

The service stores job state and logs under the repo root:

- `runtime/admin_http/jobs/<job_id>.json`
- `runtime/admin_http/logs/<job_id>.log`
