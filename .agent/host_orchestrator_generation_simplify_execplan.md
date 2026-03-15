# Simplify Host Orchestrator Generation Flow

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work proceeds.

This repository includes `.agent/PLANS.md`; this document is maintained in accordance with `.agent/PLANS.md`.

## Purpose / Big Picture

After this change, `src/neuralcast/pipelines/host_orchestrator/generation.py` keeps the same generation behavior, but the main dispatcher `generate_archetype_script()` is much easier to read. A maintainer can follow plain-script, concert-check, and news generation as separate flows instead of parsing one 400-line function with repeated setup and fallback logic.

## Progress

- [x] (2026-03-15 21:16Z) Audited `generate_archetype_script()` and identified repeated per-archetype prompt setup and three distinct generation modes.
- [x] (2026-03-15 21:20Z) Extracted prompt-variant resolution and split generation into standard, concert, and news helper flows.
- [x] (2026-03-15 21:21Z) Validated with `python -m compileall src/neuralcast/pipelines/host_orchestrator/generation.py` and `python -m unittest tests.test_story_orchestrator -q`.
- [ ] Redeploy with the required host-orchestrator rsync workflow.

## Surprises & Discoveries

- Observation: most of the complexity in `generate_archetype_script()` was not deep logic but repeated orchestration scaffolding.
  Evidence: the same function handled prompt-variant selection, retry plumbing, fallback policy, and three different output contracts before the extraction.
- Observation: the existing unit suite already exercises enough of the public generation surface to safely refactor the dispatcher without adding new tests first.
  Evidence: `tests/test_story_orchestrator.py` already covers news parsing/validation, fallback-on-`NO_SCRIPT`, and forced focus prompt behavior.

## Decision Log

- Decision: keep all new helpers inside `generation.py`.
  Rationale: the goal of this pass is simplification and code reduction, not adding more files or another abstraction layer.
  Date/Author: 2026-03-15 / Codex
- Decision: extract mode-specific helpers (`standard`, `concert`, `news`) before touching larger prompt-building functions like `format_shared_input()`.
  Rationale: this reduces the highest-impact complexity first while preserving test coverage and minimizing behavioral risk.
  Date/Author: 2026-03-15 / Codex

## Outcomes & Retrospective

The dispatcher refactor is complete locally. `generate_archetype_script()` is now a coordinator over prompt-variant resolution and three mode-specific generation helpers, which removes a large amount of nesting and repeated inline setup while preserving existing behavior under the current tests. The remaining operational step is the mandatory VPS redeploy.

## Context and Orientation

`src/neuralcast/pipelines/host_orchestrator/generation.py` owns prompt assembly, Gemini calls, contract parsing, repair passes, and validation. The key entrypoint is `generate_archetype_script()`, which returns `(script, news_segment, archetype_used)`. Before this change, that function directly embedded the control flow for all supported archetypes, including the special structured-output paths for `NEWS` and `CONCERT_CHECK`.

In this file, “standard” means archetypes that return plain spoken text. “Concert-check” and “news” are special because they ask Gemini for structured output and then validate it before converting back to spoken script text.

## Plan of Work

Keep the top-level function signature unchanged. Extract the per-archetype focus and lane selection into a small helper and dataclass so the prompt inputs are assembled in one place. Then move the three execution styles into narrow helpers:

- plain spoken script generation,
- concert-check generation with repair and validation,
- news generation with topic retries and freshness validation.

Preserve all existing log messages, retry behavior, and fallback behavior. Validate with the existing story-orchestrator test suite, then redeploy because this file is in the mandatory host-orchestrator deploy surface.

## Concrete Steps

Run from `/home/nicou/Dropbox/Documents/Projects_and_Coding/Media_and_Content/NeuralCast`.

1. Edit `src/neuralcast/pipelines/host_orchestrator/generation.py` to:
   - add a prompt-variant dataclass,
   - extract prompt-variant resolution,
   - extract the standard / concert / news generation flows,
   - keep `generate_archetype_script()` as the public dispatcher.
2. Run:
      python -m compileall src/neuralcast/pipelines/host_orchestrator/generation.py
      python -m unittest tests.test_story_orchestrator -q
3. Redeploy:
      ./deployment/redeploy_host_orchestrator_rsync.sh

Observed validation transcript:

    python -m unittest tests.test_story_orchestrator -q
    [ultra_minimal] Gemini did not produce a usable script; using deterministic local fallback.
    ----------------------------------------------------------------------
    Ran 42 tests in 0.005s
    OK

## Validation and Acceptance

Acceptance means:

- `generate_archetype_script()` is materially shorter and easier to follow.
- The existing public return contract and fallback behavior remain intact.
- `python -m unittest tests.test_story_orchestrator -q` passes.
- The host-orchestrator rsync redeploy completes successfully.

## Idempotence and Recovery

This refactor is source-only and can be repeated safely. The test and compile commands are idempotent. The rsync deploy script is also safe to rerun because it synchronizes the changed source tree and removes stale deleted files while preserving generated snippet media via excludes.

## Artifacts and Notes

This pass intentionally does not simplify `format_shared_input()` yet. That function is still large, but splitting the dispatcher first gives a smaller, safer next step if further simplification is requested.

## Interfaces and Dependencies

The public generation interface must remain:

- `neuralcast.pipelines.host_orchestrator.generation.generate_archetype_script(...) -> Tuple[str, Optional[NewsSegment], Archetype]`

This change continues to rely on the same surrounding functions and modules:

- `build_prompt()` for prompt assembly
- `attempt_concert_repair()` and `attempt_news_repair()` for repair passes
- `validate_concert_segment()` and `validate_news_freshness_and_dedup()` for contract checks
- `run_with_retries()` for external call retry behavior

Change note: created on 2026-03-15 after implementing the dispatcher simplification so the rationale, validation, and deployment requirement are recorded with the code change.
