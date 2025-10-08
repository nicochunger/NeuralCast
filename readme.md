# NeuralCast Playlist Maintenance Pipeline

NeuralCast's `main.py` script manages station-specific music libraries by reading CSV playlists from `<station>/playlists`, ensuring the matching MP3 catalog under `<station>/songs`, and keeping metadata synchronized. Use `--station` to pick which station directory to process and `--dry-run` when you only want to audit existing files without downloading new audio. 【F:main.py†L47-L64】【F:main.py†L770-L793】

---

## Prerequisites

Install the core runtime and audio tooling before running the pipeline:

- Python 3 with `pandas` for playlist processing. 【F:main.py†L16-L32】
- `mutagen` for inspecting and rewriting ID3 tags. 【F:main.py†L16-L32】
- `yt-dlp` and `ffmpeg` for converting YouTube sources to MP3. 【F:main.py†L3-L8】【F:audio_utils.py†L1-L59】
- `mp3gain` so downloaded tracks can be normalized during tagging. 【F:audio_utils.py†L29-L55】

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

When the script starts it prints the resolved playlist and song directories, then enumerates each playlist CSV so operators can confirm the scope before any modifications occur. 【F:main.py†L47-L58】【F:main.py†L742-L768】

---

## Running the Pipeline

```bash
python main.py --station NeuralCast
```

- `--station` (or `-s`) points to the folder containing the `playlists/` and `songs/` directories (defaults to `NeuralCast`). 【F:main.py†L770-L793】
- `--dry-run` (or `-n`) keeps existing MP3s in place while auditing tags, validations, and reports. 【F:main.py†L61-L63】【F:main.py†L770-L793】

---

## How It Works

1. **Playlist loading & deletion markers** – Each CSV is parsed into song records, collecting any `[DEL]` entries so the matching MP3s can be removed across playlists. The script updates the in-memory lists and rewrites the CSVs when markers are cleared. 【F:main.py†L80-L160】
2. **Library backfill** – For every playlist folder under `<station>/songs/<playlist>`, `backfill_songs_from_library` inspects existing MP3s to recover metadata into the CSV before further processing. 【F:main.py†L156-L200】
3. **Deduplication & normalization** – `deduplicate_and_sort_songs` ensures consistent casing/order, removing repeated entries and triggering a CSV save if changes are made. 【F:main.py†L200-L226】
4. **Validation pass** – Songs already on disk but still marked unvalidated go through `perform_song_validation`. Album confirmations update the row, while failures queue the track for deletion and note reasons for later reporting. 【F:main.py†L362-L470】
5. **Forced overrides** – Rows with an explicit YouTube URL (`override_url`) bypass search. Existing MP3s are backed up, replaced, re-tagged, and the playlist updated accordingly. 【F:main.py†L230-L336】
6. **Download queue & tagging** – Remaining songs are split into “existing” versus “missing.” Missing tracks are downloaded with `youtube_to_mp3` and tagged with playlist-aware genres, album data, and ReplayGain via `tag_mp3`. 【F:main.py†L336-L430】【F:audio_utils.py†L1-L59】
7. **Post-download cleanup** – Invalid songs identified during validation have their files removed, and the playlist CSV is rewritten to reflect removals or metadata updates. 【F:main.py†L430-L589】

Throughout the run, per-playlist summaries detail validation totals, download counts, and pending overrides to aid operators reviewing the console log. 【F:main.py†L204-L359】

---

## Dry-Run Audits

Dry-run mode performs all metadata maintenance without fetching new audio. Existing files are re-tagged when artist/title/year/genre/album fields drift from the playlist CSV, giving you a safe way to normalize tags across the library. Validation still executes so you can review album failures or other issues without modifying the filesystem. 【F:main.py†L61-L63】【F:main.py†L362-L589】

---

## Generated Reports

After processing, the station root (next to `playlists/` and `songs/`) receives a `duplicate_analysis.log` summarizing cross-playlist reuse and an `albums_not_validated.csv` capturing album checks that could not be confirmed. Review these reports along with the console summaries for a complete audit trail. 【F:main.py†L635-L738】

---

## Future Enhancements

`openai_utils` hooks are still available for speech generation and trivia drops, and the project can be extended with automated scheduling or prompt-driven playlist creation when those features are reintroduced. For now, `main.py` focuses on keeping curated CSV playlists synchronized with your on-disk MP3 catalog. 【F:main.py†L16-L32】【F:main.py†L635-L738】
