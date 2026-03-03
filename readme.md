# NeuralCast Toolkit

NeuralCast is a package-first toolkit for:

- Station playlist synchronization (`main.py` shim -> `neuralcast.cli.sync_playlists`)
- Spotify-based New Releases updates (`update_new_releases.py` shim -> `neuralcast.cli.update_new_releases`)
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

- `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET` (new releases + validation)
- `AZURACAST_API_KEY` (host/schedule API operations)
- `AZURACAST_BASE_URL` (optional if passed via CLI)
- `AZURACAST_STATION` (optional; defaults to `neuralforge`)
- `GEMINI_API_KEY` (story generation/TTS via Gemini path)
- `OPENAI_API_KEY` (optional for OpenAI-specific helpers)
- `NC_REMOTE_SYNC_HOST` (optional rsync SSH host, default: `neuralvps`)
- `NC_REMOTE_SYNC_USER` (optional rsync SSH username)
- `NC_REMOTE_SYNC_PORT` (optional rsync SSH port)
- `NC_REMOTE_SYNC_MEDIA_ROOT` (optional remote path template, default: `/var/lib/docker/volumes/azuracast_station_data/_data/{station}/media`)
- `NC_REMOTE_SYNC_SSH_KEY` (optional SSH key path for rsync)
- `NC_REMOTE_SYNC_TIMEOUT_SECONDS` (optional rsync timeout, default: `300`)

## Common Commands

Playlist sync:

```bash
python main.py --dry-run
python main.py --station neuralcast --dry-run
python main.py --station neuralforge
python main.py --station neuralforge --sync-remote
python main.py --station neuralforge --dry-run --sync-remote
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
- `--sync-remote` mirrors local `<station>/songs/` to AzuraCast media with rsync.
- Remote mirror uses `--delete` by default and excludes `AI Stories/***` so host-generated stories are preserved.

## Station Data Layout

Each station directory keeps:

- `playlists/` (CSV definitions)
- `songs/` (downloaded/tagged MP3s)
- `metadata/` (Spotify cache + orchestrator/schedule state)
- `tts_snippets/` (manual/scripted snippets)

## Reports and Artifacts

Runtime reports such as `duplicate_analysis.log` and `albums_not_validated.csv` are generated during operation but are not intended as committed source artifacts.

## Packaging Note

Current operational guidance assumes repo-layout/editable installs (`pip install -e .`). Wheel-safe packaging for all runtime assets is deferred.
