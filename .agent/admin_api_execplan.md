# Implement Phase-1 Admin HTTP API For Host Orchestrator

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work proceeds.

This repository includes `.agent/PLANS.md`; this document is maintained in accordance with `.agent/PLANS.md`.

## Purpose / Big Picture

After this change, an Android app or any other HTTPS client can trigger an immediate host-orchestrator run without SSH access. The new behavior is a small authenticated HTTP service that validates a station and archetype, launches the existing `neuralcast.cli.host_orchestrator` command as a subprocess, stores job state on disk, and exposes job status and logs through HTTP. A user can verify the change by starting the local API, sending a bearer-authenticated `POST /admin/force-archetype` request with `dry_run=true`, receiving `202 Accepted`, and then polling `GET /admin/jobs/{job_id}` until the job exits.

## Progress

- [x] (2026-03-14 13:57Z) Read `.agent/PLANS.md`, host orchestrator CLI/runtime wiring, shared path helpers, dependency files, and deployment docs.
- [x] (2026-03-14 20:20Z) Implemented `src/neuralcast/admin_api/` with FastAPI endpoints, bearer-token auth, disk-backed job records, detached runner subprocesses, and log-tail reporting.
- [x] (2026-03-14 20:22Z) Added `python -m neuralcast.cli.admin_api`, updated `pyproject.toml` and `vps_requirements.txt`, and added canonical deployment artifacts/docs including a systemd unit.
- [x] (2026-03-14 20:29Z) Added automated coverage in `tests/test_admin_api.py` for route registration, auth validation, request validation, command shaping, and the job-manager lifecycle.
- [x] (2026-03-14 20:33Z) Ran local verification: compile checks, `unittest` suite, and a manual localhost curl flow that accepted a dry-run job and returned persisted failed status/log output when the orchestrator hit the intentionally unreachable AzuraCast test URL.

## Surprises & Discoveries

- Observation: The real host-orchestrator CLI is already a thin wrapper around `neuralcast.pipelines.host_orchestrator.run`, which means the admin API can stay thin and launch the existing CLI instead of importing runtime logic directly.
  Evidence: `src/neuralcast/cli/host_orchestrator.py` only parses args and calls `run(args)`.
- Observation: The repository already centralizes repo-root and station path logic in `src/neuralcast/config.py`.
  Evidence: `PROJECT_ROOT` and `ALLOWED_STATION_SLUGS` are exported there, and host-orchestrator utilities already use those helpers.
- Observation: Importing `neuralcast.pipelines.host_orchestrator.models` unexpectedly loaded the full orchestrator runtime because `src/neuralcast/pipelines/host_orchestrator/__init__.py` re-exported `main.py` at import time.
  Evidence: `uv run python -u -c "from neuralcast.admin_api.app import create_app; print(789)"` hung until `__init__.py` stopped importing `main.py`.
- Observation: In-process FastAPI test harnesses (`TestClient` and `httpx.ASGITransport`) hung in this environment even for a trivial `/healthz` app, while a real uvicorn process plus `curl` behaved correctly.
  Evidence: `TestClient(create_app()).get("/healthz")` never returned, but manual `curl` against `uv run python -m neuralcast.cli.admin_api` returned `{"status":"ok"}` immediately.

## Decision Log

- Decision: Use FastAPI plus uvicorn for the new admin service.
  Rationale: The feature needs JSON request/response handling, bearer-authenticated endpoints, and a small maintainable HTTP layer; FastAPI keeps the endpoint surface concise without pulling orchestrator logic into the HTTP module.
  Date/Author: 2026-03-14 / Codex
- Decision: Persist admin API job files under `PROJECT_ROOT / "admin_http"` instead of hardcoding `/root/radio_host_orchestrator` in code.
  Rationale: The repository is the source of truth and must run both locally and on the VPS. On the VPS, `PROJECT_ROOT` will still resolve to `/root/radio_host_orchestrator`.
  Date/Author: 2026-03-14 / Codex
- Decision: Launch a detached runner module per admin job, and let that runner spawn the real `neuralcast.cli.host_orchestrator` command.
  Rationale: This keeps the HTTP layer thin while allowing job JSON to be updated even if the API service restarts after handing off the job.
  Date/Author: 2026-03-14 / Codex
- Decision: Remove package-level `main.py` imports from `src/neuralcast/pipelines/host_orchestrator/__init__.py` and point the CLI wrapper directly at `main.py`.
  Rationale: The admin API must import `Archetype` without paying the cost or side effects of importing the entire orchestrator runtime.
  Date/Author: 2026-03-14 / Codex
- Decision: Keep automated tests focused on the job layer, auth helper, request model, and route registration instead of in-process ASGI requests.
  Rationale: The local runtime combination caused TestClient/ASGITransport hangs unrelated to feature behavior, and a real uvicorn + curl flow now covers the HTTP path end to end.
  Date/Author: 2026-03-14 / Codex

## Outcomes & Retrospective

Phase 1 now exists as a canonical repo feature rather than a VPS-only patch. The new admin API lives under `src/neuralcast/admin_api/`, listens on localhost by default, authenticates `/admin/...` routes with `NEURALCAST_ADMIN_HTTP_TOKEN`, exposes the required options/job endpoints, and launches the real host-orchestrator CLI with a strict argv list and no arbitrary flags. Job JSON and logs persist under `admin_http/`, and a detached runner updates final job state after the API returns `202 Accepted`.

Validation covered both code-level and runtime behavior. `uv run python -m unittest tests.test_admin_api -v` passed nine targeted tests. `uv run python -m compileall ...` compiled the new modules successfully. A real uvicorn process on `127.0.0.1:8878` returned the expected `/healthz` and `/admin/options` responses, accepted a `POST /admin/force-archetype` request, and then returned a persisted failed job status with `exit_code=1` and a log tail when the intentionally invalid AzuraCast URL (`http://127.0.0.1:9`) forced the spawned dry-run orchestrator subprocess to fail fast.

Remaining gap: the manual curl validation intentionally used a failing AzuraCast URL so the verification could complete locally and quickly without relying on live station infrastructure. A live VPS smoke test against real AzuraCast credentials is still recommended after deployment.

## Context and Orientation

The existing host orchestrator lives under `src/neuralcast/pipelines/host_orchestrator/` and is invoked through `src/neuralcast/cli/host_orchestrator.py`. Its CLI already accepts `--force-archetype`, validates stations through `ALLOWED_STATION_SLUGS`, and validates archetypes through the real `Archetype` enum in `src/neuralcast/pipelines/host_orchestrator/models.py`. That means the new HTTP service does not need to know how an archetype run works internally. It only needs to validate the incoming request, spawn the existing CLI process safely, and track the resulting job.

The repository already defines `PROJECT_ROOT` in `src/neuralcast/config.py`. That helper should anchor the new disk layout for admin API state:

- `PROJECT_ROOT / "admin_http" / "jobs" / "<job_id>.json"` for persisted job state.
- `PROJECT_ROOT / "admin_http" / "logs" / "<job_id>.log"` for stdout/stderr capture.

The user-facing HTTP interface is intentionally small. `GET /healthz` reports liveness. `GET /admin/options` tells clients which stations and archetypes are valid. `POST /admin/force-archetype` creates a background job immediately and returns a job identifier. `GET /admin/jobs/{job_id}` returns persisted job status, timestamps, exit code, log path, and a short tail of the log content. Every `/admin/...` endpoint must require `Authorization: Bearer <token>` where the expected token comes from `NEURALCAST_ADMIN_HTTP_TOKEN`.

## Plan of Work

Implementation will proceed in four passes. First, add a new package at `src/neuralcast/admin_api/`. `jobs.py` will define the disk-backed job model, functions to create directories, create a job file, detect whether a station already has a running job, launch the real CLI subprocess with an argv list, capture output to a per-job log file, and refresh persisted state by polling child process completion when status is read. `app.py` will define the FastAPI application, bearer-token authentication, request/response models, and endpoint handlers that call the job layer. The service code will import `Archetype` and `ALLOWED_STATION_SLUGS` instead of duplicating allowed values.

Second, add a runnable CLI module at `src/neuralcast/cli/admin_api.py`. It will parse `--host` and `--port` arguments, default to `127.0.0.1` and a documented port, and start uvicorn against the FastAPI app object. The entrypoint must remain thin so deployment commands stay stable and obvious.

Third, add tests in `tests/test_admin_api.py`. The tests will cover: bearer token enforcement, `/admin/options` values, job creation, per-station conflict behavior returning HTTP 409, persisted status updates when a child process exits, and safe rejection of invalid stations or archetypes. The job layer tests will stub subprocess creation so tests do not run the real orchestrator.

Fourth, update packaging and deployment artifacts. `pyproject.toml` and `vps_requirements.txt` will gain the minimal runtime dependencies for FastAPI and uvicorn. A systemd service unit file will be added under `deployment/systemd/`. A short deployment guide will be added under `docs/` covering environment variables, local start, systemd usage, reverse proxy examples, curl commands, and exact VPS deployment steps. After code changes, run tests and a manual curl flow with `dry_run=true`.

## Concrete Steps

Run from repository root (`/home/ungern/Dropbox/Documents/Projects_and_Coding/Media_and_Content/NeuralCast`):

1. Add the new admin API package and CLI entrypoint.
2. Add tests in `tests/test_admin_api.py`.
3. Update `pyproject.toml`, `vps_requirements.txt`, and deployment/docs files.
4. Run:
   python -m pytest tests/test_admin_api.py -q
5. Start the API locally with a temporary token:
   NEURALCAST_ADMIN_HTTP_TOKEN=test-token PYTHONPATH=$(pwd)/src python -m neuralcast.cli.admin_api --host 127.0.0.1 --port 8787
6. In another shell, create a dry-run job:
   curl -sS -H "Authorization: Bearer test-token" -H "Content-Type: application/json" -d '{"station":"neuralforge","archetype":"deep_dive","dry_run":true}' http://127.0.0.1:8787/admin/force-archetype
7. Poll the returned `job_id`:
   curl -sS -H "Authorization: Bearer test-token" http://127.0.0.1:8787/admin/jobs/<job_id>

Expected behavior includes HTTP 202 for accepted jobs, persisted job JSON/log files under `admin_http/`, and a terminal log that shows the real host-orchestrator CLI argv being launched without `shell=True`.

## Validation and Acceptance

Acceptance is behavioral:

- `GET /healthz` returns HTTP 200 and a simple healthy payload without authentication.
- `GET /admin/options` returns exactly the two supported stations and all archetype values derived from `Archetype`.
- `POST /admin/force-archetype` rejects missing or wrong bearer tokens with HTTP 401.
- `POST /admin/force-archetype` rejects a second concurrent job for the same station with HTTP 409.
- Successful job creation writes both a JSON record and a log path under `admin_http/`.
- `GET /admin/jobs/{job_id}` returns timestamps, exit code when available, and a short tail of the log text.
- A manual dry-run request launches the real orchestrator CLI subprocess immediately and returns without waiting for completion.

## Idempotence and Recovery

The new API writes additive job files and log files; repeated test runs are safe because each job gets a unique identifier. If a subprocess dies unexpectedly or the HTTP service restarts, reading job status will refresh the persisted JSON based on the stored process information when available, and historical finished job files remain readable. If local manual testing leaves behind temporary admin job files, they can be deleted manually from `admin_http/` without touching station runtime state.

## Artifacts and Notes

Validation transcripts:

    UV_CACHE_DIR=/tmp/uv-cache uv run python -m unittest tests.test_admin_api -v
    # Ran 9 tests in 0.019s
    # OK

    UV_CACHE_DIR=/tmp/uv-cache uv run python -m compileall src/neuralcast/admin_api src/neuralcast/cli/admin_api.py src/neuralcast/cli/host_orchestrator.py src/neuralcast/pipelines/host_orchestrator/__init__.py tests/test_admin_api.py
    # Listing 'src/neuralcast/admin_api'...
    # Compiling 'src/neuralcast/cli/host_orchestrator.py'...
    # Compiling 'tests/test_admin_api.py'...

    curl -sS http://127.0.0.1:8878/healthz
    # {"status":"ok"}

    curl -sS -H 'Authorization: Bearer test-token' http://127.0.0.1:8878/admin/options
    # {"stations":["neuralcast","neuralforge"],"archetypes":["back_sell",...,"ultra_minimal"]}

    curl -sS -H 'Authorization: Bearer test-token' -H 'Content-Type: application/json' -d '{"station":"neuralforge","archetype":"deep_dive","dry_run":true}' http://127.0.0.1:8878/admin/force-archetype
    # {"job_id":"20260314T203126Z-neuralforge-deep_dive","status":"accepted"}

    curl -sS -H 'Authorization: Bearer test-token' http://127.0.0.1:8878/admin/jobs/20260314T203126Z-neuralforge-deep_dive
    # {"status":"failed","exit_code":1,...,"log_tail":"...Connection refused..."}

## Interfaces and Dependencies

The implementation should create these stable interfaces:

- `neuralcast.admin_api.app.create_app(job_manager: JobManager | None = None) -> FastAPI`
- `neuralcast.admin_api.jobs.JobManager`
- `neuralcast.cli.admin_api.main() -> None`

`JobManager` should encapsulate:

- base directory setup for jobs/logs,
- job creation and persistence,
- single-running-job-per-station checks,
- subprocess spawning with argv lists,
- status refresh and log-tail retrieval.

Runtime dependencies added for this feature should be the minimum needed to serve HTTP locally on the VPS:

- `fastapi`
- `uvicorn`

Test-only dependency if needed for FastAPI endpoint tests:

- `httpx`

Change note: Created this plan before implementation so the feature work, test steps, and deployment behavior can be tracked as a living document.

Change note (2026-03-14 20:33Z): Updated after implementation to record the detached-runner design, the import-side-effect fix in `host_orchestrator/__init__.py`, the automated/unit validation, and the real localhost curl verification flow.
