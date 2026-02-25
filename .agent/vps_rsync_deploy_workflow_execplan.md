```md
# ExecPlan: Migrate VPS deploy workflow from zip/unzip to rsync

## Purpose / Big Picture

The current VPS deploy flow (`zip` -> `scp` -> `unzip -o`) leaves deleted files behind on the server, which makes old modules continue to exist after refactors. This plan introduces an `rsync --delete` deployment script that keeps the VPS code tree in sync with the repo, preserves generated media (including story snippet MP3s), and updates documentation to use stable CLI module entrypoints in cron.

## Progress

- [x] (2026-02-25 15:40Z) Added `deployment/redeploy_host_orchestrator_rsync.sh` with `rsync --delete`, safe excludes, and verification output.
- [x] (2026-02-25 15:40Z) Ran the rsync deploy against the VPS and confirmed stale top-level pipeline files were removed from the synced `src/` tree.
- [x] (2026-02-25 15:40Z) Verified deployed CLI entrypoints on VPS, confirmed cron uses CLI module endpoints, and checked host/scheduler logs.
- [x] (2026-02-25 15:40Z) Updated `AGENTS.md` redeploy procedure and cron guidance to the new rsync workflow.

## Surprises & Discoveries

- Observation: VPS cron jobs were still calling old internal pipeline file paths under `src/neuralcast/pipelines/*.py`.
  Evidence: `crontab -l` on VPS showed calls to removed paths like `.../pipelines/host_orchestrator.py`.
- Observation: Root wrapper scripts (`inject_host_segment.py`, `schedule_generator.py`) are stable locally but are not deployed by the current VPS bundle because the zip ships only `src/` and `vps_requirements.txt`.
  Evidence: `/root/radio_host_orchestrator` on VPS did not contain those wrapper files.
- Observation: The first local run of the new deploy script failed because `rsync` was missing locally; after local install, the next run failed because `rsync` was missing on the VPS.
  Evidence: script output (`rsync is required but not installed.` locally; `bash: line 1: rsync: command not found` remotely).
- Observation: `rsync --delete-excluded` would delete excluded snippet media on the VPS.
  Evidence: rsync semantics; script was corrected to use `--delete` with excludes preserved.

## Decision Log

- Decision: Prefer CLI module entrypoints (`python -m neuralcast.cli.<pipeline>`) for VPS cron jobs.
  Rationale: Stable across internal package refactors and compatible with the current deployment layout (`src/` only).
  Date/Author: 2026-02-25 / Codex
- Decision: Deploy only `src/` and `vps_requirements.txt` via `rsync --delete`, with explicit exclusions for generated snippets and bytecode caches.
  Rationale: Keeps code synchronized while protecting generated artifacts and avoiding accidental media sync.
  Date/Author: 2026-02-25 / Codex

## Outcomes & Retrospective

The deployment workflow is now more stable: code deploys use `rsync --delete`, stale deleted files are cleaned up automatically, cron invokes stable CLI module entrypoints, and `AGENTS.md` now documents the updated procedure. Generated story snippet media on the VPS remained intact during sync (snippet directory and MP3 counts verified after deploy).

## Context and Orientation

The VPS app lives at `/root/radio_host_orchestrator`. The current process copies a zip and extracts with `unzip -o`, which overwrites files but does not remove files deleted locally. Recent refactors moved pipeline modules into packages, so stale files can remain on the VPS and mask the fact that cron is still pointing at outdated internal paths.

The repository already has SSH access configured (`ssh neuralvps`) and cron jobs on the VPS export `PYTHONPATH=$(pwd)/src` before running commands.

## Plan of Work

1. Add `deployment/redeploy_host_orchestrator_rsync.sh`:
   - `rsync` local `src/` to remote `${REMOTE_DIR}/src/` with `--delete`
   - sync `vps_requirements.txt`
   - exclude `src/neuralcast/assets/stories/snippets/`, `__pycache__/`, and `*.pyc`
   - perform basic remote verification output (key files exist)
2. Run the script to deploy and remove stale code files on VPS.
3. Verify stale internal pipeline files are gone and CLI module commands still work.
4. Confirm cron jobs use CLI module entrypoints and inspect relevant logs.
5. Update `AGENTS.md` to use the new script and guidance.

## Concrete Steps

Run from repo root:

    bash -n deployment/redeploy_host_orchestrator_rsync.sh
    ./deployment/redeploy_host_orchestrator_rsync.sh
    ssh neuralvps 'crontab -l'
    ssh neuralvps 'cd /root/radio_host_orchestrator && export PYTHONPATH=$(pwd)/src && venv/bin/python3 -m neuralcast.cli.host_orchestrator --help >/dev/null'
    ssh neuralvps 'cd /root/radio_host_orchestrator && export PYTHONPATH=$(pwd)/src && venv/bin/python3 -m neuralcast.cli.schedule_generator --help >/dev/null'
    ssh neuralvps 'tail -n 50 /root/radio_host_orchestrator/host_orchestrator.log'
    ssh neuralvps 'tail -n 50 /root/radio_host_orchestrator/schedule_generator.log'

## Validation and Acceptance

Acceptance criteria:

- `deployment/redeploy_host_orchestrator_rsync.sh` runs successfully.
- Old deleted code files under `/root/radio_host_orchestrator/src/` are removed after deploy.
- VPS cron entries point to `python -m neuralcast.cli.host_orchestrator` / `python -m neuralcast.cli.schedule_generator`.
- CLI `--help` commands run on the VPS in the deployed environment.
- Logs can be tailed and do not show immediate import-path breakage from the refactor.

## Idempotence and Recovery

The rsync deploy script should be safe to rerun. `rsync --delete` is destructive for the synced subset (`src/`), so excludes must remain correct to preserve generated snippet media. Cron edits should be backed up via `crontab -l > /root/crontab.backup.<timestamp>` before changes.

## Artifacts and Notes

Validation / operations summary:

- VPS `rsync` installed and reachable (`rsync 3.2.7`).
- `./deployment/redeploy_host_orchestrator_rsync.sh` succeeded after local+remote `rsync` installs.
- Script verification reported legacy top-level pipeline files as `MISSING`.
- Remote CLI checks passed:
  - `venv/bin/python3 -m neuralcast.cli.host_orchestrator --help`
  - `venv/bin/python3 -m neuralcast.cli.schedule_generator --help`
- Cron snapshot shows CLI module entrypoints (not internal pipeline paths).
- Log checks:
  - `host_orchestrator.log` showed successful recent cycles and one successful segment queue event (no import path errors)
  - `schedule_generator.log` showed prior successful weekly apply run (no import path errors)
- Snippet preservation check (post-rsync):
  - snippets dir exists
  - file count: `112`
  - MP3 count: `56`
- Cron backup created earlier during cron rewrite:
  - `/root/crontab.backup.20260225160931`

## Interfaces and Dependencies

Expected script interface:

- `deployment/redeploy_host_orchestrator_rsync.sh`
  - Defaults to host `neuralvps`
  - Syncs to `/root/radio_host_orchestrator`
  - Uses `rsync`, `ssh`
  - Excludes generated story snippets and Python cache artifacts

Revision note (2026-02-25 / Codex): Created plan for rsync-based VPS deploy workflow migration and verification.
Revision note (2026-02-25 / Codex): Updated after implementation with script creation, rsync rollout, VPS cleanup verification, cron/log checks, and AGENTS.md workflow changes.
```
