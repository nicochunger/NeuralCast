# Replace Schedule Generator LLM Path With Code-Only Planner

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work proceeds.

This document must be maintained in accordance with `.agent/PLANS.md`.

## Purpose / Big Picture

After this change, `python schedule_generator.py ...` and `python -m neuralcast.cli.schedule_generator ...` will generate the weekly schedule entirely in Python code without Gemini/OpenAI calls, while keeping the existing CLI endpoints and arguments so cron jobs continue to work. The observable behavior remains a fixed daily template repeated across 7 days and applied to AzuraCast, but the playlist selection and combined blocks are produced by deterministic, week-seeded randomized code.

## Progress

- [x] (2026-02-26 13:11Z) Reviewed scheduler package split modules, prompts, tests, and current deterministic helpers/validation.
- [x] (2026-02-26 13:11Z) Collected `NeuralForge` playlist names to design a minimal curated compatibility list.
- [x] (2026-02-26 13:17Z) Implemented code-only generation path in `src/neuralcast/pipelines/schedule_generator/generation.py`, including week-seeded randomized scaffolding and curated NeuralForge combo presets.
- [x] (2026-02-26 13:17Z) Updated `src/neuralcast/pipelines/schedule_generator/main.py` to use the code-only generator while preserving CLI args (`--model` kept as a no-op).
- [x] (2026-02-26 13:19Z) Validated tests/CLI and completed mandatory VPS rsync redeploy for scheduler runtime changes.

## Surprises & Discoveries

- Observation: The existing scheduler already has strong deterministic scaffolding and validation (`template.py`) including multi-playlist block support, so the LLM mostly chooses playlist assignments and labels.
  Evidence: `build_deterministic_daily_template(...)` plus `validate_daily_template(...)` in `src/neuralcast/pipelines/schedule_generator/template.py`.

- Observation: With purely probabilistic combo selection, NeuralForge could legitimately produce zero combined blocks for a given seeded week.
  Evidence: Initial mock generation produced `combo_blocks=0`, then a targeted patch was added to force at least one curated combo in a suitable daytime block (>= 90 minutes).

## Decision Log

- Decision: Preserve the current CLI arguments (including `--model`) but ignore the model parameter in generation.
  Rationale: Cron compatibility is the user’s explicit requirement; keeping `--model` as a no-op avoids breaking existing invocations.
  Date/Author: 2026-02-26 / Codex

- Decision: Use a week-seeded pseudo-random generator instead of true non-deterministic randomness.
  Rationale: This keeps “randomized” schedules stable for repeated runs in the same week, which works better with plan hashing and cron idempotency.
  Date/Author: 2026-02-26 / Codex

- Decision: Guarantee at least one curated combo block for `neuralforge` when a daytime playlist block of >= 90 minutes exists.
  Rationale: The user explicitly wants the compatibility-based combined blocks to be meaningfully used, and pure probability could hide the feature on some weeks.
  Date/Author: 2026-02-26 / Codex

## Outcomes & Retrospective

The scheduler no longer depends on Gemini/LLM generation in the runtime path. CLI endpoints and arguments remain compatible for cron jobs, including `--model` (ignored). The new generator creates a valid fixed daily template using code-only logic with week-seeded randomized block durations and curated NeuralForge combinations. Validation and VPS redeploy both completed successfully.

## Context and Orientation

The schedule generator pipeline lives under `src/neuralcast/pipelines/schedule_generator/`. `main.py` is the CLI/orchestration entrypoint used by the package and repo-root shim. `generation.py` currently contains Gemini prompt assembly and response parsing. `template.py` contains the hard scheduling rules and deterministic helper functions (time parsing, open-slot windows, block partitioning, validation, hash building, and weekly expansion). `client.py` handles AzuraCast API reads/writes and conversion of the daily template into per-playlist `schedule_items`.

The safe refactor boundary is the plan-generation call in `main.run(...)`: downstream code only requires a `WeeklySchedulePlan` instance from `models.py`.

## Plan of Work

Rewrite `src/neuralcast/pipelines/schedule_generator/generation.py` so generation is code-only. The new generator will:

1. Build a randomized-but-valid daily scaffold (block durations and open/playlist placement) while preserving the fixed 22:00-06:00 open window and all ratio/duration constraints.
2. Assign playlists to daytime playlist blocks using weighted randomized selection with simple recency penalties.
3. Use a curated compatibility preset list for `neuralforge` only, allowing a small set of sensible combined blocks (for example Prog + Instrumental Prog, Power + Symphonic, Folk Rock + Folk Metal) and avoiding unwanted combinations by omission.
4. Validate the generated template with existing `validate_daily_template(...)`, expand to 7 days, compute plan hash, and return `WeeklySchedulePlan`.

Then update `src/neuralcast/pipelines/schedule_generator/main.py` to call the code generator and clean up stale Gemini-only imports/help text while keeping argument names intact.

## Concrete Steps

From repository root (`/home/ungern/Dropbox/Documents/Projects_and_Coding/Media_and_Content/NeuralCast`):

1. Edit `src/neuralcast/pipelines/schedule_generator/generation.py`.
2. Edit `src/neuralcast/pipelines/schedule_generator/main.py`.
3. Run:
   - `PYTHONPATH=src python tests/test_schedule_generator.py`
   - `PYTHONPATH=src python -m neuralcast.cli.schedule_generator --help`
   - `PYTHONPATH=src python - <<'PY' ...build_weekly_plan_with_code mock NeuralForge snippet... PY`
4. Run the mandatory redeploy script:
   - `./deployment/redeploy_host_orchestrator_rsync.sh`

## Validation and Acceptance

Acceptance criteria:

- Scheduler CLI commands still parse the same arguments (including `--model`) without errors.
- Unit tests for schedule helper behavior continue to pass.
- A generated plan is produced without requiring Gemini client packages or network LLM calls.
- VPS redeploy script completes (or fails with a clearly reported external connectivity issue).

Observed validation results:

- `PYTHONPATH=src python tests/test_schedule_generator.py` -> `Ran 10 tests ... OK`
- `PYTHONPATH=src python -m neuralcast.cli.schedule_generator --help` -> help output rendered successfully with unchanged argument names
- Mock NeuralForge generation via `build_weekly_plan_with_code(...)` -> valid plan produced with combined blocks:
  - `06:00-09:00 ['Power Metal', 'Symphonic Metal']`
  - `12:00-15:30 ['Prog Metal', 'Instrumental Prog Metal']`
- `./deployment/redeploy_host_orchestrator_rsync.sh` -> completed successfully (`[deploy] Done.`)

## Idempotence and Recovery

The code-only generator is designed to be week-seeded and deterministic for repeated runs with the same inputs, so rerunning dry-run/apply should produce the same plan hash for the same week unless code/config changes. If generation fails validation, the implementation should retry with alternate seeds and then fall back to the existing deterministic template path.

## Artifacts and Notes

Key proof snippets:

    ..........
    ----------------------------------------------------------------------
    Ran 10 tests in 0.001s
    OK

    blocks/day 10
    combo_blocks 2
    06:00 09:00 ['Power Metal', 'Symphonic Metal']
    12:00 15:30 ['Prog Metal', 'Instrumental Prog Metal']

    [verify] Key deployed entrypoints:
    ...
    /root/radio_host_orchestrator/src/neuralcast/pipelines/schedule_generator/main.py
    [deploy] Done.

## Interfaces and Dependencies

Keep the `build_weekly_plan_with_llm(...)` callable available to avoid touching CLI argument wiring beyond import cleanup; it may become a compatibility wrapper around the new code generator.

Define a code-generation function in `src/neuralcast/pipelines/schedule_generator/generation.py` with this stable output contract:

- Input: station slug/name/timezone, week dates, playlist list, open ratio bounds, block duration bounds, and a (possibly ignored) `model` string.
- Output: `neuralcast.pipelines.schedule_generator.models.WeeklySchedulePlan`

No new third-party dependencies are required.

Revision note (2026-02-26 / Codex): Created initial ExecPlan for the LLM-to-code scheduler rewrite before implementation.
Revision note (2026-02-26 / Codex): Updated with implementation progress, validation evidence, and final deployment outcome.
