# Repository Guidelines

## Project Structure & Module Organization
The repo is now package-first under `src/neuralcast/`. Root scripts such as `main.py`, `update_new_releases.py`, `inject_host_segment.py`, and `schedule_generator.py` are compatibility shims that bootstrap `src/` and dispatch into `neuralcast.cli.*` entrypoints. Core logic is split across subpackages: `src/neuralcast/pipelines/` (playlist sync, new releases, host orchestrator, schedule generator), `src/neuralcast/audio/` (download/tagging/art), `src/neuralcast/metadata/` (album lookup), `src/neuralcast/playlists/` (CSV helpers), and `src/neuralcast/services/` (API clients).
Station data still lives alongside the code in station folders (currently `NeuralCast/` and `NeuralForge/`). Each station keeps `playlists/` (CSV definitions), `songs/` (MP3 catalog mirrored per playlist), `metadata/` (Spotify cache + `New Releases.metadata.json` + orchestrator/schedule state/logs), and `tts_snippets/` for generated/scripted drops. Generated reports such as `duplicate_analysis.log` and `albums_not_validated.csv` land in the station folder.
Global storytelling assets live under `src/neuralcast/assets/stories/`, including `style_history.json`, `snippets/<station>/<YYYY-MM-DD>/`, and `prompts/` used by host-orchestrator and schedule-generation flows. Keep that layout intact so the AzuraCast tooling can resolve prompts, persist style history, and clean up old media. Album-art fallbacks reside in `src/neuralcast/assets/images/Thumbnail_logo.png`; if you customize the image, keep a copy or symlink named `Thumbnail_logo.png` there so `audio.download.tag_mp3` can embed it when playlists lack album metadata.

## Build, Test, and Development Commands
- `python -m pip install -e .` installs the package and CLI entrypoints from `pyproject.toml` for local development. Use `python -m pip install -e '.[dev]'` if you also want the optional dev extras (currently `pytest`).
- Runtime workflows currently assume editable/repo-layout installs; wheel-safe packaging for all runtime assets is deferred.
- `python main.py --station neuralforge --dry-run` audits playlists and tags without writing MP3s (root shim for `neuralcast.cli.sync_playlists`); run this before shipping changes.
- `python main.py --station neuralforge` performs the full sync, including downloads and tag rewrites.
- `python main.py --station neuralforge --sync-remote` performs local sync and then mirrors `<station>/songs/` to AzuraCast media via rsync (`--delete` enabled by default, with `AI Stories/***` excluded).
- `python update_new_releases.py -s neuralforge --dry-run` previews Spotify-driven updates to `New Releases.csv`; drop `--dry-run` to write results (shim for `neuralcast.cli.update_new_releases`).
- `python inject_host_segment.py --base-url https://192.168.1.226 -s neuralforge --dry-run` exercises the AzuraCast host orchestrator locally (no uploads); remove `--dry-run` only when you intend to push the MP3 and queue it live (shim for `neuralcast.cli.host_orchestrator`).
- `python schedule_generator.py --base-url https://192.168.1.226 -s neuralforge --dry-run` generates and validates a weekly AzuraCast schedule plan without writing it (shim for `neuralcast.cli.schedule_generator`).
- `python -m neuralcast.cli.host_orchestrator --dry-run -s neuralforge` and `python -m neuralcast.cli.schedule_generator --dry-run -s neuralforge` are the stable module entrypoints to prefer in cron/scripts and for VPS parity.
- `./deployment/redeploy_host_orchestrator_rsync.sh` syncs the VPS host-orchestrator code (`src/` + `vps_requirements.txt`) using `rsync --delete` while preserving generated story snippets and excluding cache files.

## VPS Redeploy Procedure
When asked to redeploy the host orchestrator package to the VPS, use the rsync deploy script:

1. Run the deploy script locally from the repo root:
   - `./deployment/redeploy_host_orchestrator_rsync.sh`

The script syncs `src/` and `vps_requirements.txt` to `/root/radio_host_orchestrator`, removes stale deleted code files via `rsync --delete`, preserves generated snippet media under `src/neuralcast/assets/stories/snippets/`, and prints a verification summary (including checks that legacy top-level pipeline files are gone).

Prerequisites:
- `rsync` must be installed both locally and on the VPS.
- SSH access to `neuralvps` must be working.

After deploy, verify the deployed host-orchestrator entrypoint timestamp or checksum in `/root/radio_host_orchestrator/src/neuralcast/pipelines/host_orchestrator/main.py` when needed.

Cron guidance (VPS):
- Cron jobs should invoke stable CLI module entrypoints (for example `python -m neuralcast.cli.host_orchestrator` and `python -m neuralcast.cli.schedule_generator`) instead of internal `src/neuralcast/pipelines/*.py` file paths.
- Root-level wrapper scripts (`inject_host_segment.py`, `schedule_generator.py`) are stable locally but are not included in the VPS rsync subset unless you explicitly add them.

Additional rule for host-orchestrator/scheduler edits:
- Whenever a requested change modifies host-orchestrator runtime code/assets, automatically run the VPS rsync redeploy script after finishing the change, unless explicitly told not to deploy.
- Treat these paths as mandatory redeploy triggers:
  - `src/neuralcast/pipelines/host_orchestrator/main.py`
  - `src/neuralcast/pipelines/host_orchestrator/*.py`
  - `src/neuralcast/pipelines/schedule_generator/*.py`
  - `src/neuralcast/cli/schedule_generator.py`
  - `src/neuralcast/cli/host_orchestrator.py`
  - `inject_host_segment.py`
  - `src/neuralcast/assets/stories/prompts/*.md`

## Station Metadata & Spotify Cache
`update_new_releases.py` and `main.py` both rely on `<station>/metadata/New Releases.metadata.json` to store structured playlist metadata plus `<station>/metadata/ArtistIDs.json` for cached Spotify artist IDs. The helpers automatically fall back to legacy copies under `playlists/` but will rewrite them into `metadata/` on the next save—do not delete the directory. When songs leave `New Releases.csv`, `main.py` calls `remove_new_releases_metadata_entries` so the JSON stays in sync; keep these files committed alongside the playlists whenever you touch release data.

## Playlist Editing
When adding new songs to playlist CSVs, always set the `Validated` column to `False` for the new rows so the pipeline can re-validate them.

## Coding Style & Naming Conventions
Follow Black-compatible, 4-space indentation with type hints where practical; the existing modules use dataclasses, Optional typing, and explicit return types. Function names stay snake_case (`youtube_to_mp3`), while classes and dataclasses use PascalCase (`AlbumMatch`). MP3 filenames should remain `Artist - Title.mp3`, sanitized via `sanitize_filename_component`. Keep side-effecting scripts guarded by `if __name__ == "__main__":` blocks to support imports.

## Testing Guidelines
Use `pytest` for automated coverage (`tests/test_story_orchestrator.py`, `tests/test_schedule_generator.py`) plus dry-run executions and targeted notebook checks (`tests.ipynb`, `test_album_art.ipynb`) for operational validation. When touching validation or tagging flows, capture console summaries plus the regenerated `duplicate_analysis.log` for review. The host orchestrator supports `--dry-run`, which still reads AzuraCast APIs and requires valid API credentials while skipping upload/queue mutation; attach those logs (and any queue screenshots for live runs) to document manual tests. Document manual test steps in your pull request so reviewers can replay them quickly.

## Commit & Pull Request Guidelines
Commits in this repo use short, imperative subjects (`Improve album lookup`, `Fix load playlist output length`). Group related edits together and avoid mixing feature work with data-only changes. Pull requests should include: 1) a concise summary of behavior changes, 2) manual test evidence (command output or log locations), and 3) any new configuration requirements (e.g., `.env` keys for Spotify, OpenAI, or AzuraCast). Add screenshots only when UI artifacts change, otherwise link to the relevant report files.

## Environment & Credentials
The music metadata pipeline depends on `yt-dlp`, `ffmpeg`, `mp3gain`, and Spotify/MusicBrainz credentials loaded via `.env`. Verify `.env` contains `SPOTIFY_CLIENT_ID` and `SPOTIFY_CLIENT_SECRET` before running validation or new-release discovery, set `GEMINI_API_KEY` for `src/neuralcast/services/ai_client.py` story generation, and define `AZURACAST_API_KEY` (plus optional `AZURACAST_BASE_URL`/`AZURACAST_STATION`) before running `inject_host_segment.py`. Optional remote music mirroring uses rsync and can be configured with `NC_REMOTE_SYNC_HOST`, `NC_REMOTE_SYNC_USER`, `NC_REMOTE_SYNC_PORT`, station-specific `NC_REMOTE_SYNC_MEDIA_ROOT_<STATION_SLUG_UPPER>` overrides, shared `NC_REMOTE_SYNC_MEDIA_ROOT` (default `/var/lib/docker/volumes/azuracast_station_data/_data/{station}/media`), `NC_REMOTE_SYNC_SSH_KEY`, and `NC_REMOTE_SYNC_TIMEOUT_SECONDS`. Keep secrets out of Git—reference variable names and required scopes in docs instead, and confirm locals install `yt-dlp`, `ffmpeg`, and `mp3gain` system-wide. For VPS rsync deploys, ensure `rsync` is installed both locally and on the VPS.

## Story Snippet Automation
`inject_host_segment.py` ties together AzuraCast queue polling, Gemini story generation, deterministic style selection with `src/neuralcast/assets/stories/style_history.json`, TTS synthesis via `src/neuralcast/services/ai_client.py`, and media uploads back to the station. The script reads prompt assets under `src/neuralcast/assets/stories/prompts/` (including `personality.md`, wrappers, and `tts_instructions.md`), writes assets under `src/neuralcast/assets/stories/snippets/<station>/<date>/`, cleans up stale items with `--keep-local-days` / `--keep-remote-days`, and pushes the final MP3 into AzuraCast’s `AI Stories/` folder before queuing it through the telnet `interrupting_requests.push` command. Keep the style history file checked in so the variant-avoidance logic works across runs, and document any changes to prompts or AzuraCast credentials in your PR.

## ExecPlans
 
When writing complex features or significant refactors, use an ExecPlan (as described in .agent/PLANS.md) from design to implementation.
