# Simplify Host Orchestrator Runtime Flow

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work proceeds.

This repository includes `.agent/PLANS.md`; this document is maintained in accordance with `.agent/PLANS.md`.

## Purpose / Big Picture

After this change, the host orchestrator runtime entrypoint will keep the same CLI behavior and the same injection workflow, but the main cycle code will be easier to follow and cheaper to maintain. A reader should be able to open `src/neuralcast/pipelines/host_orchestrator/main.py`, see a short top-level orchestration flow, and find the gating, schedule-resolution, generation, and publish phases in small helpers instead of one long transaction.

## Progress

- [x] (2026-03-15 20:58Z) Audited `.agent/PLANS.md`, the code-simplifier skill, `src/neuralcast/pipelines/host_orchestrator/main.py`, and current tests.
- [x] (2026-03-15 21:06Z) Extracted runtime helper dataclasses and phase helpers in `src/neuralcast/pipelines/host_orchestrator/main.py`, reducing `run()` to a coordinator over station bootstrap, playback gating, queue/schedule resolution, archetype selection, generation context assembly, and publish/finalize.
- [x] (2026-03-15 21:08Z) Added focused tests for the extracted archetype-selection helper in `tests/test_story_orchestrator.py`.
- [x] (2026-03-15 21:10Z) Ran targeted validation with `python -m unittest tests.test_story_orchestrator -q` and `python -m compileall src/neuralcast/pipelines/host_orchestrator/main.py tests/test_story_orchestrator.py`.
- [x] (2026-03-15 21:12Z) Redeployed with `./deployment/redeploy_host_orchestrator_rsync.sh` and verified the updated `src/neuralcast/pipelines/host_orchestrator/main.py` on the VPS.

## Surprises & Discoveries

- Observation: `tests/test_story_orchestrator.py` currently covers `validate_runtime_args()` and lower-level schedule/generation helpers, but not the runtime cycle in `run()`.
  Evidence: static review of `tests/test_story_orchestrator.py` imports and test names on 2026-03-15.
- Observation: `src/neuralcast/pipelines/host_orchestrator/main.py` still carries dead imports from older integration work.
  Evidence: static AST check flagged unused imports such as `build_system_prompt`, `parse_news_output`, `validate_news_freshness_and_dedup`, `OrchestratorState`, `ScheduleContext`, `build_news_dedup_key`, `default_state`, and `migrate_state`.
- Observation: The cleanest simplification seam was not “move code to a new module” but “make `run()` read like a script of existing phases.”
  Evidence: after extraction, the runtime logic stayed in one file while the repeated inline gate/publish logic became testable helpers and the unit suite still passed unchanged plus two new tests.

## Decision Log

- Decision: Keep this refactor inside `src/neuralcast/pipelines/host_orchestrator/main.py` instead of introducing another module.
  Rationale: the user asked for simplification and code reduction, so the first pass should reduce local complexity without widening the file graph.
  Date/Author: 2026-03-15 / Codex
- Decision: Preserve the public interface of `validate_runtime_args()`, `run()`, and `build_arg_parser()` exactly.
  Rationale: these functions are already imported by tests and used by CLI wrappers and deployment automation.
  Date/Author: 2026-03-15 / Codex
- Decision: Add helper dataclasses (`StationRuntime`, `PlaybackContext`, `QueueContext`, `GenerationContext`) in `main.py`.
  Rationale: a small amount of structure reduced tuple/parameter ambiguity and let the extracted helpers stay simpler without spreading this refactor across more files.
  Date/Author: 2026-03-15 / Codex
- Decision: Add tests for `_select_archetype()` rather than trying to mock the full `run()` cycle.
  Rationale: that helper captures the most important branch behavior introduced by the refactor, while full-cycle tests would require much heavier mocking and add more noise than signal for this simplification pass.
  Date/Author: 2026-03-15 / Codex

## Outcomes & Retrospective

The simplification pass achieved the intended result. `run()` is now a coordinator instead of a single long transaction, but the runtime still lives in the same file and keeps the same public API, which kept the blast radius small. The new helper dataclasses made the control flow easier to read without adding another layer of modules.

The local validation passed and the mandatory VPS redeploy also passed. A future pass could push this farther by simplifying the generation and schedule modules, but this change already removes a large amount of mental overhead from the runtime entrypoint without changing the externally visible behavior.

## Context and Orientation

The runtime entrypoint for host injection lives in `src/neuralcast/pipelines/host_orchestrator/main.py`. That file is executed indirectly by `src/neuralcast/cli/host_orchestrator.py` and the root shim `inject_host_segment.py`. The `run(args)` function is the live cycle: it loads AzuraCast credentials, acquires the station lock, reads now-playing and queue data, decides whether to speak, generates the host segment, synthesizes audio, uploads the file, injects it into AzuraCast, updates local state, and then releases the lock.

The helper modules already exist. `src/neuralcast/pipelines/host_orchestrator/schedule.py` resolves block context, `generation.py` handles prompt/generation logic, `transport.py` handles AzuraCast parsing and API helpers, `state.py` manages cadence and lock state, and `assets.py` builds local text/audio assets. The current problem is not missing modularity across the package; it is that `main.py` still stitches everything together in a single oversized function with interleaved gates and side effects.

In this plan, “gate” means a runtime condition that intentionally skips the cycle early, for example “not enough listeners” or “current track too close to ending.” “Publish” means the side-effecting portion after generation: upload to AzuraCast, queue through telnet, update state, and clean old assets.

## Plan of Work

First, simplify `src/neuralcast/pipelines/host_orchestrator/main.py` by extracting phase helpers that describe the flow in plain language. The helper boundaries will be chosen from existing behavior seams: initialization, station/bootstrap loading, now-playing and queue context resolution, archetype selection, generation input assembly, and publish/finalization. These helpers will return simple values or small dataclasses rather than carrying implicit state through long local variable chains.

Second, remove dead imports and repeated local code while preserving log messages and short-circuit behavior. The goal is to make `run()` read as a thin coordinator. The refactor must not change CLI flags, lock behavior, file log configuration, the timing of `save_state_atomic()`, or the current dry-run behavior.

Third, add focused tests in `tests/test_story_orchestrator.py` for any newly exposed helper logic that is important to preserve and that was previously buried in `run()`. These tests should validate branch behavior that is now easier to express than before, especially around forced archetype handling and fallback gating.

Finally, run the targeted unit suite and redeploy with `./deployment/redeploy_host_orchestrator_rsync.sh`, because host-orchestrator runtime code is part of the mandatory redeploy surface in `AGENTS.md`.

## Concrete Steps

Run from `/home/nicou/Dropbox/Documents/Projects_and_Coding/Media_and_Content/NeuralCast`.

1. Edit `src/neuralcast/pipelines/host_orchestrator/main.py` to:
   - remove dead imports,
   - introduce small runtime helper dataclasses if they reduce parameter sprawl,
   - extract phase helpers from `run()`,
   - reduce repeated `now_ts()` / gate boilerplate where safe,
   - keep the public functions and CLI unchanged.
2. Edit `tests/test_story_orchestrator.py` to add focused tests for the new helper boundaries or the public runtime behavior they preserve.
3. Run:
      python -m unittest tests.test_story_orchestrator -q
4. Redeploy:
      ./deployment/redeploy_host_orchestrator_rsync.sh

Expected validation transcript shape:

    python -m unittest tests.test_story_orchestrator -q
    ----------------------------------------------------------------------
    Ran ... tests in ...s
    OK

Observed during implementation:

    python -m unittest tests.test_story_orchestrator -q
    [ultra_minimal] Gemini did not produce a usable script; using deterministic local fallback.
    ----------------------------------------------------------------------
    Ran 42 tests in 0.005s
    OK

    ./deployment/redeploy_host_orchestrator_rsync.sh
    [deploy] Target: neuralvps:/root/radio_host_orchestrator
    ...
    [verify] Key deployed entrypoints:
    -rw-r--r-- 1 1000 1000 29538 Mar 15 22:02 /root/radio_host_orchestrator/src/neuralcast/pipelines/host_orchestrator/main.py
    [deploy] Done.

## Validation and Acceptance

Acceptance means:

- `src/neuralcast/pipelines/host_orchestrator/main.py` is materially simpler to read, with `run()` acting as orchestration instead of holding every decision inline.
- `validate_runtime_args()`, `run()`, and `build_arg_parser()` keep the same signatures and CLI behavior.
- `python -m unittest tests.test_story_orchestrator -q` passes after the refactor.
- The required VPS redeploy completes successfully after the runtime edit.

## Idempotence and Recovery

This refactor is source-only and can be rerun safely. The unit test command is idempotent. The rsync deploy script is also idempotent because it overwrites changed source files and removes stale deleted files while preserving generated snippet media through its exclude rules.

If the refactor introduces a behavior regression, recovery is straightforward: revert only `src/neuralcast/pipelines/host_orchestrator/main.py` and any new tests, then rerun the unit test command and redeploy script.

## Artifacts and Notes

The first-pass target is simplification, not deeper architecture work. The package already has separate modules for generation, transport, schedule, state, and assets; this plan focuses on making the runtime coordinator actually take advantage of that modularity instead of layering another abstraction system on top.

## Interfaces and Dependencies

The public runtime surface must remain:

- `neuralcast.pipelines.host_orchestrator.main.validate_runtime_args(args: argparse.Namespace) -> TrackFocus | None`
- `neuralcast.pipelines.host_orchestrator.main.run(args: argparse.Namespace) -> None`
- `neuralcast.pipelines.host_orchestrator.main.build_arg_parser() -> argparse.ArgumentParser`

This change should continue using the existing module dependencies:

- `assets.py` for local story asset generation and cleanup
- `generation.py` for script generation
- `schedule.py` for block-context resolution
- `state.py` for orchestration state and lock management
- `transport.py` for AzuraCast payload parsing and queue/upload helpers

Change note: created on 2026-03-15 to drive a simplification-first refactor of the host orchestrator runtime entrypoint.
