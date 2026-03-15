# NeuralCast Admin HTTP API

This document covers the phase-1 admin API that lets an HTTPS client trigger an immediate forced host-orchestrator run without SSH.

## What It Does

The admin API is a thin HTTP wrapper around the existing host-orchestrator CLI. It validates the request, launches the real `neuralcast.cli.host_orchestrator` subprocess with `--force-archetype`, optionally forwards `--force-track-focus current|next` for story-style forced archetypes, captures stdout/stderr to a per-job log, and stores job state under `admin_http/` so job status survives API restarts.

The service binds to localhost by default. Expose it publicly through a reverse proxy such as nginx or caddy, and require HTTPS at the proxy layer.

## Environment Variables

Set these in `/root/radio_host_orchestrator/.env` on the VPS:

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

`GET /admin/jobs/{job_id}`

- Requires `Authorization: Bearer <token>`.
- Returns the persisted job state including timestamps, exit code, optional `track_focus`, log path, and a short log tail.

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

Create a dry-run job:

```bash
curl -sS \
  -H "Authorization: Bearer test-token" \
  -H "Content-Type: application/json" \
  -d '{"station":"neuralforge","archetype":"deep_dive","track_focus":"next","dry_run":true}' \
  http://127.0.0.1:8787/admin/force-archetype
```

Poll a job:

```bash
curl -sS \
  -H "Authorization: Bearer test-token" \
  http://127.0.0.1:8787/admin/jobs/20260314T153012Z-neuralforge-deep_dive
```

## VPS Deployment Steps

1. From the local canonical repository, deploy the latest `src/` tree and `vps_requirements.txt`:

```bash
./deployment/redeploy_host_orchestrator_rsync.sh
```

2. On the VPS, install the updated requirements inside `/root/radio_host_orchestrator/venv`:

```bash
ssh neuralvps
cd /root/radio_host_orchestrator
./venv/bin/pip install -r vps_requirements.txt
```

3. Add `NEURALCAST_ADMIN_HTTP_TOKEN` to `/root/radio_host_orchestrator/.env`.

If AzuraCast will proxy to the API from Docker on the same host, also add:

```env
NEURALCAST_ADMIN_HTTP_HOST=172.18.0.1
NEURALCAST_ADMIN_HTTP_PORT=8787
```

4. Install and start the systemd unit:

```bash
cp /root/radio_host_orchestrator/deployment/systemd/neuralcast-admin-api.service /etc/systemd/system/neuralcast-admin-api.service
systemctl daemon-reload
systemctl enable --now neuralcast-admin-api.service
systemctl status neuralcast-admin-api.service
```

5. Point nginx or caddy at `http://127.0.0.1:8787`.

If AzuraCast owns `80/443`, use the mounted nginx include approach above and point it at `http://172.18.0.1:8787/`.

## Runtime File Layout

The service stores job state and logs under the repo root:

- `admin_http/jobs/<job_id>.json`
- `admin_http/logs/<job_id>.log`
