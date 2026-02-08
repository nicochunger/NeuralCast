# Implement AI Host Orchestrator Runtime

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work proceeds.

This repository includes `.agent/PLANS.md`; this document is maintained in accordance with `.agent/PLANS.md`.

## Purpose / Big Picture

After this change, `inject_story_snippet.py` will no longer generate one generic story style. It will run a persistent AI host orchestrator that decides when to speak, what segment type to generate, and how to avoid repetition across runs. A user can verify this by running a dry run repeatedly and observing: stateful cadence control, archetype/angle selection, strict no-fabrication prompt framing, and deterministic station-scoped state persisted under `<station>/metadata/ai_host_orchestrator_state.json`.

## Progress

- [x] (2026-02-05 21:29Z) Read `ai_host_orchestrator.md`, current injector pipeline, and integration constraints.
- [x] (2026-02-05 21:36Z) Replaced `src/neuralcast/pipelines/story_injector.py` with the new orchestrator runtime (state machine, gates, archetypes, generation/TTS/upload flow, lock + atomic state persistence).
- [x] (2026-02-05 21:37Z) Added `tests/test_story_orchestrator.py` covering state migration, wait-gate behavior, archetype anti-repeat selection, and News parsing/validation helpers.
- [x] (2026-02-05 21:39Z) Ran local validation commands (`compileall`, unit tests, CLI help, and a real dry-run call).
- [x] (2026-02-05 21:39Z) Updated this plan with outcomes and implementation notes.

## Surprises & Discoveries

- Observation: The current injector queues stories with `requests.push` while repository guidance text mentions `interrupting_requests.push`.
  Evidence: `src/neuralcast/pipelines/story_injector.py` currently builds `requests.push annotate:...` commands.
- Observation: A live dry-run cycle can legitimately skip generation even with `--force-archetype` when the lead-time gate (`remaining < 90s`) fails.
  Evidence: Runtime output showed `Current track has only 36s remaining (< 90s); skipping cycle.` while still persisting state.

## Decision Log

- Decision: Keep the runtime replacement in `src/neuralcast/pipelines/story_injector.py` instead of introducing a parallel pipeline module.
  Rationale: The spec explicitly calls for replacing that runtime path, and keeping entrypoints stable avoids deployment changes.
  Date/Author: 2026-02-05 / Codex
- Decision: Keep queue insertion via `requests.push` (normal play-next queue behavior) rather than switching to `interrupting_requests.push`.
  Rationale: The orchestrator spec explicitly requests normal play-next injection semantics.
  Date/Author: 2026-02-05 / Codex
- Decision: Enforce News freshness using a required `published_at` field in META (validated against 72h max age) in addition to required topic/headline/source URL.
  Rationale: The operational spec includes a hard freshness limit; timestamp validation is required to enforce it.
  Date/Author: 2026-02-05 / Codex
- Decision: Add archetype anti-repeat filtering (N=1) before weighted sampling whenever multiple legal archetypes exist.
  Rationale: The spec’s anti-loop rules require immediate-repeat avoidance unless no alternatives are legal.
  Date/Author: 2026-02-05 / Codex

## Outcomes & Retrospective

The injector runtime now behaves as a stateful AI host orchestrator rather than a one-shot story generator. It persists per-station state under `<station>/metadata/ai_host_orchestrator_state.json`, uses a station lockfile, applies listener/lead-time/wait/content gates, supports weighted archetype selection with anti-repeat rules, and enforces News parsing + freshness/dedup validation before allowing News segments to air.

Validation confirmed:
- Syntax compiles for the new pipeline and tests.
- Unit tests pass for core pure orchestration helpers.
- CLI exposes the required contract including `--force-archetype`.
- A real dry-run invocation reached the orchestrator runtime, loaded/saved state, and correctly skipped due lead-time gate.

Remaining gap: this session did not include a full live generation+upload+queue success path because the sampled now-playing track had insufficient remaining time for lead-time eligibility.

## Context and Orientation

The runtime command `python inject_story_snippet.py ...` is a shim into `src/neuralcast/cli/story_injector.py`, which imports `build_arg_parser` and `run` from `src/neuralcast/pipelines/story_injector.py`. That pipeline currently performs one-shot story generation plus TTS and queue injection.

The new orchestrator must introduce a persistent state machine and station lock semantics. State must live under station data directories (`<station>/metadata/`) and not under package assets. Prompt assets still live under `src/neuralcast/assets/stories/`; generated artifacts continue under `src/neuralcast/assets/stories/snippets/<station>/<YYYY-MM-DD>/`.

The Gemini client utilities already exist in `src/neuralcast/services/openai_client.py`, including text generation and TTS helpers. The new runtime needs richer generation controls (temperature/top-p/system instruction/search tool usage), plus strict news-output parsing and validation logic.

## Plan of Work

The implementation will be done in three concrete passes. First, replace the core runtime with a new orchestrator architecture in `src/neuralcast/pipelines/story_injector.py`: station lock management, per-station state load/migrate/save (atomic writes), eligibility gates, archetype legality/cooldowns, weighted selection, angle/hook anti-repeat logic, prompt assembly from constitution + wrappers, Gemini generation with randomized settings, TTS, upload, queue push, and post-air state mutation.

Second, wire in operational constraints from the spec: persist state on every run path (including skips/failures), retry transient external calls with 2s/5s backoff, news contract validation with one repair attempt, topic retry behavior for stale/duplicate headlines, and `--force-archetype` semantics.

Third, add tests in `tests/` for pure logic that does not require network access, then run compile/test commands to validate syntax and expected behavior.

## Concrete Steps

Run from repository root (`/home/ungern/Personal_Projects/Media_and_Content/NeuralCast`):

1. Rewrite `src/neuralcast/pipelines/story_injector.py` with the orchestrator runtime.
2. Add `tests/test_story_orchestrator.py` for orchestration utility tests.
3. Validate syntax:
   python -m compileall src/neuralcast/pipelines/story_injector.py tests/test_story_orchestrator.py
4. Run tests:
   python -m unittest tests.test_story_orchestrator -v
5. Optionally run a local dry run (requires credentials and API reachability):
   python inject_story_snippet.py --station neuralcast --base-url <url> --dry-run --min-listeners 0

Expected indicators include successful state-file creation under station metadata, no exceptions in unit tests, and dry-run artifact emission under `src/neuralcast/assets/stories/snippets/`.

## Validation and Acceptance

Acceptance is behavioral:

- Running dry-run repeatedly should not speak every cycle; cadence waits for song changes and deadline logic.
- For a forced archetype command such as `--force-archetype back_sell`, the selected archetype bypasses wait/cooldown checks.
- State file exists at `<station>/metadata/ai_host_orchestrator_state.json` and updates on skipped and successful cycles.
- News responses must either parse as `SCRIPT` + `META JSON` with required keys or become `NO_SCRIPT` fallback (except forced-news mode, which fails clearly).

## Idempotence and Recovery

State saves use atomic temp-file replacement so repeated runs are safe and partial writes are avoided. If the state JSON is corrupt, the runtime moves it to `ai_host_orchestrator_state.corrupt.<timestamp>.json` and reinitializes defaults. Lock files older than 10 minutes are considered stale and replaced.

## Artifacts and Notes

Key validation transcripts:

    python -m compileall src/neuralcast/pipelines/story_injector.py tests/test_story_orchestrator.py
    # Compiling ... OK

    python -m unittest discover -s tests -p 'test_story_orchestrator.py' -v
    # Ran 6 tests ... OK

    python inject_story_snippet.py --help
    # Shows required flags including --station, --base-url, --dry-run, --min-listeners, --force-archetype

    python inject_story_snippet.py --station neuralcast --dry-run --min-listeners 0 --force-archetype ultra_minimal
    # Loaded orchestrator state ...
    # Now playing ...
    # Current track has only 36s remaining (< 90s); skipping cycle.

Created artifact during validation:
- `NeuralCast/metadata/ai_host_orchestrator_state.json` was created and persisted by the dry run, then removed to keep the working tree free of generated station state.

## Interfaces and Dependencies

The runtime will keep this public interface:

- `neuralcast.pipelines.story_injector.build_arg_parser() -> argparse.ArgumentParser`
- `neuralcast.pipelines.story_injector.run(args: argparse.Namespace) -> None`

New internal orchestrator interfaces in `src/neuralcast/pipelines/story_injector.py` will include dataclasses and helpers for:

- Parsed now-playing/queue track payloads.
- Persistent state model migration and atomic persistence.
- Archetype/angle/hook selection logic.
- News response parsing and validation.

Dependencies remain within existing project stack: `requests`, `python-dotenv`, `google-genai` (via existing client helper), and system `ffmpeg` for optional audio speed jitter.

Change note: Updated after implementation and validation to reflect completed progress, recorded decisions, and concrete outcomes.
