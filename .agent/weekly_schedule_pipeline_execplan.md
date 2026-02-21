# Implement Weekly AI Schedule Generator and Host Schedule Awareness

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work proceeds.

This repository includes `.agent/PLANS.md`; this document is maintained in accordance with `.agent/PLANS.md`.

## Purpose / Big Picture

After this change, the project can generate a fixed weekly schedule (same daily layout for all seven days) from available AzuraCast playlists, apply it directly to AzuraCast, and persist the schedule so the AI host can reference the active programming block on-air. A user can verify this by running the new scheduler command in dry-run/live mode and then observing `inject_story_snippet.py` prompts include current section and genre context.

## Progress

- [x] (2026-02-20 12:20Z) Reviewed existing orchestrator, AzuraCast integration points, and Plan requirements.
- [x] (2026-02-20 12:45Z) Added `src/neuralcast/pipelines/schedule_generator.py` with weekly fixed-template planning, LLM generation, validation, state persistence, and AzuraCast apply helpers.
- [x] (2026-02-20 12:46Z) Added scheduler CLI entrypoint (`src/neuralcast/cli/schedule_generator.py`) and repo-root shim (`schedule_generator.py`).
- [x] (2026-02-20 12:47Z) Added schedule prompt templates (`schedule_system.md`, `schedule_user.md`) for strict JSON output.
- [x] (2026-02-20 12:58Z) Integrated schedule context and schedule mention tracking into `src/neuralcast/pipelines/story_injector.py`.
- [x] (2026-02-20 13:03Z) Added `tests/test_schedule_generator.py` and extended `tests/test_story_orchestrator.py` with schedule-context coverage.
- [x] (2026-02-20 13:05Z) Ran compile and unit tests successfully for schedule + orchestrator helper coverage.
- [x] (2026-02-20 13:06Z) Attempted NeuralForge scheduler dry-run; environment lacked API/runtime deps (`AZURACAST_API_KEY`, `requests`), so live dry-run could not complete locally.
- [x] (2026-02-20 13:14Z) Ran mandatory 3-step VPS redeploy (twice, final sync after last source edit) and verified deployed `story_injector.py` and `schedule_generator.py` checksums match local files.
- [x] (2026-02-20 14:26Z) Added dedicated `block_intro` archetype with automatic force-on-block-start-window behavior and updated tests for this control path.
- [x] (2026-02-20 14:28Z) Re-ran mandatory 3-step VPS redeploy for `story_injector.py` edits and verified deployed `story_injector.py` + `wrapper_block_intro.md` checksums match local files.
- [x] (2026-02-20 15:18Z) Refactored scheduler generation into hybrid mode: deterministic time scaffold + LLM content decoration with strict boundary locking.
- [x] (2026-02-20 15:20Z) Added deterministic fallback when LLM output is invalid/unavailable, so weekly generation still succeeds without remote mutation.
- [x] (2026-02-20 15:21Z) Fixed AzuraCast schedule payload time serialization to HHMM integers (e.g., `04:00 -> 400`) to prevent `00:04` drift.
- [x] (2026-02-20 15:22Z) Expanded scheduler tests (9 total) for HHMM payload conversion, empty-day shape preservation, and deterministic template constraints.
- [x] (2026-02-20 15:25Z) Validated end-to-end with `python schedule_generator.py --station neuralforge --dry-run`; generated weekly plan with correct block boundaries.

## Surprises & Discoveries

- Observation: No existing station scheduling module exists in the repository; only queue injection and playlist sync are implemented.
  Evidence: `rg -n "schedule|playlist schedule|station/.*/playlist" src` returns only host-orchestrator and unrelated references.
- Observation: Local execution environment did not have core runtime dependencies (`requests`, `python-dotenv`, `openai`, `pandas`) installed, which prevented direct live API dry-runs.
  Evidence: import checks failed and `python schedule_generator.py --station neuralforge --dry-run` raised missing dependency/runtime environment errors.

## Decision Log

- Decision: Implement scheduler as a new pipeline (`src/neuralcast/pipelines/schedule_generator.py`) with its own CLI entrypoint.
  Rationale: This avoids overloading `story_injector.py` responsibilities while keeping station-specific logic reusable.
  Date/Author: 2026-02-20 / Codex
- Decision: Use a fixed weekly model with one 24-hour daily template duplicated for all 7 days.
  Rationale: User explicitly requested non-rolling weekly generation with same day-to-day schedule.
  Date/Author: 2026-02-20 / Codex
- Decision: Preserve "open slots" as times where no playlist-specific schedule is applied, allowing AzuraCast weighted random playback.
  Rationale: User explicitly requested support for unscheduled blocks that use AzuraCast default weighted behavior.
  Date/Author: 2026-02-20 / Codex
- Decision: Keep scheduler and orchestrator import-safe in environments without optional API packages by adding dependency guards and explicit runtime errors.
  Rationale: Unit tests for pure helper logic should run even when optional network dependencies are unavailable.
  Date/Author: 2026-02-20 / Codex
- Decision: Introduce a dedicated `block_intro` archetype and auto-force it during a block start grace window instead of relying only on prompt hints.
  Rationale: This yields deterministic, idempotent block intros despite cron timing jitter and cadence gates.
  Date/Author: 2026-02-20 / Codex
- Decision: Serialize AzuraCast `schedule_items.start_time/end_time` as HHMM integers instead of `HH:MM` strings.
  Rationale: API-side coercion of `HH:MM` strings caused leading-hour collapse (`04:00` becoming `00:04`) in live schedules.
  Date/Author: 2026-02-20 / Codex
- Decision: Seed LLM schedule generation from a deterministic scaffold and enforce boundary equivalence at validation time.
  Rationale: Keeps timing stable and valid while still allowing AI-driven section naming and playlist curation.
  Date/Author: 2026-02-20 / Codex
- Decision: Return a deterministic weekly template fallback when all LLM attempts fail validation.
  Rationale: Prevents pipeline hard-failures and keeps weekly automation reliable.
  Date/Author: 2026-02-20 / Codex

## Outcomes & Retrospective

Completed implementation of the new weekly schedule pipeline and host schedule-awareness integration. The code now supports fixed weekly (non-rolling) generation with one daily template repeated for all seven days, bounded open slots, AzuraCast apply hooks, and station metadata persistence under `ai_schedule_state.json`.

The host runtime now also supports a forced `block_intro` path for schedule transitions: when a new block is detected in its start window and intro has not yet aired, the run bypasses cadence/cooldown gating and forces a `block_intro` generation attempt.

The scheduler has now been hardened with deterministic timing semantics: it builds a deterministic daily scaffold first, uses LLM output only for compliant refinement, and falls back to deterministic output when LLM responses are invalid. AzuraCast payloads are serialized as HHMM integers to match live API expectations.

Remaining operational gap is environmental: live NeuralForge dry-run/apply requires valid AzuraCast credentials and runtime packages (`requests` plus network reachability). The local sandbox did not have these available, so validation is complete at compile/unit-test level but not full live API execution.

## Context and Orientation

`src/neuralcast/pipelines/story_injector.py` currently contains the live host runtime plus an embedded AzuraCast API client. It reads track/queue metadata, generates host scripts, synthesizes audio, uploads media, and queues segments. It persists orchestrator state under `<station>/metadata/ai_host_orchestrator_state.json`.

No scheduling pipeline currently exists. The new feature must add one and also make host prompts aware of the active schedule block. The schedule state should live under `<station>/metadata/ai_schedule_state.json` to mirror existing station metadata conventions.

The existing root shim `inject_story_snippet.py` points to `src/neuralcast/cli/story_injector.py`. We will add a parallel CLI entrypoint for scheduling and keep script behavior additive.

## Plan of Work

First, add `src/neuralcast/pipelines/schedule_generator.py` and define dataclasses for playlists, daily template blocks, and weekly schedule state. Implement AzuraCast reads/writes for station playlists and playlist schedule updates. Add dry-run mode that prints intended changes without remote mutation.

Second, add prompt files under `src/neuralcast/assets/stories/prompts/` and implement LLM generation with strict JSON output parsing, one repair attempt, and deterministic validation checks (coverage, overlap, duration bounds, open-slot bounds, known playlist IDs).

Third, integrate schedule context into `src/neuralcast/pipelines/story_injector.py`. Extend orchestrator state to track per-block schedule mentions (start and mid). Add schedule context lines into prompt assembly and update mention tracking only after successful segment injection.

Fourth, add tests in `tests/test_schedule_generator.py` and extend `tests/test_story_orchestrator.py` for schedule context and mention gating.

Finally, run validation commands, update this ExecPlan, and perform the mandatory 3-step VPS redeploy procedure because `story_injector.py` is modified.

## Concrete Steps

Run from repository root (`/home/nicou/Documents/NeuralCast`):

1. Implement scheduler pipeline and CLI files.
2. Add schedule prompt templates.
3. Integrate schedule context into story injector.
4. Add/update tests.
5. Validate:
   python -m compileall src/neuralcast/pipelines/schedule_generator.py src/neuralcast/pipelines/story_injector.py tests/test_schedule_generator.py tests/test_story_orchestrator.py
   python -m unittest tests.test_schedule_generator tests.test_story_orchestrator -v
6. Manual dry-run check on NeuralForge:
   python -m neuralcast.cli.schedule_generator --station neuralforge --dry-run
7. VPS redeploy (mandatory for `story_injector.py` edits):
   zip -r deployment/deploy_host_orchestrator.zip src vps_requirements.txt -x "*/__pycache__/*" "*.pyc" "src/neuralcast/assets/stories/snippets/*"
   scp deployment/deploy_host_orchestrator.zip neuralvps:~/deploy_host_orchestrator.zip
   ssh neuralvps 'cd /root && unzip -o deploy_host_orchestrator.zip -d radio_host_orchestrator'

## Validation and Acceptance

Acceptance is behavioral:

- `python -m neuralcast.cli.schedule_generator --station neuralforge --dry-run` prints a valid weekly plan summary and does not mutate AzuraCast.
- Live scheduler run updates AzuraCast playlist schedules and persists `<station>/metadata/ai_schedule_state.json`.
- `inject_story_snippet.py` prompt input includes active schedule block metadata when schedule state exists.
- Start and mid-block mention flags are tracked and not repeated within the same block.
- New tests for schedule validation and context gating pass.

## Idempotence and Recovery

Scheduler writes local state atomically. If an LLM output is invalid after repair, the run fails without remote mutation. Live apply uses full authoritative updates for all playlist schedule items, so rerunning with the same plan is idempotent.

If AzuraCast API calls fail mid-apply, the run logs the failure and exits non-zero; rerun is safe because full schedule payload is reapplied.

## Artifacts and Notes

Validation snippets:

    python -m unittest tests.test_schedule_generator -v
    # Ran 6 tests ... OK

    python -m unittest tests.test_story_orchestrator -v
    # Ran 13 tests ... OK

    python schedule_generator.py --help
    # New CLI flags shown for weekly scheduling workflow.

    AZURACAST_API_KEY=dummy python schedule_generator.py --station neuralforge --dry-run
    # RuntimeError: requests package is required for AzuraCast API calls...

    zip -r deployment/deploy_host_orchestrator.zip src vps_requirements.txt -x "*/__pycache__/*" "*.pyc" "src/neuralcast/assets/stories/snippets/*"
    scp deployment/deploy_host_orchestrator.zip neuralvps:~/deploy_host_orchestrator.zip
    ssh neuralvps 'cd /root && unzip -o deploy_host_orchestrator.zip -d radio_host_orchestrator'
    ssh neuralvps 'sha256sum /root/radio_host_orchestrator/src/neuralcast/pipelines/story_injector.py /root/radio_host_orchestrator/src/neuralcast/pipelines/schedule_generator.py'
    sha256sum src/neuralcast/pipelines/story_injector.py src/neuralcast/pipelines/schedule_generator.py
    # deployed checksums == local checksums for both pipeline files

    ssh neuralvps 'sha256sum /root/radio_host_orchestrator/src/neuralcast/pipelines/story_injector.py /root/radio_host_orchestrator/src/neuralcast/assets/stories/prompts/wrapper_block_intro.md'
    sha256sum src/neuralcast/pipelines/story_injector.py src/neuralcast/assets/stories/prompts/wrapper_block_intro.md
    # deployed checksums == local checksums for block-intro deployment artifacts

## Interfaces and Dependencies

New public entrypoint:

- `neuralcast.cli.schedule_generator.main() -> None`

New pipeline module APIs (internal but stable for tests):

- `neuralcast.pipelines.schedule_generator.build_arg_parser() -> argparse.ArgumentParser`
- `neuralcast.pipelines.schedule_generator.run(args: argparse.Namespace) -> None`
- `neuralcast.pipelines.schedule_generator.validate_daily_template(...) -> List[DailyTemplateBlock]`
- `neuralcast.pipelines.schedule_generator.expand_daily_template_to_week(...) -> List[ExpandedScheduleBlock]`

Story injector additions:

- Extend `OrchestratorState` with schedule mention tracking fields.
- Add helpers to load weekly schedule state and compute schedule context from local time.

Dependencies remain within existing stack: `requests`, `python-dotenv`, `google-genai`, and standard library modules (`zoneinfo`, `datetime`, `hashlib`, `json`).

Change note: Initial ExecPlan created at implementation start to satisfy `.agent/PLANS.md` for a complex feature.
Change note: Updated after implementation/testing with completed steps, validation outputs, dependency-related discoveries, and remaining redeploy task.
Change note: Updated after final VPS redeploy sync to record checksum verification for both updated pipeline files.
Change note: Updated after introducing forced `block_intro` behavior and final redeploy verification.
