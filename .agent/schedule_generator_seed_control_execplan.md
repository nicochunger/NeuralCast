# Add Seed Control And Advanced Scheduler Options To The Admin API

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work proceeds.

This repository includes `.agent/PLANS.md`; this document is maintained in accordance with `.agent/PLANS.md`.

## Purpose / Big Picture

After this change, the schedule generator can be driven in two distinct ways: deterministic weekly automation for cron-style runs, and explicit rerolls for interactive admin/API usage. A user can verify this by calling the admin schedule endpoint with `seed_mode="fresh"` to get a new weekly plan, or with `seed_mode="stable_week"` to reproduce the same plan for the same week and inputs. The same API call can also request `force_apply`, set a week start date, and adjust open-slot and block-size bounds.

## Progress

- [x] (2026-03-15 15:10Z) Reviewed `.agent/PLANS.md`, the existing schedule-generator pipeline, the admin API request/response models, and the current scheduler tests to identify deterministic seed behavior and the current CLI/API option surface.
- [x] (2026-03-15 15:27Z) Added scheduler seed-mode support (`stable_week`, `fresh`, `custom`) and persisted `seed_mode`, `seed_salt`, and `resolved_seed` in generated plan/state output.
- [x] (2026-03-15 15:36Z) Extended the schedule-generator CLI and admin HTTP job pipeline with force-apply, week-start, seed, and tuning arguments; admin jobs now persist resolved scheduler options including auto-generated fresh salts.
- [x] (2026-03-15 15:40Z) Updated admin capability metadata/docs/tests and ran local unit validation (`python -m unittest tests.test_schedule_generator tests.test_admin_api -v`).
- [x] (2026-03-15 15:53Z) Ran the mandatory VPS rsync redeploy script and verified the remote `src/neuralcast/pipelines/schedule_generator/main.py` entrypoint timestamp updated on `/root/radio_host_orchestrator`.

## Surprises & Discoveries

- Observation: The scheduler already uses randomness internally, but it is seeded from a stable hash of station/week/playlist/configuration inputs, so repeated runs with the same inputs intentionally converge to the same result.
  Evidence: `src/neuralcast/pipelines/schedule_generator/generation.py` computes `_stable_seed(...)` from station slug, week start, timezone, playlist signature, and tuning bounds, then uses `random.Random(seed + attempt_offset)`.
- Observation: A seed-behavior unit test cannot use only two enabled playlists because the scheduler’s no-repeat rules may require more distinct playlist blocks than that input can satisfy.
  Evidence: The initial test run failed with `Template contains ... playlist blocks but only 2 enabled playlists are available for no-repeat scheduling.` until the test fixture was expanded to twelve enabled playlists for the full-plan generation case.

## Decision Log

- Decision: Keep the CLI default seed mode deterministic (`stable_week`) and make the admin API request model default to `fresh`.
  Rationale: Scheduled automation should remain reproducible and idempotent, while interactive admin usage should default to “generate a new schedule” semantics without requiring the caller to discover an extra flag first.
  Date/Author: 2026-03-15 / Codex
- Decision: Resolve and persist a concrete `seed_salt` for fresh admin jobs before launching the subprocess.
  Rationale: Fresh runs should still be auditable and reproducible after the fact; storing the resolved salt in the job record and passing it on the CLI preserves that.
  Date/Author: 2026-03-15 / Codex

## Outcomes & Retrospective

The scheduler and admin API now support explicit reroll semantics without sacrificing deterministic automation. CLI callers still default to `stable_week`, while admin HTTP schedule requests default to `fresh`, and fresh admin jobs persist a resolved `seed_salt` so the exact run remains auditable. The admin capabilities payload now advertises these controls, and job status responses include `schedule_options` for clients that want to inspect what was actually requested.

Local validation passed with:

    python -m unittest tests.test_schedule_generator tests.test_admin_api -v

The mandatory VPS redeploy also completed successfully via `./deployment/redeploy_host_orchestrator_rsync.sh`, and the verification summary showed the updated remote schedule-generator entrypoint plus the expected absence of removed legacy pipeline files.

## Context and Orientation

The scheduler entrypoint lives at `src/neuralcast/pipelines/schedule_generator/main.py`. It parses CLI arguments, fetches station/playlist data from AzuraCast, builds a `WeeklySchedulePlan`, optionally applies it to AzuraCast, and persists local scheduler state under `<station>/metadata/ai_schedule_state.json`.

The schedule generation algorithm itself lives in `src/neuralcast/pipelines/schedule_generator/generation.py`. That module currently derives one deterministic seed from the station, week, playlist configuration, and tuning parameters. It then explores multiple attempts by offsetting that seed, but the entire sequence remains stable for identical inputs.

The admin HTTP API lives under `src/neuralcast/admin_api/`. `app.py` validates requests and exposes FastAPI routes. `jobs.py` persists job metadata under `admin_http/jobs/`, builds CLI subprocess arguments, and launches detached runner processes. `runner.py` executes the real CLI modules and writes logs/status back to disk. Any new scheduler options added to the HTTP API must be serialized into the persisted job record so the detached runner can reconstruct the command exactly.

The existing admin capabilities endpoint currently exposes only coarse booleans (`dry_run_supported`, `track_focus_supported`). This change will widen the schedule-generator capability payload enough for callers to discover supported seed modes and advanced scheduler controls without replacing the whole endpoint contract.

## Plan of Work

First, extend the scheduler generation code so it accepts a seed mode and optional seed salt. Add a small, explicit seed-resolution helper in `src/neuralcast/pipelines/schedule_generator/generation.py` that produces three modes: `stable_week` (existing deterministic behavior), `fresh` (new salt if omitted, different result on each interactive run), and `custom` (caller-supplied salt for reproducible rerolls). Update `WeeklySchedulePlan` in `src/neuralcast/pipelines/schedule_generator/models.py` so plan/state JSON records `seed_mode`, `seed_salt`, and the final integer seed used by the generator.

Second, extend the scheduler CLI in `src/neuralcast/pipelines/schedule_generator/main.py` to accept `--seed-mode`, `--seed-salt`, `--force-apply`, `--week-start-date`, and the existing tuning arguments as a coherent surface. Preserve the current default behavior for CLI callers by defaulting `--seed-mode` to `stable_week`.

Third, update the admin API in `src/neuralcast/admin_api/app.py` and `src/neuralcast/admin_api/jobs.py`. The schedule request body should accept the advanced scheduler options, default `seed_mode` to `fresh`, validate the numeric bounds before job enqueue, and persist the full scheduler option set in the job JSON. The command builder should then emit the real CLI command with the explicit flags required to reproduce that exact admin-triggered run.

Fourth, expand `JobStatusResponse` and `/admin/capabilities` so clients can inspect the scheduler option set for a job and discover what scheduler controls are available. The capabilities entry for `schedule_generator` should describe `force_apply`, `week_start_date`, supported seed modes, and the supported tuning field names. Keep the response backward compatible by only adding new optional fields.

Fifth, update `tests/test_admin_api.py`, `tests/test_schedule_generator.py`, and `docs/admin_api.md` so the new behavior is covered and documented. Then run the relevant unit tests and the mandatory VPS redeploy script because schedule-generator runtime code is changing.

## Concrete Steps

Run from repository root (`/home/nicou/Dropbox/Documents/Projects_and_Coding/Media_and_Content/NeuralCast`):

1. Edit:
   `src/neuralcast/pipelines/schedule_generator/generation.py`
   `src/neuralcast/pipelines/schedule_generator/models.py`
   `src/neuralcast/pipelines/schedule_generator/main.py`
   `src/neuralcast/admin_api/app.py`
   `src/neuralcast/admin_api/jobs.py`
   `tests/test_schedule_generator.py`
   `tests/test_admin_api.py`
   `docs/admin_api.md`
2. Run local unit coverage:
   `python -m unittest tests.test_schedule_generator tests.test_admin_api -v`
3. Redeploy the updated schedule-generator/admin API code to the VPS:
   `./deployment/redeploy_host_orchestrator_rsync.sh`

## Validation and Acceptance

Acceptance is behavioral:

- A CLI dry-run with the same inputs and `--seed-mode stable_week` produces the same `plan_hash` on repeated runs.
- A CLI dry-run with `--seed-mode custom --seed-salt reroll-a` produces a stable plan that differs from another custom salt.
- The admin schedule endpoint accepts `seed_mode`, `seed_salt`, `force_apply`, `week_start_date`, and tuning args, and persists those options in the resulting job state.
- The admin schedule command builder emits the expected CLI flags.
- `/admin/capabilities` advertises the new schedule-generator controls without removing the existing fields.

## Idempotence and Recovery

The new seed controls are additive. Repeating the same `stable_week` or `custom` request is safe and reproducible. Repeating a `fresh` request is intentionally non-idempotent, but the resolved salt will still be recorded in plan/job state so the result can be traced. If a job fails, rerunning it with the same `seed_mode/custom seed_salt` reproduces the same plan shape.

## Artifacts and Notes

The most important evidence to capture after implementation is:

    python -m unittest tests.test_schedule_generator tests.test_admin_api -v

    curl -sS -H "Authorization: Bearer <token>" \
      -H "Content-Type: application/json" \
      -d '{"station":"neuralforge","dry_run":true,"seed_mode":"fresh"}' \
      https://neuralcast.duckdns.org/admin-http/admin/run-schedule-generator

The job status JSON should then show the persisted scheduler options, including the resolved `seed_salt` for a fresh run.

## Interfaces and Dependencies

At the end of this change, these interfaces must exist:

- `neuralcast.pipelines.schedule_generator.generation.SCHEDULE_SEED_MODE_STABLE_WEEK`
- `neuralcast.pipelines.schedule_generator.generation.SCHEDULE_SEED_MODE_FRESH`
- `neuralcast.pipelines.schedule_generator.generation.SCHEDULE_SEED_MODE_CUSTOM`
- `neuralcast.pipelines.schedule_generator.generation.SUPPORTED_SCHEDULE_SEED_MODES`
- `neuralcast.pipelines.schedule_generator.generation.build_weekly_plan_with_code(..., seed_mode: str = ..., seed_salt: str | None = ...)`
- `WeeklySchedulePlan.seed_mode`, `WeeklySchedulePlan.seed_salt`, and `WeeklySchedulePlan.resolved_seed`
- `ScheduleGeneratorRequest` fields for force/apply, seed control, week start date, and tuning values
- `JobRecord.schedule_options: dict[str, object] | None`

Change note: Initial ExecPlan created at implementation start to satisfy `.agent/PLANS.md` for this scheduler/admin API feature.
Change note: Updated after implementation to record the shipped seed-control design, test discovery about playlist count, and successful local unit validation.
Change note: Updated after the mandatory VPS redeploy to record the completed remote sync and verification outcome.
