# Repository Guidelines

## Project Structure & Module Organization
`main.py` orchestrates playlist maintenance, coordinating helpers in `audio_utils.py`, `playlist_utils.py`, `album_lookup.py`, and `validation_utils.py`. Station data lives alongside the code; for example, `NeuralCast/playlists/` holds CSV definitions while `NeuralCast/songs/` stores the MP3 catalog mirrored per playlist. Generated reports (`duplicate_analysis.log`, `albums_not_validated.csv`) land in each station folder.
Each station directory (currently `NeuralCast/` and `NeuralForge/`) additionally keeps a `metadata/` folder for Spotify cache files (`ArtistIDs.json`) and the `New Releases.metadata.json` payload, plus `tts_snippets/` for scripted host drops. `update_new_releases.py` and `main.py` expect this structure and will migrate any legacy `New Releases.metadata.json` that still lives under `playlists/`.
Global storytelling assets now live under `src/neuralcast/assets/stories/`, which holds `style_history.json`, `snippets/<station>/<YYYY-MM-DD>/`, and a `prompts/` folder consumed by `story_variation.py` and `inject_host_segment.py` (with `inject_story_snippet.py` as legacy alias). Keep that layout intact so the AzuraCast injector can find prompts, remember recent styles, and clean up old media. Album-art fallbacks reside in `src/neuralcast/assets/images/Thumbnail_logo.png`; if you customize the image, keep a copy or symlink named `Thumbnail_logo.png` there so `audio.download.tag_mp3` can embed it when playlists lack album metadata.

## Build, Test, and Development Commands
- `python main.py --station NeuralCast --dry-run` audits playlists and tags without writing MP3s—run this before shipping changes.
- `python main.py --station NeuralCast` performs the full sync, including downloads and tag rewrites.
- `python update_new_releases.py NeuralCast --dry-run` previews Spotify-driven updates to `New Releases.csv`; drop `--dry-run` to write results.
- `python inject_host_segment.py --base-url https://192.168.1.226 -s neuralcast --dry-run` exercises the AzuraCast host orchestrator locally (no uploads); remove `--dry-run` only when you intend to push the MP3 and queue it live.
- `./deployment/redeploy_host_orchestrator_rsync.sh` syncs the VPS host-orchestrator code (`src/` + `vps_requirements.txt`) using `rsync --delete` while preserving generated story snippets and excluding cache files.
- `python -m pip install pandas mutagen spotipy musicbrainzngs python-dotenv tqdm requests openai google-genai pydantic pillow` installs the Python dependencies used across the pipeline; document any other tools you introduce.

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
  - `src/neuralcast/pipelines/story_injector.py` (legacy compatibility shim)
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
There is no automated test suite yet; rely on dry-run executions and targeted notebook checks (`tests.ipynb`, `test_album_art.ipynb`) to validate logic. When touching validation or tagging flows, capture console summaries plus the regenerated `duplicate_analysis.log` for review. The host orchestrator supports `--dry-run`, which should produce text + audio artifacts under `src/neuralcast/assets/stories/snippets/<station>/` without contacting AzuraCast—attach those logs (and any queue screenshots for live runs) to document manual tests. Document manual test steps in your pull request so reviewers can replay them quickly.

## Commit & Pull Request Guidelines
Commits in this repo use short, imperative subjects (`Improve album lookup`, `Fix load playlist output length`). Group related edits together and avoid mixing feature work with data-only changes. Pull requests should include: 1) a concise summary of behavior changes, 2) manual test evidence (command output or log locations), and 3) any new configuration requirements (e.g., `.env` keys for Spotify, OpenAI, or AzuraCast). Add screenshots only when UI artifacts change, otherwise link to the relevant report files.

## Environment & Credentials
The music metadata pipeline depends on `yt-dlp`, `ffmpeg`, `mp3gain`, and Spotify/MusicBrainz credentials loaded via `.env`. Verify `.env` contains `SPOTIFY_CLIENT_ID` and `SPOTIFY_CLIENT_SECRET` before running validation or new-release discovery, set `GEMINI_API_KEY` for `src/neuralcast/services/openai_client.py` story generation, and define `AZURACAST_API_KEY` (plus optional `AZURACAST_BASE_URL`/`AZURACAST_STATION`) before running `inject_host_segment.py` (or legacy alias `inject_story_snippet.py`). Keep secrets out of Git—reference variable names and required scopes in docs instead, and confirm locals install `yt-dlp`, `ffmpeg`, and `mp3gain` system-wide. For VPS rsync deploys, ensure `rsync` is installed both locally and on the VPS.

## Story Snippet Automation
`inject_host_segment.py` (or legacy alias `inject_story_snippet.py`) ties together AzuraCast queue polling, OpenAI story generation, deterministic style selection (`story_variation.py` + `src/neuralcast/assets/stories/style_history.json`), TTS synthesis via `src/neuralcast/services/openai_client.py`, and media uploads back to the station. The script reads prompt assets under `src/neuralcast/assets/stories/prompts/` (including `personality.md`, wrappers, and `tts_instructions.md`), writes assets under `src/neuralcast/assets/stories/snippets/<station>/<date>/`, cleans up stale items with `--keep-local-days` / `--keep-remote-days`, and pushes the final MP3 into AzuraCast’s `AI Stories/` folder before queuing it through the telnet `interrupting_requests.push` command. Keep the style history file checked in so the variant-avoidance logic works across runs, and document any changes to prompts or AzuraCast credentials in your PR.

## ExecPlans
 
When writing complex features or significant refactors, use an ExecPlan (as described in .agent/PLANS.md) from design to implementation.
