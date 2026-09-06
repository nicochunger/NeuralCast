# Runtime Instructions

This repository is the production and development workspace on the VPS at `/root/projects/NeuralCast`. Make changes in this checkout; do not use another checkout as a deployment source.

## Prerequisites

- The `/root/projects/NeuralCast` checkout on this VPS

## Deploy Command

From this checkout:

```bash
cd /root/projects/NeuralCast
git pull --ff-only
```

What it does:

- This checkout is the primary development workspace; normal changes are committed and pushed directly from it.
- Updates the current branch without overwriting local operational files.
- Restart persistent services after changing their runtime code.

## Environment Variables

Create or update `/root/projects/NeuralCast/.env`:

```env
AZURACAST_API_KEY=your_azuracast_key
AZURACAST_BASE_URL=https://your-radio-url.com
AZURACAST_STATION=neuralforge
GEMINI_API_KEY=your_gemini_key
```

## Manual Runtime Checks

Use module entrypoints (preferred):

```bash
cd /root/projects/NeuralCast
source .venv/bin/activate
python -m pip install -e .
PYTHONPATH=$(pwd)/src python -m neuralcast.cli.host_orchestrator --dry-run -s neuralforge

# Multilingual channel example (shared NeuralForge catalog, French host):
PYTHONPATH=$(pwd)/src python -m neuralcast.cli.host_orchestrator --dry-run --channel neuralforge-fr --min-listeners 0
PYTHONPATH=$(pwd)/src python -m neuralcast.cli.schedule_generator --dry-run -s neuralforge
```

Notes:

- `host_orchestrator --dry-run` still reads AzuraCast APIs and requires valid API credentials.
- Remove `--dry-run` only when you intend to apply changes/upload queue media.
- Host channels are defined in `src/neuralcast/assets/stories/host_channels.json`.
  Their effective behavior is resolved from
  `src/neuralcast/assets/stories/archetype_profiles.json`, including news scope,
  concert countries, selection weights, cooldowns, and generation settings.
  Put channel-specific differences in `archetype_overrides`; do not encode
  language or geography policy in cron commands or prompt templates.

## Scheduled Host and Schedule Jobs

Host-orchestrator and schedule-generator jobs are version controlled under
`deployment/cron/`. Install them instead of maintaining equivalent entries in
root's personal crontab:

```bash
sudo install -m 0644 /root/projects/NeuralCast/deployment/cron/neuralcast-host-orchestrator /etc/cron.d/neuralcast-host-orchestrator
sudo install -m 0644 /root/projects/NeuralCast/deployment/cron/neuralcast-schedule-generator /etc/cron.d/neuralcast-schedule-generator
sudo systemctl restart cron
```

Remove any equivalent personal-crontab entries before installing these files so
the same cycle cannot run twice. Both definitions use Europe/Zurich time. The
production cadence is:

- NeuralCast normal host cycle every 30 minutes;
- NeuralCast scheduled-block-intro check every minute;
- NeuralForge host cycle every two minutes;
- French NeuralForge host cycle on odd-numbered minutes;
- NeuralForge weekly schedule generation Monday at `00:05`;
- NeuralCast weekly schedule generation Monday at `00:15`.

## Runtime Log Layout

Runtime logs use service and station/channel directories when a command has more
than one target:

```text
runtime/logs/
├── host_orchestrator/<channel>/cron.log
├── host_orchestrator/<channel>/ai_host_orchestrator.log
├── host_orchestrator/<channel>/ai_host_orchestrator_segments.log
└── schedule_generator/<station>/schedule_generator.log
```

Host logs use legacy station slugs such as `neuralcast` and `neuralforge`, or
configured channel keys such as `neuralforge-fr`. Single-scope maintenance
services may keep one descriptive log directly under `runtime/logs/`. Admin API
job output remains paired with its job records under `runtime/admin_http/logs/`.

## Automated catalog maintenance

Catalog maintenance is installed from the repository-managed cron definition:

```bash
sudo install -m 0644 /root/projects/NeuralCast/deployment/cron/neuralcast-catalog-maintenance /etc/cron.d/neuralcast-catalog-maintenance
sudo systemctl restart cron
```

The schedule uses Europe/Zurich time:

- Sunday through Friday at `03:15`: sync NeuralForge, then NeuralCast.
- Saturday at `03:15`: refresh NeuralForge New Releases, sync NeuralForge only
  after a successful refresh, then sync NeuralCast.

`deployment/run_catalog_maintenance.sh` holds
`runtime/catalog-maintenance.lock` for the full run, preventing scheduled modes
from overlapping. A failed NeuralForge New Releases refresh skips its sync so an
incomplete playlist is not downloaded; the independent NeuralCast sync still runs.
Output is appended to `runtime/logs/catalog_maintenance.log`.

Install runtime log rotation with:

```bash
sudo install -m 0644 /root/projects/NeuralCast/deployment/logrotate/neuralcast-runtime-logs /etc/logrotate.d/neuralcast-runtime-logs
```

## Admin API Bridge Repair After AzuraCast Updates

If the AzuraCast Docker network is recreated during updates, the Linux bridge name can change and invalidate the UFW rule that allows the web container to reach the admin API on port `8787`. This repo now includes a repair script and a cron definition to refresh that bridge-specific UFW rule after the weekly AzuraCast update.

Install the cron file from the VPS checkout:

```bash
sudo install -m 0644 /root/projects/NeuralCast/deployment/cron/neuralcast-admin-api-post-azuracast-update /etc/cron.d/neuralcast-admin-api-post-azuracast-update
sudo systemctl restart cron
```

The installed cron runs every Monday at `04:45` Europe/Zurich time and executes:

```bash
/root/projects/NeuralCast/deployment/repair_admin_api_bridge_after_azuracast_update.sh
```

## Admin favorites storage

The authenticated admin HTTP API exposes `GET` and `PUT` `/admin/favorites` for the NeuralCast web PWA. It stores the single admin user's station-grouped favorites at:

```text
/root/projects/NeuralCast/runtime/admin_http/favorites.json
```

Writes are atomic and protected by `/root/projects/NeuralCast/runtime/admin_http/favorites.lock`. The Vercel app reaches this route through its existing `HOST_ADMIN_BASE_URL` and `HOST_ADMIN_TOKEN` configuration; the browser never receives the VPS token.

The script:

- discovers the current `azuracast_default` Docker bridge
- removes stale old UFW bridge rules for port `8787`
- ensures the current bridge is allowed to reach `172.18.0.1:8787`
- verifies direct backend health from inside the AzuraCast container
- verifies the proxied `/admin-http` path and authenticated `/admin-http/admin/capabilities`
- verifies the public `https://neuralcast.duckdns.org/admin-http/healthz` endpoint

Cron output lands in:

```text
/root/projects/NeuralCast/runtime/logs/admin_api_bridge_repair.log
```
