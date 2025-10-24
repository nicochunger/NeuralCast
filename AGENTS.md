# Repository Guidelines

## Project Structure & Module Organization
`main.py` orchestrates playlist maintenance, coordinating helpers in `audio_utils.py`, `playlist_utils.py`, `album_lookup.py`, and `validation_utils.py`. Station data lives alongside the code; for example, `NeuralCast/playlists/` holds CSV definitions while `NeuralCast/songs/` stores the MP3 catalog mirrored per playlist. Generated reports (`duplicate_analysis.log`, `albums_not_validated.csv`) land in each station folder.

## Build, Test, and Development Commands
- `python main.py --station NeuralCast --dry-run` audits playlists and tags without writing MP3s—run this before shipping changes.
- `python main.py --station NeuralCast` performs the full sync, including downloads and tag rewrites.
- `python update_new_releases.py NeuralCast --dry-run` previews Spotify-driven updates to `New Releases.csv`; drop `--dry-run` to write results.
- `python -m pip install pandas mutagen spotipy musicbrainzngs python-dotenv tqdm` installs the Python dependencies used across the pipeline; document any other tools you introduce.

## Coding Style & Naming Conventions
Follow Black-compatible, 4-space indentation with type hints where practical; the existing modules use dataclasses, Optional typing, and explicit return types. Function names stay snake_case (`youtube_to_mp3`), while classes and dataclasses use PascalCase (`AlbumMatch`). MP3 filenames should remain `Artist - Title.mp3`, sanitized via `sanitize_filename_component`. Keep side-effecting scripts guarded by `if __name__ == "__main__":` blocks to support imports.

## Testing Guidelines
There is no automated test suite yet; rely on dry-run executions and targeted notebook checks (`tests.ipynb`, `test_album_art.ipynb`) to validate logic. When touching validation or tagging flows, capture console summaries plus the regenerated `duplicate_analysis.log` for review. Document manual test steps in your pull request so reviewers can replay them quickly.

## Commit & Pull Request Guidelines
Commits in this repo use short, imperative subjects (`Improve album lookup`, `Fix load playlist output length`). Group related edits together and avoid mixing feature work with data-only changes. Pull requests should include: 1) a concise summary of behavior changes, 2) manual test evidence (command output or log locations), and 3) any new configuration requirements (e.g., `.env` keys for Spotify or ElevenLabs). Add screenshots only when UI artifacts change, otherwise link to the relevant report files.

## Environment & Credentials
The music metadata pipeline depends on `yt-dlp`, `ffmpeg`, `mp3gain`, and Spotify/MusicBrainz credentials loaded via `.env`. Verify `.env` contains `SPOTIPY_CLIENT_ID` and `SPOTIPY_CLIENT_SECRET` before running discovery scripts, and confirm ElevenLabs keys are present only if you enable `openai_utils.py` TTS features. Keep secrets out of Git—reference variable names and required scopes in docs instead.

## ExecPlans
 
When writing complex features or significant refactors, use an ExecPlan (as described in .agent/PLANS.md) from design to implementation.