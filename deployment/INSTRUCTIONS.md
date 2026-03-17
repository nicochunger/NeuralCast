# VPS Deployment Instructions (Host Orchestrator + Scheduler)

This repository now deploys runtime code with rsync, not zip packaging.

## Prerequisites

- Local machine with `rsync` and `ssh`
- VPS reachable through the `neuralvps` SSH target (or override `REMOTE_HOST`)
- `rsync` installed on the VPS

## Deploy Command

From repository root:

```bash
./deployment/redeploy_host_orchestrator_rsync.sh
```

What it does:

- Syncs `src/` to `/root/radio_host_orchestrator/src/` with `rsync --delete`
- Syncs `vps_requirements.txt`
- Syncs the full `deployment/` tree so canonical unit files, cron templates, and VPS repair scripts are available on the VPS
- Preserves generated snippet media under `src/neuralcast/assets/stories/snippets/`
- Verifies deployed entrypoints and confirms legacy top-level pipeline files are gone

## Environment Variables on VPS

Create or update `/root/radio_host_orchestrator/.env`:

```env
AZURACAST_API_KEY=your_azuracast_key
AZURACAST_BASE_URL=https://your-radio-url.com
AZURACAST_STATION=neuralforge
GEMINI_API_KEY=your_gemini_key
```

## Manual Runtime Checks

Use module entrypoints (preferred):

```bash
cd /root/radio_host_orchestrator
source venv/bin/activate
PYTHONPATH=$(pwd)/src python -m neuralcast.cli.host_orchestrator --dry-run -s neuralforge
PYTHONPATH=$(pwd)/src python -m neuralcast.cli.schedule_generator --dry-run -s neuralforge
```

Notes:

- `host_orchestrator --dry-run` still reads AzuraCast APIs and requires valid API credentials.
- Remove `--dry-run` only when you intend to apply changes/upload queue media.

## Cron Examples (VPS)

```cron
# Host orchestrator every hour at minute 5
5 * * * * cd /root/radio_host_orchestrator && PYTHONPATH=$(pwd)/src ./venv/bin/python -m neuralcast.cli.host_orchestrator -s neuralforge >> /root/radio_host_orchestrator/host_orchestrator.log 2>&1

# Weekly schedule generator every Monday at 02:10
10 2 * * 1 cd /root/radio_host_orchestrator && PYTHONPATH=$(pwd)/src ./venv/bin/python -m neuralcast.cli.schedule_generator -s neuralforge >> /root/radio_host_orchestrator/schedule_generator.log 2>&1
```

## Admin API Bridge Repair After AzuraCast Updates

If the AzuraCast Docker network is recreated during updates, the Linux bridge name can change and invalidate the UFW rule that allows the web container to reach the admin API on port `8787`. This repo now includes a repair script and a cron definition to refresh that bridge-specific UFW rule after the weekly AzuraCast update.

Deploy the repo as usual, then install the cron file on the VPS:

```bash
sudo cp /root/radio_host_orchestrator/deployment/cron/neuralcast-admin-api-post-azuracast-update /etc/cron.d/neuralcast-admin-api-post-azuracast-update
sudo chmod 644 /etc/cron.d/neuralcast-admin-api-post-azuracast-update
sudo systemctl reload cron
```

The installed cron runs every Monday at `04:45` Europe/Paris time and executes:

```bash
/root/radio_host_orchestrator/deployment/repair_admin_api_bridge_after_azuracast_update.sh
```

The script:

- discovers the current `azuracast_default` Docker bridge
- removes stale old UFW bridge rules for port `8787`
- ensures the current bridge is allowed to reach `172.18.0.1:8787`
- verifies direct backend health from inside the AzuraCast container
- verifies the proxied `/admin-http` path and authenticated `/admin-http/admin/capabilities`
- verifies the public `https://neuralcast.duckdns.org/admin-http/healthz` endpoint

Cron output lands in:

```text
/var/log/neuralcast-admin-api-bridge-repair.log
```

## Optional Deploy Overrides

```bash
REMOTE_HOST=myvps REMOTE_DIR=/opt/radio_host_orchestrator ./deployment/redeploy_host_orchestrator_rsync.sh
```
