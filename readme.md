# NeuralCast Toolkit

NeuralCast is a package-first toolkit for:

- Station playlist synchronization (`main.py` shim -> `neuralcast.cli.sync_playlists`)
- New Releases updates (`update_new_releases.py` shim -> `neuralcast.cli.update_new_releases`)
- AI host orchestration for AzuraCast (`inject_host_segment.py` shim -> `neuralcast.cli.host_orchestrator`)
- Weekly schedule generation for AzuraCast (`schedule_generator.py` shim -> `neuralcast.cli.schedule_generator`)

Default station for CLI workflows is `neuralforge` unless overridden.

## Repository Layout

- `src/neuralcast/`: main Python package
- Root compatibility shims: `main.py`, `update_new_releases.py`, `inject_host_segment.py`, `schedule_generator.py`
- Station folders at repo root (for example `NeuralForge/`, `NeuralCast/`)
- Prompt/media assets: `src/neuralcast/assets/`
- Deployment helpers: `deployment/`
- Docs: `docs/`

For contribution and operational rules, see [AGENTS.md](AGENTS.md).

## Install

Editable install is the supported local workflow:

```bash
python -m pip install -e .
```

Optional dev extras:

```bash
python -m pip install -e '.[dev]'
```

## Runtime Dependencies

Install system tools used by the pipeline:

- `yt-dlp`
- `ffmpeg`
- `mp3gain`

Python dependencies are defined in `pyproject.toml`.

## Environment Variables

Depending on the workflow, configure:

- `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET` (optional Spotify-backed album lookup fallbacks)
- `AZURACAST_API_KEY` (host/schedule API operations)
- `AZURACAST_BASE_URL` (optional if passed via CLI)
- `AZURACAST_STATION` (optional; defaults to `neuralforge`)
- `GEMINI_API_KEY` (story generation/TTS via Gemini path)
- `OPENAI_API_KEY` (optional for OpenAI-specific helpers)

## Common Commands

Playlist sync:

```bash
python main.py --dry-run
python main.py --station neuralcast --dry-run
python main.py --station neuralforge
```

New releases:

```bash
python update_new_releases.py -s neuralforge --dry-run
python update_new_releases.py -s neuralcast
```

Host orchestrator:

```bash
python inject_host_segment.py --dry-run -s neuralforge
python -m neuralcast.cli.host_orchestrator --dry-run -s neuralforge
```

Schedule generator:

```bash
python schedule_generator.py --dry-run -s neuralforge
python -m neuralcast.cli.schedule_generator --dry-run -s neuralforge
```

Notes:

- `host_orchestrator --dry-run` still performs AzuraCast reads and requires API credentials.
- Module entrypoints are preferred for cron/VPS usage.
- The VPS checkout is authoritative: each `<station>/songs/` path is a symlink to its live AzuraCast media directory, so no rsync media mirror is required.
- The new releases updater writes to `New Releases.csv` plus `metadata/New Releases.metadata.json` and `metadata/ArtistIDs.json`.

## Station Data Layout

Each station directory keeps:

- `playlists/` (CSV definitions)
- `songs/` (symlink to AzuraCast's live downloaded/tagged MP3 media directory)
- `metadata/` (New Releases caches + orchestrator/schedule state)
- `tts_snippets/` (manual/scripted snippets)

## Reports and Artifacts

Runtime reports such as `duplicate_analysis.log` are generated during operation but are not intended as committed source artifacts.

## Packaging Note

Current operational guidance assumes repo-layout/editable installs (`pip install -e .`). Wheel-safe packaging for all runtime assets is deferred.
