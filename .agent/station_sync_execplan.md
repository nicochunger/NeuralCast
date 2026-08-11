# ExecPlan: Station Sync Service Refactor

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work proceeds.

This document must be maintained in accordance with `.agent/PLANS.md`.

## Purpose / Big Picture

Refactor the main playlist sync pipeline so callers invoke a single deep module that owns station reconciliation end to end. After this change, the CLI and compatibility shims will call a `StationSync` service through a small request/report interface instead of invoking a thousand-line procedural `main()` function directly. The observable behavior should stay the same: `python main.py --station neuralforge --dry-run` still audits playlists and retags existing files, and `python main.py --station neuralforge` still validates, downloads, tags, and writes reports directly against the VPS-resident station media tree. The difference is architectural: tests can exercise the boundary of “sync this station” without reaching into internal helpers such as `_save_playlist_state` and `_backfill_album_for_missing_song`.

## Progress

- [x] (2026-03-17 07:13Z) Explored the current sync pipeline, adjacent helpers, and tests; identified `StationSync` as the highest-leverage deep-module boundary.
- [x] (2026-03-17 07:13Z) Chose the interface direction: a minimal `StationSync.run()` boundary with private internal action objects inspired by ports-and-adapters.
- [x] (2026-03-17 07:21Z) Created `src/neuralcast/pipelines/station_sync.py` with `SyncRequest`, `PlaylistSyncReport`, `SyncReport`, `StationSync`, default resolver/media adapters, and private playlist action types.
- [x] (2026-03-17 07:21Z) Converted `src/neuralcast/pipelines/playlist_sync.py` into a compatibility wrapper that re-exports the new service boundary and legacy helper names still referenced elsewhere.
- [x] (2026-03-17 07:22Z) Replaced the old helper-specific playlist sync test with boundary coverage for `StationSync.run()` and updated Deezer album-backfill tests to target `DefaultTrackResolver`.
- [x] (2026-03-17 07:23Z) Validated syntax with `python3 -m py_compile` successfully.
- [ ] Run the full targeted Python test suite once project dependencies (`pandas`, `mutagen`, `python-dotenv`, and preferably `pytest`) are installed in the local environment.
- [ ] Create the GitHub RFC issue required by the architecture skill; blocked in this environment because `gh` is not installed.

## Surprises & Discoveries

- Observation: `src/neuralcast/pipelines/playlist_sync.py` currently owns orchestration, persistence, album repair, file mutation, and duplicate analysis in one function.
  Evidence: `main()` spans `src/neuralcast/pipelines/playlist_sync.py:379` through `:1130`.
- Observation: the current tests already expose architectural leakage because they patch private helpers directly.
  Evidence: `tests/test_playlist_sync.py` calls `_save_playlist_state`, and `tests/test_deezer_sync_provider.py` calls `_backfill_album_for_missing_song`.
- Observation: this shell does not provide the `python` alias; commands must use `python3`.
  Evidence: `python -m py_compile ...` returned `/bin/bash: python: command not found`.
- Observation: the local environment is missing several runtime and test dependencies, so only dependency-light validations can run here.
  Evidence: the first local environment lacked several project dependencies; the repository `.venv` is now used for validation.
- Observation: the GitHub CLI is not installed, so the required architecture RFC issue cannot be created from this shell.
  Evidence: `gh --version` returned `/bin/bash: gh: command not found`.

## Decision Log

- Decision: Introduce a new module for the deep boundary instead of only adding a class inside `playlist_sync.py`.
  Rationale: A new module gives the architecture a clear center of gravity and lets `playlist_sync.py` become a compatibility layer rather than remaining the conceptual home of the monolith.
  Date/Author: 2026-03-17 / Codex
- Decision: Keep private action/outcome objects internal to the new service.
  Rationale: This preserves the simple `run(request) -> report` interface while still structuring the implementation around explicit decisions and side effects.
  Date/Author: 2026-03-17 / Codex
- Decision: Preserve `neuralcast.pipelines.playlist_sync` as a thin wrapper instead of renaming callers immediately.
  Rationale: The CLI and any local scripts can keep their current imports while the real implementation center moves to `station_sync.py`.
  Date/Author: 2026-03-17 / Codex
- Decision: Keep legacy helper functions re-exported during this refactor.
  Rationale: This reduces migration risk while the new boundary and tests settle; callers can move off the old names incrementally.
  Date/Author: 2026-03-17 / Codex

## Outcomes & Retrospective

The main sync pipeline now has a dedicated deep-module boundary in `src/neuralcast/pipelines/station_sync.py`. The CLI-facing `main()` path still works through `neuralcast.pipelines.playlist_sync.main`, but that function is now only a compatibility wrapper around `StationSync.run()`. The refactor also introduced boundary-oriented tests for the station sync workflow and shifted album-backfill coverage away from direct calls to a private playlist helper.

The remaining gaps are environmental, not architectural. Full test execution is blocked in this shell until project dependencies are installed, and the GitHub RFC issue could not be created because `gh` is unavailable. Once those two blockers are resolved, the remaining work is validation and documentation, not more design.

## Context and Orientation

The current entrypoint stack is:

- `main.py`, a repository-root shim that bootstraps `src/` and dispatches to `neuralcast.cli.sync_playlists.run`.
- `src/neuralcast/cli/sync_playlists.py`, which parses CLI arguments and calls `neuralcast.pipelines.playlist_sync.main`.
- `src/neuralcast/pipelines/playlist_sync.py`, which currently performs almost the entire station sync workflow inline.

Within this repository, a “station sync” means reconciling the playlist CSV files under `<Station>/playlists/` with the MP3 files under `<Station>/songs/`, while preserving playlist metadata, validating tracks against external providers, repairing album metadata when possible, downloading/tagging missing files, and writing a duplicate-analysis log. The `songs/` paths are symlinks to the live AzuraCast media directories on this VPS.

The helper modules already provide useful behavior, but they are not the right public boundary:

- `src/neuralcast/playlists/utils.py` loads and saves playlist CSVs, parses `[DEL]` rows, reads MP3 tags for library backfill, and deletes marked files.
- `src/neuralcast/services/validation.py` verifies tracks and albums against Deezer, MusicBrainz, and iTunes.
- `src/neuralcast/metadata/album_lookup.py` guesses album metadata and release dates.
- `src/neuralcast/audio/download.py` downloads MP3s via `yt-dlp` and applies ID3 tags plus ReplayGain.

This refactor will add a new service module that depends on those helpers through default adapters. The new service will accept a `SyncRequest` describing which station to sync and whether the run is a dry run, then return a `SyncReport` summarizing playlist-level outcomes and duplicate analysis.

## Plan of Work

Create a new module at `src/neuralcast/pipelines/station_sync.py`. In that file, define public request/report dataclasses and the `StationSync` class, plus private dataclasses that represent internal actions and per-playlist processing state. Move the existing user-visible logging helper (`PlaylistLog`), metadata-cleanup function, output-capture utilities, and album-backfill logic into this module, but reshape them around class methods and injected collaborators.

Define default collaborators in the same module at first to keep the migration tight:

- a resolver adapter for track availability validation and album backfill;
- a media adapter for override replacement, retag audit, downloads, and tagging;
- persistence methods that use `load_playlist`, `save_playlist_with_validation`, and the station metadata helpers.

After the new module works, reduce `src/neuralcast/pipelines/playlist_sync.py` to a compatibility wrapper that re-exports the legacy helper names still used by tests, delegates `main()` to `StationSync.run()`, and keeps `list_playlists()` behavior intact. The wrapper may keep thin compatibility functions around old helper names while the new service becomes the real implementation center.

Update tests so the primary sync tests assert on `StationSync.run()` and `SyncReport` rather than private helpers. Keep service-specific tests for validation and album lookup where those still express stable behavior at their own boundaries.

Finish by validating the targeted test suite and, if possible in the local environment, a dry-run CLI import or execution path. Then create the GitHub issue documenting the refactor interface and testing strategy, as required by the architecture-skill workflow.

## Concrete Steps

Run from the repository root:

    python3 -m py_compile src/neuralcast/pipelines/station_sync.py src/neuralcast/pipelines/playlist_sync.py tests/test_playlist_sync.py tests/test_deezer_sync_provider.py

When the local environment has project dependencies installed, run:

    pytest tests/test_playlist_sync.py tests/test_deezer_sync_provider.py tests/test_audio_download.py

If the environment is configured well enough for a CLI smoke test, run:

    python3 main.py --station neuralforge --dry-run

The expected result is that the command starts the sync workflow and prints the same station/playlist progress style as before. It may still fail on missing local credentials, Python dependencies, or media tooling; if so, capture that exact failure in this plan.

## Validation and Acceptance

Acceptance means:

- `src/neuralcast/cli/sync_playlists.py` still exposes the same CLI arguments and still runs a station sync successfully through the new service boundary.
- A public `StationSync.run()` interface exists and returns a report object with playlist-level outcomes.
- There is at least one boundary test that exercises station sync behavior without patching `_save_playlist_state` or `_backfill_album_for_missing_song`.
- Existing validation- and media-specific tests continue to pass, proving that the refactor preserved behavior at those lower boundaries.
- The compatibility wrapper preserves `neuralcast.pipelines.playlist_sync.main` for existing callers.

## Idempotence and Recovery

This refactor is safe to apply incrementally because the old entrypoint remains available through the wrapper handoff. If a step fails midway, rerun the affected test command after fixing the failing module; there is no schema or data migration. Do not delete station media or metadata while validating. If the CLI smoke test touches external services unexpectedly, rerun it with `--dry-run` only and document any environment-related blockers here.

## Artifacts and Notes

Validation artifacts gathered so far:

- `python3 -m py_compile src/neuralcast/pipelines/station_sync.py src/neuralcast/pipelines/playlist_sync.py tests/test_playlist_sync.py tests/test_deezer_sync_provider.py` completed successfully.
- `PYTHONPATH=src python3 -m unittest tests.test_audio_download` failed to import because `mutagen` is not installed.
- `PYTHONPATH=src python3 -m unittest tests.test_playlist_sync tests.test_deezer_sync_provider ...` failed to import because `pandas`, `python-dotenv`, and `mutagen` are not installed.
- `gh --version` failed because the GitHub CLI is not installed.

## Interfaces and Dependencies

The new public interface in `src/neuralcast/pipelines/station_sync.py` must include:

    from dataclasses import dataclass, field
    from pathlib import Path
    from typing import Protocol

    @dataclass(frozen=True)
    class SyncRequest:
        station_slug: str
        dry_run: bool = False

    @dataclass(frozen=True)
    class PlaylistSyncReport:
        name: str
        initial_song_count: int
        final_song_count: int
        added_from_files: int = 0
        duplicates_removed: int = 0
        removed_count: int = 0
        downloaded_count: int = 0
        failed_count: int = 0
        validation_updated: bool = False
        override_updated: bool = False
        pending_overrides: int = 0

    @dataclass(frozen=True)
    class SyncReport:
        station_slug: str
        dry_run: bool
        playlist_reports: list[PlaylistSyncReport]
        duplicate_analysis_log: Path

    class StationSync:
        def run(self, request: SyncRequest) -> SyncReport:
            ...

The service should use injected collaborators for external boundaries. It is acceptable for the first implementation to define concrete default adapters in the same file as long as the constructor can accept substitutes for tests.

Revision note (2026-03-17 / Codex): Created initial ExecPlan from the architecture exploration and selected interface direction.
Revision note (2026-03-17 / Codex): Updated the plan after implementation with completed progress, validation evidence, environmental blockers, and the compatibility-wrapper decision.
