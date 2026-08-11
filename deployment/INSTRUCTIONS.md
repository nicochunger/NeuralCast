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
pip install -e .
PYTHONPATH=$(pwd)/src python -m neuralcast.cli.host_orchestrator --dry-run -s neuralforge
PYTHONPATH=$(pwd)/src python -m neuralcast.cli.schedule_generator --dry-run -s neuralforge
```

Notes:

- `host_orchestrator --dry-run` still reads AzuraCast APIs and requires valid API credentials.
- Remove `--dry-run` only when you intend to apply changes/upload queue media.

## Cron Examples (VPS)

```cron
# Host orchestrator every hour at minute 5
5 * * * * cd /root/projects/NeuralCast && PYTHONPATH=$(pwd)/src ./.venv/bin/python -m neuralcast.cli.host_orchestrator -s neuralforge >> runtime/logs/host_orchestrator.log 2>&1

# Weekly schedule generator every Monday at 02:10
10 2 * * 1 cd /root/projects/NeuralCast && PYTHONPATH=$(pwd)/src ./.venv/bin/python -m neuralcast.cli.schedule_generator -s neuralforge >> runtime/logs/schedule_generator.log 2>&1
```

## Admin API Bridge Repair After AzuraCast Updates

If the AzuraCast Docker network is recreated during updates, the Linux bridge name can change and invalidate the UFW rule that allows the web container to reach the admin API on port `8787`. This repo now includes a repair script and a cron definition to refresh that bridge-specific UFW rule after the weekly AzuraCast update.

Install the cron file from the VPS checkout:

```bash
sudo cp /root/projects/NeuralCast/deployment/cron/neuralcast-admin-api-post-azuracast-update /etc/cron.d/neuralcast-admin-api-post-azuracast-update
sudo chmod 644 /etc/cron.d/neuralcast-admin-api-post-azuracast-update
sudo systemctl reload cron
```

The installed cron runs every Monday at `04:45` Europe/Paris time and executes:

```bash
/root/projects/NeuralCast/deployment/repair_admin_api_bridge_after_azuracast_update.sh
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
