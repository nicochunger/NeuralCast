# Auto-Repair Admin API Bridge Access After AzuraCast Updates

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work proceeds.

This repository includes `.agent/PLANS.md`; this document is maintained in accordance with `.agent/PLANS.md`.

## Purpose / Big Picture

After this change, weekly AzuraCast updates on the VPS should no longer leave the NeuralCast admin API unreachable from the public `/admin-http` path because of stale UFW bridge rules. A repo-tracked repair script will discover the current AzuraCast Docker bridge, refresh the firewall allowance for port `8787`, verify the backend and proxy health checks, and a repo-tracked cron entry will run the script after the weekly AzuraCast update window.

## Progress

- [x] (2026-03-16 19:30Z) Inspected the current VPS cron configuration, the live Docker/UFW state, and confirmed that weekly AzuraCast updates recreate the `azuracast_default` network and can invalidate the existing bridge-specific UFW rule.
- [x] (2026-03-16 20:17Z) Implemented the repo-tracked repair script, cron definition, deploy-script sync changes, and docs updates.
- [x] (2026-03-16 20:19Z) Deployed the new automation to the VPS, installed the cron entry, and verified the repair script live against the current Docker/UFW state.

## Surprises & Discoveries

- Observation: the weekly AzuraCast update is already scheduled in root's crontab for Mondays at `04:00` Europe/Paris time.
  Evidence: `crontab -l` on the VPS shows `CRON_TZ=Europe/Paris` and `0 4 * * 1 /bin/bash -lc "cd /var/azuracast && yes '' | ./docker.sh update ..."` on March 16, 2026.
- Observation: the live failure happened because UFW allowed port `8787` only on an old bridge interface while the current `azuracast_default` network had been recreated as `br-bb084daaeb95`.
  Evidence: `docker network inspect azuracast_default` shows a March 16, 2026 creation time and `ufw status verbose` showed only the previous bridge rule before manual repair.

## Decision Log

- Decision: keep the AzuraCast update cron unchanged and add a separate repo-tracked follow-up cron that runs later with health verification.
  Rationale: this keeps the risky Docker update path untouched, gives the update ample time to finish, and still makes the recovery behavior source-controlled and repeatable.
  Date/Author: 2026-03-16 / Codex

## Outcomes & Retrospective

The repair path now lives in the repo and is installed on the VPS. The weekly AzuraCast update remains at `04:00` Europe/Paris, and a second cron now runs at `04:45` Europe/Paris to refresh the admin API bridge firewall rule and verify health.

The manual verification on March 16, 2026 proved the intended behavior end to end:

- the script discovered the current bridge `br-bb084daaeb95`
- it deleted the stale old bridge rule that had caused the outage
- it kept the current bridge rule for `172.18.0.0/16 -> 8787/tcp`
- it passed direct backend, container-proxy, authenticated capabilities, and public health checks

## Context and Orientation

The admin API is served by `neuralcast-admin-api.service` and listens on `172.18.0.1:8787` so the AzuraCast web container can proxy `/admin-http/` to it. The current public proxying is configured inside AzuraCast nginx. UFW is enabled on the VPS and currently defaults to `deny (incoming)`, which means the bridge interface allowing container access to port `8787` must match the active `azuracast_default` Docker bridge.

Deployment currently syncs `src/`, `vps_requirements.txt`, and `deployment/systemd/` via `deployment/redeploy_host_orchestrator_rsync.sh`. The new repair automation needs repo-tracked files under `deployment/`, so the rsync deploy should sync the full `deployment/` tree instead of only `deployment/systemd/`.

## Plan of Work

First, add a deployment script that runs on the VPS as root. It will discover the current `azuracast_default` network ID and subnet, derive the Linux bridge name, remove stale UFW port-`8787` bridge rules for old `br-*` interfaces, ensure the current bridge has the needed allow rule, and verify that the `azuracast` container can reach both the direct admin API backend and the nginx-proxied `/admin-http` path. The script will also run a final public `healthz` check against `https://neuralcast.duckdns.org/admin-http/healthz`.

Second, add a repo-tracked cron file under `deployment/cron/` that runs the repair script on Mondays at `04:45` in the same Europe/Paris timezone as the AzuraCast update. That gives the existing `04:00` update substantial time to finish while keeping the schedule deterministic. The cron command will append output to a dedicated log file under `/var/log/`.

Third, update the rsync deploy script so the full `deployment/` directory, including the new cron file and repair script, is synced to `/root/radio_host_orchestrator/deployment/`. Update the deployment/admin API docs with install instructions and troubleshooting notes.

Finally, deploy the new files to the VPS, install the cron file into `/etc/cron.d/`, run the repair script manually once to validate it on the current host, and verify that the public admin API remains reachable.

## Concrete Steps

Run from the repository root (`/home/ungern/Dropbox/Documents/Projects_and_Coding/Media_and_Content/NeuralCast`):

1. Add the repair script and repo-tracked cron file under `deployment/`.
2. Update `deployment/redeploy_host_orchestrator_rsync.sh`, `deployment/INSTRUCTIONS.md`, and `docs/admin_api.md`.
3. Run `bash -n` on the new shell script and, if useful, a manual local dry read of the cron file.
4. Deploy with `./deployment/redeploy_host_orchestrator_rsync.sh`.
5. Install the cron file on the VPS:
   `sudo cp /root/radio_host_orchestrator/deployment/cron/neuralcast-admin-api-post-azuracast-update /etc/cron.d/neuralcast-admin-api-post-azuracast-update`
6. Run the repair script manually on the VPS and verify the public health/capabilities endpoint.

## Validation and Acceptance

Acceptance is behavioral:

- The VPS contains the repo-tracked repair script and cron file under `/root/radio_host_orchestrator/deployment/`.
- `/etc/cron.d/neuralcast-admin-api-post-azuracast-update` is installed and points at the repo-tracked script.
- Running the repair script manually succeeds on the VPS and confirms backend, proxy, and public health checks.
- `ufw status verbose` shows an allow rule for port `8787` on the current `azuracast_default` bridge.
- `https://neuralcast.duckdns.org/admin-http/healthz` remains reachable after the manual run.

## Idempotence and Recovery

The repair script is intended to be safe to rerun. If the current bridge rule already exists, it should not add duplicates. If stale bridge rules are present from earlier Docker networks, it should delete them. The health checks make failures visible in the cron log so the repair path can be rerun manually if needed.

## Artifacts and Notes

Key deployed artifacts:

- `/root/radio_host_orchestrator/deployment/repair_admin_api_bridge_after_azuracast_update.sh`
- `/etc/cron.d/neuralcast-admin-api-post-azuracast-update`

Key verification notes:

- `sudo /root/radio_host_orchestrator/deployment/repair_admin_api_bridge_after_azuracast_update.sh` succeeded on the VPS on March 16, 2026
- `ufw status verbose` now shows only the current bridge rule for port `8787`
- `https://neuralcast.duckdns.org/admin-http/healthz` returns `{"status":"ok"}`
- `https://neuralcast.duckdns.org/admin-http/admin/capabilities` returns `200 OK` with the expected JSON
