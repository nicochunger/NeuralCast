# Repository Guidelines

## Start Here

- Work from the repository root and work directly on `main` unless the user explicitly asks for a branch.
- Treat station data, generated media, and live AzuraCast state on this VPS as operational data. Inspect the relevant pipeline before deleting files, running sync, or deploying.
- Preserve unrelated user changes in a dirty worktree.
- Prefer package entrypoints and code under `src/neuralcast/`; root scripts are compatibility shims.
- The supported station slugs are `neuralcast` and `neuralforge`. The CLI default is `neuralforge`.

## Current Architecture

The project is package-first under `src/neuralcast/`:

- `cli/`: stable command entrypoints for playlist sync, new releases, host orchestration, schedule generation, and the admin API.
- `pipelines/station_sync.py`: authoritative station playlist/media synchronization service.
- `pipelines/playlist_sync.py`: compatibility facade over `station_sync.py`; do not add new core behavior here.
- `pipelines/new_releases/`: Deezer-backed new-release discovery and playlist updates.
- `pipelines/host_orchestrator/`: AzuraCast queue inspection, story generation, TTS, upload, cleanup, and interrupting-request insertion.
- `pipelines/schedule_generator/`: weekly AzuraCast schedule planning and application.
- `playlists/catalog.py`: authoritative playlist CSV parsing, round-tripping, deletion markers, YouTube overrides, and New Releases companion metadata.
- `playlists/utils.py`: MP3/library reconciliation, filename sanitation, deduplication, and deletion helpers.
- `audio/`: yt-dlp download, ID3 tagging, ReplayGain, and album-art handling.
- `metadata/`: album lookup, track resolution, and station metadata storage.
- `services/`: validation and AI/provider clients.
- `admin_api/`: authenticated HTTP API and disk-backed jobs for host/schedule operations plus read-only live station views.
- `assets/`: fallback images and story prompt/style assets.

Root scripts `main.py`, `update_new_releases.py`, `inject_host_segment.py`, and `schedule_generator.py` bootstrap `src/` and dispatch into `neuralcast.cli.*`. Keep them thin and guarded by `if __name__ == "__main__":`.

Station directories (`NeuralCast/` and `NeuralForge/`) contain:

- `playlists/`: CSV source of truth for station playlists.
- `songs/<playlist>/`: MP3 files mirrored per playlist.
- `metadata/`: New Releases metadata/artist caches and host/schedule runtime state.
- `tts_snippets/`: station-local scripted/generated drops.

Story assets shared by both stations live under `src/neuralcast/assets/stories/`. Generated host snippets live under `src/neuralcast/assets/stories/snippets/<station>/<YYYY-MM-DD>/`. Do not move these paths; deployment and cleanup code depend on them.

## Installation and Tests

Editable/repository-layout installs are the supported runtime model:

```bash
python -m pip install -e .
python -m pip install -e '.[dev]'
```

Run the default offline suite with:

```bash
python -m pytest
```

`pyproject.toml` excludes `integration` and `live` tests by default. Only run those markers when the task requires external tools/services and the necessary credentials are available. Tests are organized under `tests/unit/`, `tests/boundary/`, and opt-in `tests/integration/`.

For CSV-only edits, also parse every changed CSV with Python's `csv` module or pandas and verify:

- every row has exactly the header's fields;
- every new row has `Validated=False`;
- commas and embedded quotes are correctly CSV-escaped;
- no unintended same-playlist artist/title duplicate was introduced.

Do not treat playlist sync `--dry-run` as a unit test or a read-only command. See the sync warning below.

## Playlist CSV Contract

Normal playlist rows use these logical fields:

```text
Artist,Title,Album,Year,Validated
```

Some existing files use `Artist,Title,Year,Album,Validated`. The catalog resolves columns by name, so preserve each file's existing header order rather than mechanically reordering it.

When editing playlists:

- Set `Validated` to `False` on every added or materially changed row. The resolver owns validation.
- Keep the displayed artist/title canonical because the pair is the song identity and becomes the MP3 filename.
- MP3 names are `Artist - Title.mp3`, with `/` and `\` replaced by spaces through `sanitize_filename_component`.
- Same-playlist identity is case-insensitive after trimming. Do not add the same artist/title pair twice.
- Cross-playlist repetition is allowed and intentional. The sync writes a station-level report to `<station>/duplicate_analysis.log`; do not “clean up” cross-playlist repeats unless asked.
- Preserve optional/extra CSV columns. `StationPlaylistCatalog` round-trips existing columns.
- Do not hand-edit generated `duplicate_analysis.log` files.

### CSV Rows and Existing MP3s Must Be Changed Together

The sync reconciles each `songs/<playlist>/` directory back into its CSV. If an MP3 remains after its CSV row is removed, `backfill_songs_from_library` can add the row back on the next run.

Therefore:

- When removing only a redundant spelling/version from one playlist, remove both that CSV row and its exact MP3 from that playlist directory.
- Leave MP3s and rows in other playlists untouched.
- Before deleting an MP3, verify that the retained duplicate exists and identify the exact filename being removed.
- Do not use `[DEL]` for a playlist-local cleanup when the artist/title should remain in another playlist; `[DEL]` is global within the station sync.

### `[DEL]` Global Deletion Marker

Prefix either the `Artist` or `Title` field with `[DEL]` to request deletion:

```csv
[DEL] Old Artist,Old Song,Old Album,1999,False
```

Actual behavior is broader than the row's source playlist:

- The catalog strips `[DEL]` and creates a deletion request for the artist/title identity.
- Before syncing individual playlists, the station sync collects all deletion requests globally.
- It deletes `Artist - Title.mp3` from every playlist directory under that station's `songs/` root.
- It removes the matching artist/title row from every loaded playlist, then drops the marker row when CSVs are saved.

Use `[DEL]` only when the recording should disappear from the entire station. Search all station playlists first. This deletion processing currently runs even with `--dry-run`, so a dry-run containing `[DEL]` markers is destructive.

For `New Releases.csv`, removals also clean matching entries from `metadata/New Releases.metadata.json`.

### Forced YouTube Replacement

To force a specific YouTube source, prefix the `Artist` field with a bracketed YouTube URL and keep the artist after it:

```csv
[https://youtu.be/VIDEO_ID] Artist,Title,Album,2024,False
```

Supported hosts are `youtube.com` and `youtu.be`. On a non-dry sync, the pipeline:

1. targets `songs/<playlist>/Artist - Title.mp3`;
2. renames an existing file to `.mp3.bak`;
3. downloads the exact URL with yt-dlp (no search fallback);
4. tags the replacement and reapplies art/ReplayGain;
5. deletes the backup and removes the bracketed URL from the CSV after success.

If replacement fails, the original backup is restored and the override remains available for retry. In `--dry-run`, the replacement is only reported and the URL remains in the CSV.

Use this syntax to replace a bad recording, live version, cover, or incorrect search result. Do not put the URL in `Title`, `Album`, or a separate invented column.

## Playlist Sync Commands and Side Effects

Preview planned playlist and media changes:

```bash
python main.py --station neuralcast --dry-run
```

Apply changes directly to the live AzuraCast media tree:

```bash
python main.py --station neuralcast
```

Important: playlist `--dry-run` skips new MP3 downloads, but it is not fully read-only locally. Current code may still:

- process `[DEL]` markers and delete MP3s;
- rewrite/sort playlist CSVs and validation results;
- remove invalid/unavailable rows and invalid existing files;
- rename/backfill files discovered in playlist directories;
- retag existing MP3s and reapply album art/ReplayGain;
- regenerate `duplicate_analysis.log`.

Never run it merely to inspect data if those local changes are not authorized. For a read-only audit, parse CSVs and inspect files directly.

`<station>/songs` is a symlink to that station's AzuraCast media directory. The VPS media tree is authoritative, so playlist sync writes and deletes operate on the live media files directly; no rsync or separate media mirror is used.

## New Releases and Station Metadata

Preview/apply with:

```bash
python update_new_releases.py -s neuralcast --dry-run
python update_new_releases.py -s neuralcast
```

New-release discovery is Deezer-backed and does not require Spotify credentials. Optional Spotify album-lookup fallbacks still use `SPOTIFY_CLIENT_ID` and `SPOTIFY_CLIENT_SECRET`.

Keep these station metadata files synchronized and committed when release data changes:

- `<station>/metadata/New Releases.metadata.json`
- `<station>/metadata/ArtistIDs.json`

The storage helpers may read legacy locations but canonical writes belong under `metadata/`. Do not delete that directory.

## Host Orchestrator, Scheduler, and Admin API

Prefer module entrypoints for cron and other operational work in this VPS-resident checkout:

```bash
python -m neuralcast.cli.host_orchestrator --dry-run -s neuralforge
python -m neuralcast.cli.schedule_generator --dry-run -s neuralforge
python -m neuralcast.cli.admin_api
```

Host and schedule dry-runs still read live AzuraCast APIs and require valid credentials. Remove `--dry-run` only when uploads, queue changes, or schedule application are intended.

The host orchestrator uses prompts under `src/neuralcast/assets/stories/prompts/`, persistent style history at `src/neuralcast/assets/stories/style_history.json`, and generated snippets under `src/neuralcast/assets/stories/snippets/`. Keep `style_history.json` checked in and preserve generated snippet directories during deployment.

The admin API requires `NEURALCAST_ADMIN_HTTP_TOKEN`; live station views also require `AZURACAST_BASE_URL` and `AZURACAST_API_KEY`. Its persistent jobs/logs live under `runtime/admin_http/`. The canonical service unit is `deployment/systemd/neuralcast-admin-api.service`.

## VPS-Resident Development and Media

This repository is the production and development workspace on the VPS at `/root/projects/NeuralCast`. Run tests, commit, and push from this checkout; do not treat another local checkout as a deployment source. The former rsync deployment workflow is retired; do not use `deployment/redeploy_host_orchestrator_rsync.sh` for normal changes.

Keep all project-local operational state close to the checkout:

- `.env`: Git-ignored VPS secrets;
- `.venv/`: Git-ignored runtime environment;
- `runtime/admin_http/`: admin API jobs and job logs;
- `runtime/logs/`: scheduled-service logs;
- `NeuralCast/songs` and `NeuralForge/songs`: symlinks to their existing AzuraCast media roots.

AzuraCast's media directories are the only MP3 copies. Do not replace the `songs/` symlinks or introduce another media mirror. This checkout and its linked media tree are authoritative.

After a runtime code change, restart `neuralcast-admin-api` when applicable and verify `/healthz`. Cron-launched host/schedule commands load new code on their next invocation. See `deployment/INSTRUCTIONS.md` for the current service and cron layout.

## Environment and External Tools

Keep secrets out of Git. Relevant variables include:

- `AZURACAST_API_KEY`, `AZURACAST_BASE_URL`, `AZURACAST_STATION`
- `GEMINI_API_KEY`, optional `GEMINI_TEXT_MODEL` and `GEMINI_TTS_MODEL`
- optional `OPENAI_API_KEY`
- optional `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET`
- `NEURALCAST_ADMIN_HTTP_TOKEN`, optional admin host/port settings
- `NC_YTDLP_COOKIES_FILE` or `NC_YTDLP_COOKIES_FROM_BROWSER` when YouTube requires authentication

Music workflows require yt-dlp, ffmpeg, and mp3gain. Album art and validation may require network access to MusicBrainz/provider APIs.

The fallback cover is `src/neuralcast/assets/images/Thumbnail_logo.png`. Keep that exact filename available if the image is customized.

## Coding and Review Conventions

- Use Black-compatible formatting, 4-space indentation, and type hints for new Python code.
- Use snake_case for functions and PascalCase for classes/dataclasses.
- Keep domain behavior in the authoritative package module rather than compatibility shims.
- Preserve atomic catalog writes and companion-metadata cleanup when changing playlist persistence.
- Add focused unit tests for behavior changes and boundary tests when several internal modules interact.
- Mock external HTTP, subprocess, filesystem, and credential boundaries in the default test suite.
- Do not commit generated runtime logs, cache files, downloaded dependencies, or temporary lock artifacts created only by local tooling.

Commits use short imperative subjects. Keep feature/code work separate from unrelated station-data edits. Pull requests should summarize behavior, include reproducible test/manual evidence, and list new environment or deployment requirements.
