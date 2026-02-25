```md
# ExecPlan: Pipelines Package Reorganization

## Purpose / Big Picture

Reorganize `src/neuralcast/pipelines/` so the main pipeline families are grouped into their own folders. After this change, the host orchestrator modules live under `src/neuralcast/pipelines/host_orchestrator/` with shorter filenames (`config.py`, `state.py`, etc.), and `schedule_generator` / `new_releases` become package folders with `main.py` entry modules. The observable result is cleaner module layout while preserving existing import paths such as `neuralcast.pipelines.host_orchestrator` and CLI commands.

## Progress

- [x] (2026-02-25 11:45Z) Created subpackages and moved/renamed pipeline modules for `host_orchestrator`, `schedule_generator`, and `new_releases`.
- [x] (2026-02-25 11:45Z) Rewrote host orchestrator internal imports to package-relative imports and added package `__init__.py` / `__main__.py` wrappers.
- [x] (2026-02-25 11:45Z) Validated imports via `compileall` and `--help` checks for both CLI and `python -m neuralcast.pipelines.<name>` entrypoints.
- [x] (2026-02-25 11:45Z) Ran required host-orchestrator VPS redeploy (zip, `scp`, remote `unzip -o`) and verified remote `host_orchestrator/main.py` path exists.

## Surprises & Discoveries

- Observation: `schedule_generator.py` builds its logger name from `Path(__file__).stem`, so moving to `main.py` would rename the logger to `main` unless fixed.
  Evidence: `src/neuralcast/pipelines/schedule_generator.py:51`.
- Observation: Converting module files to packages breaks `python -m neuralcast.pipelines.<name>` unless a `__main__.py` shim is added.
  Evidence: Python package execution resolves `__main__.py`, not `__init__.py`.
- Observation: `unzip -o` on the VPS does not remove deleted files, so old top-level pipeline modules remain as stale files after deployment.
  Evidence: Deployment command is overwrite-only; remote cleanup was not part of the required 3-step procedure.

## Decision Log

- Decision: Preserve top-level import paths (`neuralcast.pipelines.host_orchestrator`, `...schedule_generator`, `...new_releases`) by turning each into a package with `__init__.py` re-exporting from `.main`.
  Rationale: Keeps CLI imports stable and minimizes downstream breakage while cleaning file layout.
  Date/Author: 2026-02-25 / Codex
- Decision: Add package `__main__.py` shims for all three moved pipelines.
  Rationale: Preserves `python -m neuralcast.pipelines.<pipeline>` behavior that existed when these were plain modules.
  Date/Author: 2026-02-25 / Codex
- Decision: Keep `story_injector.py` and `playlist_sync.py` top-level for now.
  Rationale: User explicitly indicated more pipeline restructuring will happen later; this change focuses on the highest-value grouping first.
  Date/Author: 2026-02-25 / Codex

## Outcomes & Retrospective

The `pipelines` directory is now cleaner and grouped by pipeline family without changing the main public import paths used by CLI modules. Host orchestrator internals now live under `src/neuralcast/pipelines/host_orchestrator/` with shorter module names. `schedule_generator` and `new_releases` are package folders with `main.py` entry modules and `__main__.py` wrappers.

Remaining caveat: the VPS deploy procedure (`unzip -o`) leaves removed files in place, so stale top-level modules still exist remotely until manually cleaned.

## Context and Orientation

Current layout mixes multiple pipeline domains in one directory (`src/neuralcast/pipelines/`), including a multi-file host orchestrator (`host_orchestrator.py` plus `host_orchestrator_*.py`) and single-file pipelines (`schedule_generator.py`, `new_releases.py`). Internal imports in host orchestrator files currently use absolute paths like `neuralcast.pipelines.host_orchestrator_config`. The CLIs import these modules via:

- `src/neuralcast/cli/host_orchestrator.py`
- `src/neuralcast/cli/schedule_generator.py`
- `src/neuralcast/cli/update_new_releases.py`

`story_injector.py` is a legacy compatibility shim that imports from `neuralcast.pipelines.host_orchestrator`.

## Plan of Work

1. Create package directories:
   - `src/neuralcast/pipelines/host_orchestrator/`
   - `src/neuralcast/pipelines/schedule_generator/`
   - `src/neuralcast/pipelines/new_releases/`
2. Move files:
   - `host_orchestrator.py` -> `host_orchestrator/main.py`
   - `host_orchestrator_assets.py` -> `host_orchestrator/assets.py`
   - `host_orchestrator_config.py` -> `host_orchestrator/config.py`
   - `host_orchestrator_generation.py` -> `host_orchestrator/generation.py`
   - `host_orchestrator_models.py` -> `host_orchestrator/models.py`
   - `host_orchestrator_schedule.py` -> `host_orchestrator/schedule.py`
   - `host_orchestrator_state.py` -> `host_orchestrator/state.py`
   - `host_orchestrator_transport.py` -> `host_orchestrator/transport.py`
   - `host_orchestrator_utils.py` -> `host_orchestrator/utils.py`
   - `schedule_generator.py` -> `schedule_generator/main.py`
   - `new_releases.py` -> `new_releases/main.py`
3. Add `__init__.py` files that re-export from `.main` (host package should preserve broad symbol export via `from .main import *`).
4. Rewrite host internal imports to package-relative imports.
5. Fix any move-related regressions (host direct-exec `parents[]` path, schedule logger naming).
6. Validate with import/CLI checks and redeploy the host orchestrator bundle to VPS (required by repo rules).

## Concrete Steps

Run from repo root:

    git status --short
    python -m compileall src/neuralcast/pipelines
    python -m neuralcast.cli.host_orchestrator --help
    python -m neuralcast.cli.schedule_generator --help
    python -m neuralcast.cli.update_new_releases --help

Redeploy steps (required after host orchestrator runtime changes):

    zip -r deployment/deploy_host_orchestrator.zip src vps_requirements.txt -x "*/__pycache__/*" "*.pyc" "src/neuralcast/assets/stories/snippets/*"
    scp deployment/deploy_host_orchestrator.zip neuralvps:~/deploy_host_orchestrator.zip
    ssh neuralvps 'cd /root && unzip -o deploy_host_orchestrator.zip -d radio_host_orchestrator'

## Validation and Acceptance

Acceptance criteria:

- Imports remain stable:
  - `from neuralcast.pipelines.host_orchestrator import run`
  - `from neuralcast.pipelines.schedule_generator import build_arg_parser`
  - `from neuralcast.pipelines.new_releases import main`
- `python -m compileall src/neuralcast/pipelines` succeeds.
- CLI `--help` commands import and print usage without `ModuleNotFoundError`.
- VPS redeploy completes successfully after packaging changes.

## Idempotence and Recovery

The move/import rewrite is repeatable as long as paths are adjusted only once. If validation fails, fix imports and rerun compile/CLI checks. Do not delete station metadata/state files; this refactor only targets Python package layout.

## Artifacts and Notes

Validation run summaries:

- `python -m compileall src/neuralcast/pipelines` succeeded.
- `PYTHONPATH=src python -m neuralcast.cli.host_orchestrator --help` succeeded.
- `PYTHONPATH=src python -m neuralcast.cli.schedule_generator --help` succeeded.
- `PYTHONPATH=src python -m neuralcast.cli.update_new_releases --help` succeeded.
- `PYTHONPATH=src python -m neuralcast.pipelines.host_orchestrator --help` succeeded.
- `PYTHONPATH=src python -m neuralcast.pipelines.schedule_generator --help` succeeded.
- `PYTHONPATH=src python -m neuralcast.pipelines.new_releases --help` succeeded.
- VPS redeploy commands (zip + `scp` + remote `unzip -o`) succeeded; remote verification showed `/root/radio_host_orchestrator/src/neuralcast/pipelines/host_orchestrator/main.py`.

## Interfaces and Dependencies

Preserved public import paths:

- `neuralcast.pipelines.host_orchestrator` (package; exports host orchestrator entrypoints and symbols from `.main`)
- `neuralcast.pipelines.schedule_generator` (package; exports `.main`)
- `neuralcast.pipelines.new_releases` (package; exports `.main`)

Internal host modules should import each other via relative imports (`from .config import ...`, `from .models import ...`, etc.) after the move.

Revision note (2026-02-25 / Codex): Created initial plan for package reorganization before implementation.
Revision note (2026-02-25 / Codex): Updated plan after implementation with completed progress, validation evidence, deployment results, and the stale-file caveat from `unzip -o`.
```
