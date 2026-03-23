# ExecPlan: Replace dual new-releases pipelines with Deezer-only default

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work proceeds.

This document must be maintained in accordance with `.agent/PLANS.md`.

## Purpose / Big Picture

The repository currently exposes two parallel “new releases” pipelines: the historical Spotify-backed default and a newer Deezer-backed standalone/test variant. That split leaks into commands, package names, playlist file names, metadata file names, cache file names, and tests. After this change, a user should be able to run the normal command `python update_new_releases.py -s neuralforge --dry-run` and get Deezer-backed results written to the normal `New Releases.csv` and `New Releases.metadata.json` files, with no separate Deezer-labeled variant remaining in the codebase or station data.

The observable outcome is a single source of truth. A human should be able to inspect the repository, find only one new-releases CLI and pipeline, see that the active station files no longer have `Deezer` in their names, and run focused tests plus a dry-run command that exercise the default path successfully.

## Progress

- [x] (2026-03-23 21:39Z) Audited the current split and confirmed the repository still has separate CLI entrypoints, pipeline packages, tests, and station files for Spotify and Deezer new releases.
- [x] (2026-03-23 21:39Z) Confirmed the active station data migration scope in `NeuralForge`: both `New Releases.csv` and `New Releases Deezer.csv` exist, along with both metadata/cache filename variants.
- [x] (2026-03-23 21:45Z) Replaced the default `src/neuralcast/pipelines/new_releases/main.py` implementation with the Deezer-backed pipeline and renamed its output filenames, cache filenames, help text, and user-facing success messages to the generic defaults.
- [x] (2026-03-23 21:46Z) Removed the Deezer-specific duplicate CLI/package/shim/script entrypoints and updated `pyproject.toml` so only the generic `neuralcast-new-releases` script remains.
- [x] (2026-03-23 21:47Z) Migrated `NeuralForge` station files so the Deezer outputs are now the canonical `New Releases.csv`, `New Releases.metadata.json`, and `ArtistIDs.json`, overwriting the former Spotify-backed versions per user instruction.
- [x] (2026-03-23 21:49Z) Updated tests and documentation to match the single-pipeline design, including renaming the focused test file to `tests/test_new_releases.py` and removing docs for the deleted command.
- [x] (2026-03-23 21:50Z) Ran focused validation: `pytest -q tests/test_new_releases.py tests/test_metadata_storage.py tests/test_playlist_sync.py` passed with `16 passed`, and `python update_new_releases.py --help` shows the consolidated CLI surface.
- [x] (2026-03-23 21:50Z) Investigated the live `python update_new_releases.py -s neuralforge --dry-run` timeout and confirmed it is caused by sandboxed DNS/network restrictions when resolving `api.deezer.com`, not by the refactor itself.

## Surprises & Discoveries

- Observation: the host orchestrator already reads only the generic `New Releases.metadata.json` file name, so the migration should preserve that filename rather than teaching downstream consumers about Deezer-specific metadata names.
  Evidence: `src/neuralcast/pipelines/host_orchestrator/assets.py` resolves `New Releases.metadata.json` directly.
- Observation: removing the Spotify new-releases pipeline will not remove Spotify from the repository because `src/neuralcast/metadata/album_lookup.py` still imports and uses `spotipy`.
  Evidence: repository search shows `spotipy` imports remain in `src/neuralcast/metadata/album_lookup.py`.
- Observation: the focused unit tests are sufficient to validate the refactor mechanically, but the real `neuralforge` dry-run is blocked in this sandbox because DNS resolution for `api.deezer.com` is disabled.
  Evidence: `pytest -q tests/test_new_releases.py tests/test_metadata_storage.py tests/test_playlist_sync.py` returned `16 passed in 1.89s`, and the live dry-run eventually emitted repeated `NameResolutionError` failures for `api.deezer.com`.

## Decision Log

- Decision: implement this as a replacement of the existing `new_releases` package with Deezer logic, not as a compatibility wrapper that keeps `new_releases_deezer` alive underneath.
  Rationale: the user explicitly wants the Deezer variant to become the normal one “without the Deezer labels,” so the cleanest end state is one package path, one CLI, and one set of filenames.
  Date/Author: 2026-03-23 / Codex
- Decision: delete the old Spotify-backed station files rather than backing them up in-repo.
  Rationale: the user explicitly stated the old Spotify files can be deleted and replaced by the Deezer ones.
  Date/Author: 2026-03-23 / Codex

## Outcomes & Retrospective

The refactor achieved the intended repository shape: one generic new-releases CLI, one generic pipeline package, one generic station playlist file, and one generic metadata/cache naming scheme. The former Deezer-only implementation now lives at `src/neuralcast/pipelines/new_releases/main.py`, the old Spotify-backed implementation is gone, and the checked-in `NeuralForge` data now uses only `New Releases.csv`, `New Releases.metadata.json`, and `ArtistIDs.json`.

The remaining gap is operational validation against the live provider path in this sandbox. The focused tests passed and the default CLI help output is correct, but the live `neuralforge` dry-run hits sandbox DNS failures when contacting `api.deezer.com`. That should be rechecked in an environment with outbound network access if a human wants full end-to-end confidence against the real Deezer API.

## Context and Orientation

The default root command today is `update_new_releases.py`, which bootstraps `src/` and dispatches into `src/neuralcast/cli/update_new_releases.py`. That CLI currently imports the Spotify-backed package `src/neuralcast/pipelines/new_releases/main.py`. The separate Deezer root command `update_new_releases_deezer.py` dispatches into `src/neuralcast/cli/update_new_releases_deezer.py`, which then imports `src/neuralcast/pipelines/new_releases_deezer/main.py`.

In this repository, a “pipeline” is the module that discovers artist releases, filters and ranks them, then writes a CSV playlist plus a JSON metadata cache into the station folder. The station folders live at repository root, with playlists under `NeuralForge/playlists/` and structured metadata under `NeuralForge/metadata/`.

The current generic file names are `New Releases.csv`, `New Releases.metadata.json`, and `ArtistIDs.json`. The Deezer test path currently writes `New Releases Deezer.csv`, `New Releases Deezer.metadata.json`, and `DeezerArtistIDs.json`. Downstream consumers such as the host orchestrator already expect the generic metadata filename, so the consolidated Deezer path must write back to the generic names.

## Plan of Work

First, replace the contents of `src/neuralcast/pipelines/new_releases/main.py` with the Deezer implementation from `src/neuralcast/pipelines/new_releases_deezer/main.py`. During that move, rename the module docstrings, filename constants, exclusion list, log messages, and CLI help text so the package behaves as the normal default new-releases pipeline rather than a Deezer-branded test path. Update `src/neuralcast/cli/update_new_releases.py` so its help behavior delegates to the consolidated parser instead of a stale Spotify-oriented parser definition.

Second, remove the duplicate Deezer-specific surface area: delete `src/neuralcast/cli/update_new_releases_deezer.py`, `src/neuralcast/pipelines/new_releases_deezer/`, and the root shim `update_new_releases_deezer.py`. Update `pyproject.toml` so only the generic `neuralcast-new-releases` script remains. Keep `src/neuralcast/pipelines/new_releases/__init__.py` and `__main__.py` aligned with the new implementation.

Third, migrate the checked-in station data. In `NeuralForge/`, replace the generic playlist and metadata/cache files with the Deezer-backed versions by renaming the Deezer files to the generic names and deleting the former Spotify-backed ones. If `NeuralCast/` has no matching files, leave it untouched.

Fourth, update tests and docs. The Deezer pipeline tests should move to the generic module path and generic filenames. The playlist-exclusion test should only exclude `New Releases.csv`, because the `New Releases Deezer.csv` concept will no longer exist. Documentation in `AGENTS.md` and packaging metadata in `pyproject.toml` must describe only one new-releases path.

## Concrete Steps

Run from the repository root:

    git status --short
    pytest -q tests/test_new_releases.py tests/test_metadata_storage.py tests/test_playlist_sync.py
    python update_new_releases.py -s neuralforge --dry-run

Before the refactor, the first test command does not exist because `tests/test_new_releases.py` has not been created yet. After implementation, the tests should pass and the dry-run should report Deezer-backed release collection while writing nothing because `--dry-run` is set.

## Validation and Acceptance

Acceptance means five things are true.

First, the repository contains only one new-releases CLI path and one pipeline package: `src/neuralcast/cli/update_new_releases.py` and `src/neuralcast/pipelines/new_releases/`.

Second, the active pipeline writes the generic filenames `New Releases.csv`, `New Releases.metadata.json`, and `ArtistIDs.json`, and no checked-in station file uses the `Deezer`-suffixed names anymore.

Third, focused tests pass for the consolidated module path and generic filenames. In particular, the former Deezer pipeline tests should import `neuralcast.pipelines.new_releases.main` and assert on `New Releases.csv`, `New Releases.metadata.json`, and `ArtistIDs.json`.

Fourth, `python update_new_releases.py -s neuralforge --dry-run` should execute the Deezer-backed logic from the normal command path and report a dry-run without mentioning a separate Deezer test pipeline.

Fifth, the documentation and packaging metadata should no longer describe two versions of the new-releases workflow.

## Idempotence and Recovery

The code edits are safe to reapply because the end state is deletion of duplicate paths, not a reversible runtime migration. The risky step is the station-data rename in `NeuralForge/`, because it replaces one checked-in playlist and two checked-in metadata/cache files. If validation reveals a problem after the migration, recovery is a normal git restore of those files from version control. No generated media or secrets are involved in this change.

## Artifacts and Notes

Current checked-in station files before migration:

    NeuralForge/playlists/New Releases.csv
    NeuralForge/playlists/New Releases Deezer.csv
    NeuralForge/metadata/New Releases.metadata.json
    NeuralForge/metadata/New Releases Deezer.metadata.json
    NeuralForge/metadata/ArtistIDs.json
    NeuralForge/metadata/DeezerArtistIDs.json

Current package split before migration:

    src/neuralcast/cli/update_new_releases.py
    src/neuralcast/cli/update_new_releases_deezer.py
    src/neuralcast/pipelines/new_releases/main.py
    src/neuralcast/pipelines/new_releases_deezer/main.py

## Interfaces and Dependencies

After completion, the generic CLI and pipeline interfaces should remain:

    update_new_releases.py -> neuralcast.cli.update_new_releases:run
    python -m neuralcast.cli.update_new_releases
    python -m neuralcast.pipelines.new_releases

`src/neuralcast/pipelines/new_releases/main.py` should export the same Deezer-oriented functions that the current standalone package exports, including:

    ArtistIDCache
    ArtistRelease
    build_arg_parser
    build_new_releases
    fetch_recent_releases
    load_existing_new_releases
    load_station_artists
    main
    parse_release_date
    save_new_releases

The consolidated pipeline should continue using existing dependencies already present in the repo: `pandas`, `requests`, `musicbrainzngs`, `tqdm`, and the shared metadata storage helpers under `src/neuralcast/metadata/storage.py`.

Revision note (2026-03-23 / Codex): Created initial ExecPlan before editing code for the Deezer-only consolidation.
Revision note (2026-03-23 / Codex): Updated the plan after implementation with the completed code/data/doc changes and recorded the focused test results plus the live dry-run timeout behavior.
