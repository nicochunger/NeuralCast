# Refactor Story Injector Into Modular Host Orchestrator

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work proceeds.

This repository includes `.agent/PLANS.md`; this document is maintained in accordance with `.agent/PLANS.md`.

## Purpose / Big Picture

After this change, the oversized `src/neuralcast/pipelines/story_injector.py` runtime will be replaced by a modular host orchestration pipeline under a broader name that reflects current behavior (not just story snippets). Users and operators will be able to run the same injection workflow through a new filename, while docs and cron examples point at the new path and legacy imports/entrypoints remain compatible.

## Progress

- [x] (2026-02-20 00:00Z) Audited current runtime structure, external references, tests, CLI entrypoints, and deployment instructions.
- [x] (2026-02-20 00:00Z) Chose new runtime name: `host_orchestrator`.
- [x] (2026-02-20 15:45Z) Implemented modular split into `host_orchestrator_models/config/utils/schedule/state/transport/generation/assets.py`.
- [x] (2026-02-20 15:45Z) Added `src/neuralcast/pipelines/host_orchestrator.py` runtime entrypoint and preserved backwards compatibility via `src/neuralcast/pipelines/story_injector.py`.
- [x] (2026-02-20 15:45Z) Updated CLI/root shims and docs/deployment references to the new runtime filename.
- [x] (2026-02-20 15:45Z) Validated with `compileall`, unit tests, and `--help` checks for old/new entrypoints.
- [x] (2026-02-20 15:45Z) Redeployed bundle to VPS and updated live cron entries from `story_injector.py` to `host_orchestrator.py`.
- [x] (2026-02-20 15:54Z) Renamed deployment artifact to `deployment/deploy_host_orchestrator.zip`, updated deployment docs/commands, and verified VPS extraction with the new archive name.
- [x] (2026-02-20 15:59Z) Renamed VPS working directory to `/root/radio_host_orchestrator`, switched cron output to `/root/radio_host_orchestrator/host_orchestrator.log`, and added per-run separator logging.

## Surprises & Discoveries

- Observation: The pipeline already behaves as a host orchestrator (news, concert checks, block intros), not a story-only injector.
  Evidence: `Archetype` enum and generation branches in `src/neuralcast/pipelines/story_injector.py` include `NEWS`, `CONCERT_CHECK`, and `BLOCK_INTRO`.
- Observation: Current tests only import a limited helper surface, which makes compatibility shimming feasible.
  Evidence: `tests/test_story_orchestrator.py` imports selected helpers and dataclasses rather than private internals.
- Observation: Direct file execution of `src/neuralcast/pipelines/host_orchestrator.py` still needs a `src/` bootstrap in `sys.path` to match old operator usage patterns.
  Evidence: Initial `python src/neuralcast/pipelines/host_orchestrator.py --help` failed with `ModuleNotFoundError: No module named 'neuralcast'` until bootstrapping was added.
- Observation: VPS crontab had one active and one commented `story_injector.py` entry; both needed migration for consistency.
  Evidence: `ssh neuralvps 'crontab -l'` showed two lines with `/root/radio_host_orchestrator/src/neuralcast/pipelines/story_injector.py`.

## Decision Log

- Decision: Rename runtime module to `host_orchestrator`.
  Rationale: It is the narrowest accurate name that captures current general behavior while remaining clear for cron/deployment operations.
  Date/Author: 2026-02-20 / Codex
- Decision: Keep a compatibility shim at `src/neuralcast/pipelines/story_injector.py`.
  Rationale: Existing scripts/tests/imports can continue functioning while cron/docs transition to the new path.
  Date/Author: 2026-02-20 / Codex
- Decision: Introduce `inject_host_segment.py` as a new root-level primary command and keep `inject_story_snippet.py` as alias.
  Rationale: This makes the user-facing command name match the broader orchestrator behavior while preserving compatibility.
  Date/Author: 2026-02-20 / Codex
- Decision: Rename deployment artifact from `deploy_story_injector.zip` to `deploy_host_orchestrator.zip`.
  Rationale: Deployment naming should match the current runtime purpose and reduce operational ambiguity.
  Date/Author: 2026-02-20 / Codex
- Decision: Rename VPS runtime directory/log from `radio_stories` + `story.log` to `radio_host_orchestrator` + `host_orchestrator.log`.
  Rationale: Operational paths should match current runtime naming and reduce ambiguity in server maintenance.
  Date/Author: 2026-02-20 / Codex

## Outcomes & Retrospective

The refactor is complete and operational. The original 3k-line pipeline has been decomposed into focused modules, with `src/neuralcast/pipelines/host_orchestrator.py` now acting as the runtime coordinator. Legacy import and command surfaces still work via compatibility shims, while documentation and cron/deploy references now point at the new filename.

Local validation passed (`compileall`, `tests.test_story_orchestrator`, and entrypoint `--help` checks). VPS deployment was executed using the required 3-step procedure, checksum of deployed `host_orchestrator.py` matched local, live crontab entries were migrated to `host_orchestrator.py`, and the VPS runtime directory/log naming now reflects host-orchestrator terminology.

## Context and Orientation

The existing entrypoint chain is:

- `inject_story_snippet.py` (repo-root shim)
- `src/neuralcast/cli/story_injector.py` (CLI shim)
- `src/neuralcast/pipelines/story_injector.py` (3k-line runtime)

The runtime currently mixes many concerns in one file: settings/constants, prompt/template loading, dataclasses, state/lock management, schedule interpretation, generation/parsing/validation, AzuraCast transport, audio post-processing, cleanup, and CLI runtime orchestration.

This refactor will introduce focused modules in `src/neuralcast/pipelines/` prefixed with `host_orchestrator_...` plus a small `host_orchestrator.py` runtime entrypoint. The old `story_injector.py` path will remain as a compatibility import layer.

## Plan of Work

First, extract types and constants into dedicated modules so remaining logic can import stable primitives. Next, split state/schedule/generation/transport/assets responsibilities into separate files with minimal logic changes. Then create the new main runtime file (`host_orchestrator.py`) that orchestrates those modules and exposes the same CLI API (`build_arg_parser`, `run`).

After code split, rewire CLI and root shims so new primary code path uses `host_orchestrator`, while retaining legacy wrappers. Then update documentation and deployment instructions with the new filename and cron command examples. Finally run local validation, redeploy to VPS, and patch VPS crontab entries referencing the old pipeline filename.

## Concrete Steps

Run from `/home/nicou/Documents/NeuralCast`:

1. Create modular pipeline files under `src/neuralcast/pipelines/host_orchestrator_*.py` and create `src/neuralcast/pipelines/host_orchestrator.py`.
2. Replace `src/neuralcast/pipelines/story_injector.py` with a compatibility shim.
3. Update CLI wrappers and docs referencing old runtime path.
4. Validate with:
   python -m compileall src/neuralcast/pipelines src/neuralcast/cli tests
   python -m unittest tests.test_story_orchestrator -v
5. Redeploy to VPS:
   zip -r deployment/deploy_host_orchestrator.zip src vps_requirements.txt -x "*/__pycache__/*" "*.pyc" "src/neuralcast/assets/stories/snippets/*"
   scp deployment/deploy_host_orchestrator.zip neuralvps:~/deploy_host_orchestrator.zip
   ssh neuralvps 'cd /root && unzip -o deploy_host_orchestrator.zip -d radio_host_orchestrator'
6. Update crontab on VPS if it contains `story_injector.py` references.

## Validation and Acceptance

Acceptance criteria:

- Running `python inject_story_snippet.py --help` still works.
- Running `python src/neuralcast/pipelines/host_orchestrator.py --help` works and shows the orchestrator CLI options.
- Existing unit tests in `tests/test_story_orchestrator.py` pass.
- Repository docs and deployment instructions reference `host_orchestrator.py` for direct pipeline execution and cron examples.
- VPS deployment bundle is rebuilt, copied, and extracted.
- VPS cron entries previously referencing `story_injector.py` are updated to `host_orchestrator.py`.

## Idempotence and Recovery

The refactor is additive and reversible because legacy wrappers remain. If a new module import fails, the shim strategy allows restoring behavior by re-pointing wrappers back to the old implementation. VPS redeploy uses overwrite extraction (`unzip -o`), so rerunning deploy is safe.

## Artifacts and Notes

Key verification snippets:

    python -m compileall src/neuralcast/pipelines src/neuralcast/cli tests
    # Compiling ... host_orchestrator*.py ... OK

    python -m unittest tests.test_story_orchestrator -v
    # Ran 13 tests ... OK

    python inject_host_segment.py --help
    # usage: inject_host_segment.py ...

    ssh neuralvps 'sha256sum /root/radio_host_orchestrator/src/neuralcast/pipelines/host_orchestrator.py'
    # 35396fc1b8858dcd4a1578dcac90d1d5ad1ea9946a305a4d7c23f65ca792811f

    sha256sum src/neuralcast/pipelines/host_orchestrator.py
    # 35396fc1b8858dcd4a1578dcac90d1d5ad1ea9946a305a4d7c23f65ca792811f

    ssh neuralvps 'crontab -l'
    # Active neuralforge job now points to .../src/neuralcast/pipelines/host_orchestrator.py

    # host_orchestrator.log now includes per-run separator lines:
    # 2026-02-20 ... | host_orchestrator | INFO | ====================================================================================

## Interfaces and Dependencies

Public runtime interface that must remain available:

- `neuralcast.pipelines.host_orchestrator.build_arg_parser() -> argparse.ArgumentParser`
- `neuralcast.pipelines.host_orchestrator.run(args: argparse.Namespace) -> None`

Backward-compatible legacy interface retained:

- `neuralcast.pipelines.story_injector.build_arg_parser() -> argparse.ArgumentParser`
- `neuralcast.pipelines.story_injector.run(args: argparse.Namespace) -> None`

Dependencies remain unchanged: `requests`, `python-dotenv`, `google-genai`, `ffmpeg`, `mp3gain`, plus existing project service modules.

Change note: Updated after implementation to reflect completed modular refactor, compatibility strategy, documentation updates, VPS redeploy, and cron migration.
