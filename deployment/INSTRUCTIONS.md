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
- Syncs `deployment/systemd/` so canonical unit files are available on the VPS
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

## Optional Deploy Overrides

```bash
REMOTE_HOST=myvps REMOTE_DIR=/opt/radio_host_orchestrator ./deployment/redeploy_host_orchestrator_rsync.sh
```
