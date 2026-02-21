# NeuralCast

NeuralCast is an automation system for maintaining music libraries and generating AI-driven audio content for radio stations. It manages playlist synchronization (CSV <-> MP3), metadata validation, and AI host segment injection into AzuraCast streams.

## Project Structure

- **Core Logic:** `src/neuralcast/` contains the implementation (pipelines, services, models).
- **Entry Points:**
  - `main.py`: Syncs playlists, downloads missing songs, and normalizes tags.
  - `inject_host_segment.py`: Primary entrypoint for generating and pushing AI host segments to AzuraCast.
  - `inject_story_snippet.py`: Backward-compatible alias to `inject_host_segment.py`.
  - `update_new_releases.py`: Updates "New Releases" playlists via Spotify.
- **Station Data:** Directories like `NeuralCast/` and `NeuralForge/` store:
  - `playlists/`: CSV files defining the music rotation.
  - `songs/`: Directory structure mirroring playlists containing MP3s.
  - `metadata/`: JSON caches for Spotify/Release data.
  - `logs/`: Operation reports (e.g., `duplicate_analysis.log`).

## Usage

### Playlist Synchronization
To audit and sync a station's library (e.g., `NeuralCast`):
```bash
# Dry run (safe, just checks tags/files)
python main.py --station NeuralCast --dry-run

# Full run (downloads, modifies files)
python main.py --station NeuralCast
```

### Host Segment Injection
To generate and inject an AI host segment:
```bash
python inject_host_segment.py --station neuralcast --dry-run
```

### Dependency Setup
Ensure external tools (`ffmpeg`, `yt-dlp`, `mp3gain`) are installed. Python dependencies:
```bash
python -m pip install pandas mutagen spotipy musicbrainzngs python-dotenv tqdm requests openai pydantic
```

## Development Conventions

- **Code Style:** Black-compatible formatting, 4-space indentation.
- **Typing:** Use type hints (`Optional`, `List`, etc.) and dataclasses.
- **Naming:** `snake_case` for functions/variables, `PascalCase` for classes.
- **Testing:** Currently relies on manual verification and "dry-runs". Use `notebooks/` for experimental logic. **The agent must not run `main.py` to test changes; the user will handle execution of the main pipeline manually.**
- **Configuration:** Secrets (API keys for OpenAI, Spotify, AzuraCast) must be in a `.env` file. **Never commit secrets.**

## Key Documentation
- `AGENTS.md`: Detailed developer guidelines, testing procedures, and architectural notes.
- `readme.md`: General project overview and workflow.
- `deployment/INSTRUCTIONS.md`: VPS deployment guide.
