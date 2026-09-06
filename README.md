# NeuralCast Toolkit

NeuralCast is a package-first toolkit for:

- Station playlist synchronization (`neuralcast.cli.sync_playlists`)
- New Releases updates (`neuralcast.cli.update_new_releases`)
- AI host orchestration for AzuraCast (`neuralcast.cli.host_orchestrator`)
- Weekly schedule generation for AzuraCast (`neuralcast.cli.schedule_generator`)

Default station for CLI workflows is `neuralforge` unless overridden.

## Repository Layout

- `src/neuralcast/`: main Python package
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

## Development Commands

The Makefile uses `.venv/bin/python` by default and accepts a `PYTHON` override:

```bash
make test
make test-unit
make test-boundary
make test-coverage
make test-collect
make clean
```

Run opt-in external suites only when their required services and credentials are
available:

```bash
make test-integration
make test-live
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
python -m neuralcast.cli.sync_playlists --dry-run
python -m neuralcast.cli.sync_playlists --station neuralcast --dry-run
python -m neuralcast.cli.sync_playlists --station neuralforge
```

Playlist sync `--dry-run` performs live read-only validation and reports the
same planned CSV, metadata, tag, and media changes that apply mode would execute.

New releases:

```bash
python -m neuralcast.cli.update_new_releases -s neuralforge --dry-run
python -m neuralcast.cli.update_new_releases -s neuralcast
```

Host orchestrator:

```bash
python -m neuralcast.cli.host_orchestrator --dry-run -s neuralforge
python -m neuralcast.cli.host_orchestrator --dry-run --channel neuralforge-fr
```

Schedule generator:

```bash
python -m neuralcast.cli.schedule_generator --dry-run -s neuralforge
```

Notes:

- `host_orchestrator --dry-run` still performs AzuraCast reads and requires API credentials.
- Use module entrypoints for all operational usage in this VPS-resident checkout.
- This repository is authoritative: each `<station>/songs/` path is a symlink to its live AzuraCast media directory.
- The new releases updater writes to `New Releases.csv` plus `metadata/New Releases.metadata.json` and `metadata/ArtistIDs.json`.

## Host Channels and Archetype Policies

The host configuration is split by responsibility:

- `src/neuralcast/assets/stories/host_channels.json` defines brands, locales,
  AzuraCast channels, and which cadence/archetype profile each channel uses.
- `src/neuralcast/assets/stories/archetype_profiles.json` defines reusable
  archetype profiles, news-topic catalogs, concert-country catalogs, generation
  settings, cooldowns, and geographic/content scope.
- `src/neuralcast/assets/stories/prompts/` contains the localized prompt packs;
  prompts receive their allowed topics and countries from the resolved policy.

Profiles can inherit from another profile. Channels can apply small
`archetype_overrides` using `add`, `remove`, or `replace` for list settings. Use
canonical news IDs and country codes in configuration; localized labels are
kept in the policy catalog and model output is validated against the effective
channel policy.

The French NeuralForge channel currently inherits `neuralforge`, removes
Argentine political/general news, and replaces the concert-country list with
Switzerland (`CH`) only. See [docs/host_channels.md](docs/host_channels.md) for
the schema, precedence rules, and extension examples.

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
