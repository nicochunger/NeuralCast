# Extend Admin API With Station Reads And Schedule Jobs

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work proceeds.

This repository includes `.agent/PLANS.md`; this document is maintained in accordance with `.agent/PLANS.md`.

## Purpose / Big Picture

After this change, the Android admin app can discover richer server capabilities, inspect the live station state, and trigger a dry-run or live schedule-generator job without SSH. A user can verify the change by calling new authenticated read endpoints for capabilities, now-playing, and queue state, then submitting `POST /admin/run-schedule-generator` and polling the existing jobs endpoint until the schedule job finishes.

## Progress

- [x] (2026-03-15 10:25Z) Read the current admin API package, the existing ExecPlan from phase 1, the AzuraCast transport helpers, and the schedule-generator CLI/runtime entrypoint.
- [x] (2026-03-15 12:52Z) Added `AdminStationService`, reusing the existing AzuraCast transport parsing helpers for live now-playing and queue payloads.
- [x] (2026-03-15 12:58Z) Generalized persisted admin jobs with an `operation` discriminator and added schedule-generator job launch support with backward-compatible legacy job reads.
- [x] (2026-03-15 13:10Z) Added the new endpoints, updated docs/tests, redeployed to the VPS, and verified the live HTTPS endpoints plus a successful dry-run schedule-generator job.

## Surprises & Discoveries

- Observation: The current job JSON schema is specialized for force-archetype work and does not record a generic operation name.
  Evidence: `src/neuralcast/admin_api/jobs.py` currently persists `station`, `archetype`, `track_focus`, and `dry_run`, but no operation discriminator.
- Observation: The public admin API proxy and live AzuraCast credentials were already sufficient for the new read endpoints; no extra nginx or environment changes were required for phase 2.
  Evidence: `GET /admin/stations/neuralforge/now-playing`, `GET /admin/stations/neuralforge/queue`, and `POST /admin/run-schedule-generator` all succeeded immediately after the code deploy and admin API restart.

## Decision Log

- Decision: Keep `/admin/options` for backward compatibility and add a richer `/admin/capabilities` endpoint instead of replacing the existing route.
  Rationale: The Android app can migrate incrementally, and any existing callers of `/admin/options` keep working unchanged.
  Date/Author: 2026-03-15 / Codex

## Outcomes & Retrospective

Phase 2 shipped without widening the trusted surface area beyond the agreed operations. The admin API now exposes a richer capabilities contract, read-only live station state, and a second job-backed write endpoint for the schedule generator while still delegating execution to the existing CLI modules.

Validation completed in three layers:

- `UV_CACHE_DIR=/tmp/uv-cache uv run python -m unittest tests.test_admin_api -v`
- `UV_CACHE_DIR=/tmp/uv-cache uv run python -m unittest tests.test_story_orchestrator -v`
- live HTTPS verification against `https://neuralcast.duckdns.org/admin-http`, including a successful dry-run `schedule_generator` job (`20260315T131008Z-neuralforge-schedule_generator`)

## Context and Orientation

The current admin API lives under `src/neuralcast/admin_api/`. `app.py` defines the FastAPI routes and request/response models. `jobs.py` owns persisted job JSON files under `admin_http/jobs/` plus per-job logs under `admin_http/logs/`. `runner.py` is a detached background module that reads one job JSON file, launches the real CLI subprocess, and writes back status updates.

The host-orchestrator AzuraCast read helpers already exist under `src/neuralcast/pipelines/host_orchestrator/transport.py`. That module provides `AzuraCastClient`, `extract_current_track`, `extract_current_listeners`, `parse_queue_tracks`, and `choose_upcoming_tracks`. Reusing those helpers keeps the new HTTP station endpoints thin and avoids duplicating payload parsing rules.

The weekly schedule generator already has a stable CLI entrypoint at `src/neuralcast/cli/schedule_generator.py`, which calls `src/neuralcast/pipelines/schedule_generator/main.py`. The new admin API write endpoint should launch that existing CLI module the same way the force-archetype path launches `neuralcast.cli.host_orchestrator`.

## Plan of Work

First, add a new admin-only station helper module under `src/neuralcast/admin_api/` that builds an AzuraCast client from the existing environment variables, validates station slugs against the repo allowlist, and exposes capability, now-playing, and queue payload builders. The helper will call the existing transport parsing functions so the HTTP layer stays thin.

Second, refactor `src/neuralcast/admin_api/jobs.py` so persisted jobs include an `operation` field. The job manager will gain a second enqueue method for schedule-generator jobs, and the detached runner will dispatch between the existing host-orchestrator CLI invocation and the new `python -m neuralcast.cli.schedule_generator -s <station> [--dry-run]` invocation. Old job JSON files that predate the new `operation` field will default to `force_archetype` when read.

Third, extend `src/neuralcast/admin_api/app.py` with `GET /admin/capabilities`, `GET /admin/stations/{station}/now-playing`, `GET /admin/stations/{station}/queue`, and `POST /admin/run-schedule-generator`. The new schedule-generator endpoint will use the same accepted-job response pattern and the same persisted jobs endpoint. The status response will include an `operation` string and keep archetype/track-focus optional so both job types can share one schema.

Fourth, update `tests/test_admin_api.py` with unit coverage for the new capability payload, schedule-generator job command shape, backward-compatible job parsing, and the new station helper methods. Then update `docs/admin_api.md`, redeploy `src/` to the VPS with `./deployment/redeploy_host_orchestrator_rsync.sh`, restart `neuralcast-admin-api.service`, and verify the live capabilities/read endpoints plus one dry-run schedule-generator job submission.

## Concrete Steps

Run from the repository root (`/home/ungern/Dropbox/Documents/Projects_and_Coding/Media_and_Content/NeuralCast`):

1. Edit `src/neuralcast/admin_api/` modules and add any new helper file.
2. Update `tests/test_admin_api.py` and `docs/admin_api.md`.
3. Run:
   `UV_CACHE_DIR=/tmp/uv-cache uv run python -m unittest tests.test_admin_api -v`
4. Optionally run the host-orchestrator helper suite again if shared models are touched:
   `UV_CACHE_DIR=/tmp/uv-cache uv run python -m unittest tests.test_story_orchestrator -v`
5. Deploy the updated `src/` tree to the VPS:
   `./deployment/redeploy_host_orchestrator_rsync.sh`
6. Restart the VPS admin API service:
   `ssh neuralvps 'sudo systemctl restart neuralcast-admin-api.service && sudo systemctl status neuralcast-admin-api.service --no-pager'`
7. Verify the live service:
   `curl -sS https://neuralcast.duckdns.org/admin-http/healthz`

## Validation and Acceptance

Acceptance is behavioral:

- `GET /admin/capabilities` returns stations, archetypes, track-focus metadata, and the supported write operations.
- `GET /admin/stations/neuralforge/now-playing` returns the parsed current track plus remaining seconds and listener count.
- `GET /admin/stations/neuralforge/queue` returns the parsed upcoming queue and identifies the next track using existing transport helpers.
- `POST /admin/run-schedule-generator` returns `202 Accepted` immediately with a job id.
- `GET /admin/jobs/{job_id}` returns `operation="schedule_generator"` plus timestamps, exit code, and log tail for schedule jobs.
- Existing `POST /admin/force-archetype` and `GET /admin/options` behavior remains intact.

## Idempotence and Recovery

The new read-only endpoints are safe to call repeatedly. The schedule-generator endpoint will keep the existing one-running-job-per-station protection used by the force-archetype job path, which prevents overlapping admin jobs for the same station. The VPS deploy step is safe to rerun because the rsync script is already additive and repeatable.

## Artifacts and Notes

Key verification highlights:

- `GET /admin/capabilities` returned the expected operations map, including `force_archetype` and `schedule_generator`.
- `GET /admin/stations/neuralforge/now-playing` returned the current track plus remaining seconds/listener count from live AzuraCast data.
- `GET /admin/stations/neuralforge/queue?limit=3` returned a parsed queue and `next_track`.
- `POST /admin/run-schedule-generator` returned `202 Accepted`, and the persisted job finished with `status="succeeded"` and `exit_code=0`.

## Interfaces and Dependencies

The updated admin API should expose these additional interfaces:

- `neuralcast.admin_api.stations.AdminStationService`
- `JobRecord.operation: str`
- `JobManager.enqueue_schedule_generator(station: str, dry_run: bool) -> JobRecord`
- `GET /admin/capabilities`
- `GET /admin/stations/{station}/now-playing`
- `GET /admin/stations/{station}/queue`
- `POST /admin/run-schedule-generator`

The schedule-generator job command must remain a strict argv list with no arbitrary flags, using the existing CLI module `neuralcast.cli.schedule_generator`.
