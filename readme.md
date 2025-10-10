# NeuralCast Playlist Maintenance Pipeline

NeuralCast's `main.py` script manages station-specific music libraries by reading CSV playlists from `<station>/playlists`, ensuring the matching MP3 catalog under `<station>/songs`, and keeping metadata synchronized. Use `--station` to pick which station directory to process and `--dry-run` when you only want to audit existing files without downloading new audio.

---

For contributor expectations and workflow details, see [Repository Guidelines](AGENTS.md).

## Prerequisites

Install the core runtime and audio tooling before running the pipeline:

- Python 3 with `pandas` for playlist processing.
- `mutagen` for inspecting and rewriting ID3 tags.
- `yt-dlp` and `ffmpeg` for converting YouTube sources to MP3.
- `mp3gain` so downloaded tracks can be normalized during tagging.

(Optionally configure text-to-speech credentials if you plan to enable the commented `openai_utils` integrations, but they are not required for playlist upkeep.)

---

## Station Layout

Every station lives alongside `main.py` in the repository:

```
<repo root>/
├── <station>/
│   ├── playlists/
│   │   ├── morning_drive.csv
│   │   └── ...
│   └── songs/
│       ├── morning_drive/
│       │   ├── Artist - Title.mp3
│       │   └── ...
│       └── ...
└── main.py
```

When the script starts it prints the resolved playlist and song directories, then enumerates each playlist CSV so operators can confirm the scope before any modifications occur.

---

## Running the Pipeline

```bash
python main.py --station NeuralCast
```

- `--station` (or `-s`) points to the folder containing the `playlists/` and `songs/` directories (defaults to `NeuralCast`).
- `--dry-run` (or `-n`) keeps existing MP3s in place while auditing tags, validations, and reports.

---

## How It Works

1. **Playlist loading & deletion markers** – Each CSV is parsed into song records, collecting any `[DEL]` entries so the matching MP3s can be removed across playlists. The script updates the in-memory lists and rewrites the CSVs when markers are cleared.
2. **Library backfill** – For every playlist folder under `<station>/songs/<playlist>`, `backfill_songs_from_library` inspects existing MP3s to recover metadata into the CSV before further processing.
3. **Deduplication & normalization** – `deduplicate_and_sort_songs` ensures consistent casing/order, removing repeated entries and triggering a CSV save if changes are made.
4. **Validation pass** – Songs already on disk but still marked unvalidated go through `perform_song_validation`. Album confirmations update the row, while failures queue the track for deletion and note reasons for later reporting.
5. **Forced overrides** – Rows with an explicit YouTube URL (`override_url`) bypass search. Existing MP3s are backed up, replaced, re-tagged, and the playlist updated accordingly.
6. **Download queue & tagging** – Remaining songs are split into “existing” versus “missing.” Missing tracks are downloaded with `youtube_to_mp3` and tagged with playlist-aware genres, album data, and ReplayGain via `tag_mp3`.
7. **Post-download cleanup** – Invalid songs identified during validation have their files removed, and the playlist CSV is rewritten to reflect removals or metadata updates.

Throughout the run, per-playlist summaries detail validation totals, download counts, and pending overrides to aid operators reviewing the console log.

---

## Dry-Run Audits

Dry-run mode performs all metadata maintenance without fetching new audio. Existing files are re-tagged when artist/title/year/genre/album fields drift from the playlist CSV, giving you a safe way to normalize tags across the library. Validation still executes so you can review album failures or other issues without modifying the filesystem.

---

## Generated Reports

After processing, the station root (next to `playlists/` and `songs/`) receives a `duplicate_analysis.log` summarizing cross-playlist reuse and an `albums_not_validated.csv` capturing album checks that could not be confirmed. Review these reports along with the console summaries for a complete audit trail.

---

## Future Enhancements

`openai_utils` hooks are still available for speech generation and trivia drops, and the project can be extended with automated scheduling or prompt-driven playlist creation when those features are reintroduced. For now, `main.py` focuses on keeping curated CSV playlists synchronized with your on-disk MP3 catalog.
