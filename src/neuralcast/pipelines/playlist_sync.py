#!/usr/bin/env python3
"""
main.py — AI-assisted local-network radio pipeline
-------------------------------------------------
• reads playlists from playlists/ directory
• yt-dlp + ffmpeg  → MP3s
• mutagen          → ID3 tags
• moves files into songs/ directory organized by playlist
"""

import argparse
import contextlib
import io
import json
import pathlib
import unicodedata
from subprocess import CalledProcessError
from typing import Callable, Dict, List, Optional, Tuple

import pandas as pd
from mutagen.easyid3 import EasyID3

from neuralcast.config import (
    ALLOWED_STATION_SLUGS,
    DEFAULT_STATION_SLUG,
    station_dir_from_slug,
)
from neuralcast.audio.download import (
    DownloadNoResultsError,
    DownloadOutputMissingError,
    tag_mp3,
    youtube_to_mp3,
)
from neuralcast.metadata.album_lookup import guess_album
from neuralcast.models import Song
from neuralcast.playlists.utils import (
    backfill_songs_from_library,
    deduplicate_and_sort_songs,
    delete_marked_mp3_files,
    load_playlist,
    normalize_year_value,
    playlist_song_key,
    replace_song_entry,
    sanitize_filename_component,
    save_playlist_with_validation,
)
from neuralcast.services.ai_client import (
    make_fun_fact,
    openai_text_completion,
    tts,
)
from neuralcast.services.validation import (
    perform_song_validation,
    verified,
    verified_album,
)
from neuralcast.pipelines.media_sync import (
    RemoteSyncRequest,
    add_remote_sync_args,
    build_remote_sync_config,
    remote_sync_request_from_args,
    run_remote_sync,
)


# The following paths will be set dynamically based on the station argument
STATION_PATH = None
PLAYLISTS_PATH = None

STATION = "neuralforge"

TTS = False  # turn off if you only want music
VOICE_NAME = "Adam"  # ElevenLabs voice
_METADATA_DIRNAME = "metadata"
_METADATA_FILENAME = "New Releases.metadata.json"


class PlaylistLog:
    def __init__(self, playlist_name: str) -> None:
        self.playlist_name = playlist_name
        self._header_printed = False

    def _ensure_header(self) -> None:
        if not self._header_printed:
            print(f"\n[{self.playlist_name}]")
            self._header_printed = True

    def info(self, message: str) -> None:
        self._ensure_header()
        print(f"  {message}")

    def change(self, message: str) -> None:
        self.info(message)

    def warning(self, message: str) -> None:
        self.info(f"⚠️ {message}")

    def error(self, message: str) -> None:
        self.info(f"❌ {message}")


def _capture_output(func: Callable[[], object]) -> Tuple[object, List[str]]:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        result = func()
    return result, [
        line.rstrip() for line in buffer.getvalue().splitlines() if line.strip()
    ]


def _emit_captured_lines(
    lines: List[str],
    *,
    logger: Callable[[str], None],
    include_plain: bool = False,
) -> None:
    for line in lines:
        if (
            include_plain
            or "⚠️" in line
            or "❌" in line
            or "warning" in line.casefold()
            or "error" in line.casefold()
            or "failed" in line.casefold()
            or "deleted" in line.casefold()
            or "removed" in line.casefold()
        ):
            logger(line)


def _resolve_metadata_paths(playlists_dir: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    """Return (read_path, write_path) for the New Releases metadata file with legacy fallback."""
    metadata_dir = playlists_dir.parent / _METADATA_DIRNAME
    preferred_path = metadata_dir / _METADATA_FILENAME
    legacy_path = playlists_dir / _METADATA_FILENAME
    if preferred_path.exists():
        return preferred_path, preferred_path
    if legacy_path.exists():
        print(
            f"ℹ️ Using legacy metadata path {legacy_path} for New Releases; "
            f"it will migrate to {preferred_path} on next write."
        )
        return legacy_path, preferred_path
    return preferred_path, preferred_path


def remove_new_releases_metadata_entries(
    playlists_dir: pathlib.Path, songs_to_remove: List[Song]
) -> int:
    read_path, write_path = _resolve_metadata_paths(playlists_dir)
    metadata_path = read_path if read_path.exists() else write_path
    if not songs_to_remove:
        return 0
    if not metadata_path.exists():
        print(
            f"⚠️ Metadata file not found at {metadata_path}; skipping metadata cleanup for New Releases"
        )
        return 0

    try:
        with read_path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except Exception as exc:  # noqa: BLE001
        print(f"⚠️ Failed to parse JSON from metadata file {metadata_path}: {exc}")
        return 0

    if isinstance(payload, dict) and isinstance(payload.get("entries"), dict):
        entries = payload["entries"]
        wrapped = True
    elif isinstance(payload, dict):
        entries = payload
        wrapped = False
    else:
        print(
            f"⚠️ Unexpected metadata structure in {metadata_path}; skipping removal of New Releases entries"
        )
        return 0

    def normalize_component(value: Optional[str]) -> str:
        normalized = unicodedata.normalize("NFKC", value or "")
        return normalized.strip().casefold()

    def normalize_year(value: Optional[str]) -> str:
        if value is None:
            return ""
        text = str(value).strip()
        if not text:
            return ""
        try:
            return str(int(text))
        except ValueError:
            return text

    def matching_keys(song: Song) -> List[str]:
        artist_component = normalize_component(song.artist)
        title_component = normalize_component(song.title)
        album_component = normalize_component(song.album) if song.album else ""
        year_component = normalize_year(song.year)

        primary_key = "|".join(
            (artist_component, title_component, album_component, year_component)
        )
        if primary_key in entries:
            return [primary_key]

        album_filter = album_component or None
        year_filter = year_component or None
        candidates: List[str] = []
        for existing_key in entries.keys():
            parts = existing_key.split("|")
            if len(parts) != 4:
                continue
            if parts[0] != artist_component or parts[1] != title_component:
                continue
            if album_filter is not None and parts[2] != album_component:
                continue
            if year_filter is not None and parts[3] != year_component:
                continue
            candidates.append(existing_key)
        return candidates

    removed = 0
    missing: List[Song] = []
    ambiguous: List[Song] = []
    seen_keys = set()

    for song in songs_to_remove:
        unique_key = (
            song.artist.lower().strip(),
            song.title.lower().strip(),
            (song.album or "").strip().lower(),
            (song.year or "").strip(),
        )
        if unique_key in seen_keys:
            continue
        seen_keys.add(unique_key)

        matches = matching_keys(song)
        if not matches:
            missing.append(song)
            continue
        if len(matches) > 1:
            ambiguous.append(song)
            continue

        entries.pop(matches[0], None)
        removed += 1
        song_year = normalize_year(song.year)
        print(f"🗑️ Removed metadata entry for {song.artist} - {song.title} ({song_year})")

    if removed > 0:
        output_payload = {"entries": entries} if wrapped else entries
        try:
            write_path.parent.mkdir(parents=True, exist_ok=True)
            with write_path.open("w", encoding="utf-8") as handle:
                json.dump(output_payload, handle, indent=2, sort_keys=True)
                handle.write("\n")
            print(
                f"🗂️ Updated metadata file: {write_path.name} (removed {removed} entr{'y' if removed == 1 else 'ies'})"
            )
        except TypeError as exc:
            print(f"⚠️ JSON serialization error while writing metadata file {write_path}: {exc}")
        except OSError as exc:
            print(f"⚠️ File write permission error for metadata file {write_path}: {exc}")
        except Exception as exc:  # noqa: BLE001
            print(f"⚠️ Unexpected error while writing metadata file {write_path}: {exc}")

    for song in missing:
        song_year = normalize_year(song.year)
        print(
            f"⚠️ New Releases metadata entry not found for {song.artist} - {song.title} ({song_year}); nothing removed"
        )
    for song in ambiguous:
        print(
            f"⚠️ Multiple New Releases metadata entries matched {song.artist} - {song.title}; skipped removal"
        )

    return removed


def _backfill_album_for_missing_song(
    song: Song,
    *,
    log: Callable[[str], None] = print,
) -> tuple[Song, bool]:
    album_value = (song.album or "").strip()
    year_value = str(song.year).strip() if song.year is not None else ""
    missing_year = not year_value or year_value.casefold() == "unknown"
    track_label = f"{song.artist} - {song.title}"
    album_needs_replacement = False

    def clear_album_metadata() -> tuple[Song, bool]:
        log(f"🧹 album metadata cleared: {track_label} ({album_value})")
        return song.model_copy(update={"album": None}), True

    def handle_missing_match() -> tuple[Song, bool]:
        log(f"⚠️ album lookup found no match for {track_label}")
        if album_needs_replacement:
            return clear_album_metadata()
        return song, False

    if album_value:
        try:
            if verified_album(song.artist, song.title, album_value):
                if not missing_year:
                    return song, False
            else:
                album_needs_replacement = True
                log(f"⚠️ album could not be confirmed for {track_label}: {album_value}")
        except Exception as exc:
            album_needs_replacement = True
            log(f"⚠️ album check failed for {track_label}: {exc}")

    try:
        match = guess_album(
            song.artist,
            song.title,
            prefer_spotify=False,
            prefer_deezer=True,
            min_confidence=0.55,
            allow_fallback=True,
        )
    except Exception as exc:
        log(f"⚠️ album lookup failed for {track_label}: {exc}")
        if album_needs_replacement:
            return clear_album_metadata()
        return song, False

    if not match or not match.album:
        return handle_missing_match()

    new_album = (match.album or "").strip()
    if not new_album:
        return handle_missing_match()

    updated_fields = {}
    if not album_value or new_album.casefold() != album_value.casefold():
        updated_fields["album"] = new_album
        log(
            f"📝 album updated: {track_label} -> {new_album} "
            f"(source {match.source}, confidence {match.confidence:.2f})"
        )

    if missing_year and match.release_date:
        new_year = str(match.release_date.year)
        if new_year and new_year != year_value:
            updated_fields["year"] = new_year
            log(f"📝 year updated: {track_label} -> {new_year}")

    if not updated_fields:
        return song, False

    updated_song = song.model_copy(update=updated_fields)
    return updated_song, True


def _log_album_validation_result(
    song: Song,
    result,
    *,
    log: Callable[[str], None] = print,
) -> None:
    if not result.album:
        return
    if result.album_cleared:
        log(
            f"🧹 album removed after validation failure: {song.artist} - {song.title} ({result.album})"
        )
        return
    log(f"✅ album validated: {song.artist} - {song.title} ({result.album})")


def _save_playlist_state(
    playlist_file: pathlib.Path,
    playlist_name: str,
    songs: List[Song],
    playlist_df: pd.DataFrame,
    *,
    songs_to_remove: Optional[List[Song]] = None,
    save_validation_updates: bool = False,
    log: Callable[[str], None] = print,
) -> List[Song]:
    songs_to_remove = songs_to_remove or []
    removed_count = 0

    if songs_to_remove:
        original_song_count = len(songs)
        songs = [song for song in songs if song not in songs_to_remove]
        removed_count = original_song_count - len(songs)
        if removed_count > 0:
            log(f"🗑️ playlist rows removed: {removed_count}")

    if save_validation_updates or removed_count > 0:
        save_playlist_with_validation(playlist_file, songs, playlist_df, log=log)
        log("📝 playlist CSV updated")

    if removed_count > 0 and playlist_name.casefold() == "new releases":
        remove_new_releases_metadata_entries(playlist_file.parent, songs_to_remove)

    return songs


def main(
    station_slug: str,
    dry_run: bool = False,
    *,
    remote_sync: RemoteSyncRequest | None = None,
):  # dry_run flag
    global PLAYLISTS_PATH, STATION_PATH, STATION
    station_dir = station_dir_from_slug(station_slug)

    PLAYLISTS_PATH = station_dir / "playlists"
    STATION_PATH = station_dir / "songs"
    STATION = station_slug

    mode_label = "dry-run" if dry_run else "apply"
    print(f"🎛️ [sync] station={station_slug} mode={mode_label}")

    playlists_dir = pathlib.Path(PLAYLISTS_PATH)
    if not playlists_dir.exists():
        print(f"Playlists directory '{PLAYLISTS_PATH}' does not exist!")
        return

    # Get all CSV files from playlists directory
    playlist_files = list(playlists_dir.glob("*.csv"))
    if not playlist_files:
        print(f"No playlist files found in '{PLAYLISTS_PATH}' directory!")
        return

    # First pass: load playlists and collect deletion markers
    playlist_entries = []
    for playlist_file in playlist_files:
        # CHANGED: load_playlist now returns (songs, playlist_needs_save, deletions, df)
        songs, playlist_needs_save, deletions, playlist_df = load_playlist(
            playlist_file
        )
        playlist_entries.append(
            {
                "file": playlist_file,
                "name": playlist_file.stem,
                "songs": songs,
                "needs_save": playlist_needs_save,
                "deletions": deletions,
                "df": playlist_df,  # keep the full DataFrame for extra columns
            }
        )

    deletion_targets: Dict[Tuple[str, str], Song] = {}
    deletion_sources: Dict[Tuple[str, str], set] = {}
    for entry in playlist_entries:
        for song in entry["deletions"]:
            if not song.artist or not song.title:
                continue
            key = playlist_song_key(song)
            if key not in deletion_targets:
                deletion_targets[key] = song
            deletion_sources.setdefault(key, set()).add(entry["name"])

    if deletion_targets:
        print(f"🗑️ [sync] processing {len(deletion_targets)} [DEL] marker(s)")
        for key, song in deletion_targets.items():
            playlists_list = sorted(deletion_sources.get(key, []))
            playlists_note = ", ".join(playlists_list)
            print(f"  📝 delete request: {song.artist} - {song.title} ({playlists_note})")

        deleted_files = delete_marked_mp3_files(
            deletion_targets,
            STATION_PATH,
            log=lambda line: print(f"  {line}"),
        )
        if deleted_files:
            print(f"🗑️ [sync] deleted {deleted_files} MP3 file(s) due to [DEL] markers")

        for entry in playlist_entries:
            songs = entry["songs"]
            filtered_songs = [
                song
                for song in songs
                if playlist_song_key(song) not in deletion_targets
            ]
            removed_count = len(songs) - len(filtered_songs)
            if removed_count > 0:
                entry["songs"] = filtered_songs
                entry["needs_save"] = True
                entry["removed_via_marker"] = removed_count

            if entry["deletions"] and entry["name"].casefold() == "new releases":
                removed_metadata = remove_new_releases_metadata_entries(
                    entry["file"].parent, entry["deletions"]
                )
                if removed_metadata:
                    entry["metadata_removed"] = removed_metadata

    # Store all songs across playlists for repetition analysis
    all_songs_by_playlist = {}

    for entry in playlist_entries:
        playlist_file = entry["file"]
        playlist_name = entry["name"]
        playlist_log = PlaylistLog(playlist_name)

        songs = entry["songs"]
        playlist_needs_save = entry["needs_save"]
        removed_via_marker = entry.get("removed_via_marker", 0)

        if removed_via_marker:
            playlist_log.change(
                f"🗑️ removed {removed_via_marker} song(s) marked with [DEL] from playlist"
            )

        # Create directory for this playlist (needed for MP3 backfill)
        music_dir = pathlib.Path(STATION_PATH, playlist_name)
        music_dir.mkdir(parents=True, exist_ok=True)

        songs, library_changed, added_from_files = backfill_songs_from_library(
            playlist_name,
            songs,
            music_dir,
            log=playlist_log.change,
        )
        songs, normalized_changed, duplicates_removed = deduplicate_and_sort_songs(
            songs
        )

        if duplicates_removed > 0:
            playlist_log.change(f"🧹 removed {duplicates_removed} duplicate row(s)")

        # When updating playlist, update the DataFrame, not just the list of songs
        # For example, after deduplication, validation, or removal:
        # - Update the DataFrame rows for standard columns (artist, title, etc.)
        # - Keep all other columns unchanged

        # When saving:
        # save_playlist_with_validation should now take the DataFrame and write all columns
        if playlist_needs_save or library_changed or normalized_changed:
            save_playlist_with_validation(
                playlist_file,
                songs,
                entry["df"],
                log=playlist_log.change,
            )

        if not songs:
            playlist_log.warning("playlist is empty after cleanup")
            entry["songs"] = songs
            all_songs_by_playlist[playlist_name] = []
            continue

        entry["songs"] = songs

        # Separate songs into validated and unvalidated
        # Handle forced YouTube overrides before standard download detection
        override_candidates = []
        for song in songs:
            if not song.override_url:
                continue

            safe_artist = (
                sanitize_filename_component(song.artist) if song.artist else ""
            )
            safe_title = sanitize_filename_component(song.title) if song.title else ""
            override_path = (
                music_dir / f"{safe_artist} - {safe_title}.mp3"
                if safe_artist and safe_title
                else None
            )
            override_candidates.append((song, override_path))

        override_updates = False

        for song, song_path in override_candidates:
            url = song.override_url

            if not song.artist or not song.title:
                playlist_log.warning(f"override skipped; missing artist/title for URL {url}")
                continue

            if not url or not any(
                host in url.lower() for host in ("youtube.com", "youtu.be")
            ):
                playlist_log.warning(f"override skipped; unsupported URL {url}")
                continue

            if song_path is None:
                playlist_log.warning(
                    f"override skipped; could not determine target path for {song.artist} - {song.title}"
                )
                continue

            playlist_log.change(f"🔁 forced YouTube override: {song.artist} - {song.title}")

            if dry_run:
                playlist_log.info(
                    f"🧪 dry-run: would replace {song.artist} - {song.title} via override"
                )
                continue

            file_existed = song_path.exists()
            backup_path = None
            backup_created = False

            try:
                if file_existed:
                    backup_path = song_path.with_suffix(song_path.suffix + ".bak")
                    if backup_path.exists():
                        backup_path.unlink()
                    song_path.rename(backup_path)
                    backup_created = True

                _, download_lines = _capture_output(
                    lambda: youtube_to_mp3(url, str(song_path), use_search=False)
                )
                _emit_captured_lines(
                    download_lines,
                    logger=playlist_log.change,
                    include_plain=True,
                )
                _, tag_lines = _capture_output(
                    lambda: tag_mp3(
                        str(song_path),
                        song.artist,
                        song.title,
                        song.year,
                        playlist_name,
                        song.album,
                        log_prefix="      ",
                    )
                )
                _emit_captured_lines(tag_lines, logger=playlist_log.warning)

                if backup_path and backup_path.exists():
                    backup_path.unlink()

                song.override_url = None
                override_updates = True

                replacement_note = (
                    "🔁 override replaced existing file"
                    if file_existed
                    else "⬇️ override downloaded new file"
                )
                playlist_log.change(f"{replacement_note}: {song.artist} - {song.title}")

            except CalledProcessError as exc:
                playlist_log.error(
                    f"override failed; original retained for {song.artist} - {song.title}: {exc}"
                )

                if song_path.exists() and backup_created:
                    try:
                        song_path.unlink()
                    except Exception:
                        pass

                if backup_created and backup_path and backup_path.exists():
                    try:
                        backup_path.rename(song_path)
                    except Exception as restore_exc:
                        playlist_log.warning(
                            f"failed to restore original file from backup: {restore_exc}"
                        )

            except Exception as exc:
                playlist_log.error(
                    f"override failed; original retained for {song.artist} - {song.title}: {exc}"
                )

                if song_path.exists() and backup_created:
                    try:
                        song_path.unlink()
                    except Exception:
                        pass

                if backup_created and backup_path and backup_path.exists():
                    try:
                        backup_path.rename(song_path)
                    except Exception as restore_exc:
                        playlist_log.warning(
                            f"failed to restore original file from backup: {restore_exc}"
                        )

        if override_updates:
            save_playlist_with_validation(
                playlist_file,
                songs,
                entry["df"],
                log=playlist_log.change,
            )

        # Check which songs already exist and which need to be downloaded
        existing_songs = []
        missing_songs = []

        pending_overrides = 0

        for song in songs:
            artist = song.artist
            title = song.title
            year = song.year

            # Create safe filename
            safe_artist = sanitize_filename_component(artist)
            safe_title = sanitize_filename_component(title)
            song_path = music_dir / f"{safe_artist} - {safe_title}.mp3"

            if song.override_url:
                pending_overrides += 1
                if song_path.exists():
                    existing_songs.append((song, song_path))
                continue

            if song_path.exists():
                existing_songs.append((song, song_path))
            else:
                missing_songs.append((song, song_path))

        total_songs = len(songs)
        existing_count = len(existing_songs)
        missing_count = len(missing_songs)

        # In dry-run, audit and fix tags on existing files (set Album/others if missing/mismatched)
        if dry_run and existing_songs:
            refreshed = 0
            for song, song_path in existing_songs:
                track_label = (
                    f"{song.artist or 'Unknown Artist'} - "
                    f"{song.title or song_path.stem}"
                )
                status_lines: List[str] = []
                try:
                    audio = EasyID3(str(song_path))
                    cur_artist = (
                        audio.get("artist", [""])[0] if audio.get("artist") else ""
                    )
                    cur_title = (
                        audio.get("title", [""])[0] if audio.get("title") else ""
                    )
                    cur_year = audio.get("date", [""])[0] if audio.get("date") else ""
                    cur_genre = (
                        audio.get("genre", [""])[0] if audio.get("genre") else ""
                    )
                    cur_album = (
                        audio.get("album", [""])[0] if audio.get("album") else ""
                    )
                except Exception as e:
                    status_lines.append(
                        f"⚠️ Cannot read tags ({e}); rewriting metadata + album art"
                    )
                    _, tag_lines = _capture_output(
                        lambda: tag_mp3(
                            str(song_path),
                            song.artist,
                            song.title,
                            song.year,
                            playlist_name,
                            song.album,
                            log_prefix="      ",
                        )
                    )
                    _emit_captured_lines(tag_lines, logger=playlist_log.warning)
                    refreshed += 1
                    for line in status_lines:
                        playlist_log.change(f"{track_label}: {line}")
                    continue

                needs = []
                if cur_artist.strip() != song.artist.strip():
                    needs.append("artist")
                if cur_title.strip() != song.title.strip():
                    needs.append("title")
                year_value = normalize_year_value(song.year) or ""
                current_year_value = normalize_year_value(cur_year) or cur_year.strip()
                if year_value and year_value.casefold() != "unknown":
                    if current_year_value != year_value:
                        needs.append("year")
                if cur_genre.strip() != playlist_name:
                    needs.append("genre")
                if song.album and str(song.album).strip():
                    if cur_album.strip() != str(song.album).strip():
                        needs.append("album")
                # If album missing but provided in CSV
                elif cur_album.strip() and not (song.album and str(song.album).strip()):
                    # CSV has no album but file has one; do not erase it
                    pass

                update_needed = bool(needs)
                if update_needed:
                    status_lines.append(f"Updating fields: {', '.join(needs)}")
                    status_lines.append("Reapplying album art")
                    _, tag_lines = _capture_output(
                        lambda: tag_mp3(
                            str(song_path),
                            song.artist,
                            song.title,
                            song.year,
                            playlist_name,
                            song.album,
                            log_prefix="      ",
                        )
                    )
                    _emit_captured_lines(tag_lines, logger=playlist_log.warning)
                    refreshed += 1
                    for line in status_lines:
                        playlist_log.change(f"{track_label}: {line}")

            if refreshed > 0:
                playlist_log.change(f"🧪 dry-run retag audit would refresh {refreshed} file(s)")

        # Validate existing songs (only unvalidated ones)
        songs_to_remove_from_playlist = []
        validation_updates = False

        if existing_count > 0:
            unvalidated_existing = [
                (song, path) for song, path in existing_songs if not song.validated
            ]

            if unvalidated_existing:
                invalid_existing: List[Tuple[Song, pathlib.Path]] = []

                for song, song_path in unvalidated_existing:
                    result = perform_song_validation(song)
                    _log_album_validation_result(song, result, log=playlist_log.change)

                    if result.song:
                        replace_song_entry(songs, result.song)
                        validation_updates = True
                        playlist_log.change(
                            f"✅ validated existing track: {result.song.artist} - {result.song.title}"
                        )
                    else:
                        invalid_existing.append((song, song_path))
                        songs_to_remove_from_playlist.append(song)

                if invalid_existing:
                    for song, song_path in invalid_existing:
                        playlist_log.change(
                            f"🗑️ removed invalid existing track: {song.artist} - {song.title} ({song_path.name})"
                        )

                    # Delete invalid MP3 files
                    for song, song_path in invalid_existing:
                        try:
                            song_path.unlink()
                            playlist_log.change(f"🗑️ deleted invalid file: {song_path.name}")
                        except Exception as e:
                            playlist_log.error(f"failed to delete {song_path.name}: {e}")

        invalid_songs: List[Tuple[Song, pathlib.Path]] = []
        if missing_songs:
            available_missing: List[Tuple[Song, pathlib.Path]] = []
            for song, song_path in missing_songs:
                if verified(song.artist, song.title):
                    available_missing.append((song, song_path))
                else:
                    invalid_songs.append((song, song_path))
                    songs_to_remove_from_playlist.append(song)
                    playlist_log.change(
                        f"🗑️ removed unavailable track before download: {song.artist} - {song.title}"
                    )
            missing_songs = available_missing

        if missing_songs:
            updated_missing: List[Tuple[Song, pathlib.Path]] = []
            album_backfilled = 0

            for song, song_path in missing_songs:
                updated_song, album_changed = _backfill_album_for_missing_song(
                    song,
                    log=playlist_log.change,
                )
                if album_changed and updated_song.validated:
                    updated_song = updated_song.model_copy(update={"validated": False})
                if album_changed:
                    replace_song_entry(songs, updated_song)
                    validation_updates = True
                    album_backfilled += 1
                updated_missing.append((updated_song, song_path))

            missing_songs = updated_missing
            if album_backfilled:
                playlist_log.change(
                    f"📝 updated album metadata for {album_backfilled} pending download track(s)"
                )

        # Validate missing songs (ensure BOTH previously validated and newly validated get downloaded)
        # BUGFIX: Previously, if any unvalidated songs existed, already validated-but-missing songs
        # were skipped from downloads. We now always include them.
        pre_validated_missing = [
            (song, path) for song, path in missing_songs if song.validated
        ]
        unvalidated_missing = [
            (song, path) for song, path in missing_songs if not song.validated
        ]

        if unvalidated_missing:
            newly_validated: List[Tuple[Song, pathlib.Path]] = []

            for song, song_path in unvalidated_missing:
                result = perform_song_validation(song)
                _log_album_validation_result(song, result, log=playlist_log.change)

                if result.song:
                    replace_song_entry(songs, result.song)
                    newly_validated.append((result.song, song_path))
                    validation_updates = True
                    playlist_log.change(
                        f"✅ validated for download: {result.song.artist} - {result.song.title}"
                    )
                else:
                    invalid_songs.append((song, song_path))
                    playlist_log.change(
                        f"🗑️ removed invalid/unavailable track before download: {song.artist} - {song.title}"
                    )
                    songs_to_remove_from_playlist.append(song)

            # Combine already validated + newly validated
            valid_songs = pre_validated_missing + newly_validated
            valid_count = len(valid_songs)
        else:
            # No unvalidated songs; all missing songs are already validated
            valid_songs = pre_validated_missing  # all of them
            valid_count = len(valid_songs)

        # Save validation updates to playlist
        if validation_updates or songs_to_remove_from_playlist:
            songs = _save_playlist_state(
                playlist_file,
                playlist_name,
                songs,
                entry["df"],
                songs_to_remove=songs_to_remove_from_playlist,
                save_validation_updates=validation_updates,
                log=playlist_log.change,
            )

        # Downloads are skipped in dry-run mode
        if dry_run:
            if valid_count > 0:
                playlist_log.info(
                    f"🧪 dry-run: would download {valid_count} track(s); downloads skipped"
                )
            downloaded_count = 0
            failed_count = 0
        else:
            # Process only valid missing songs
            downloaded_count = 0
            failed_count = 0
            download_removals: List[Song] = []

            for idx, (song, song_path) in enumerate(valid_songs, start=1):
                artist = song.artist
                title = song.title
                year = song.year
                try:
                    playlist_log.change(
                        f"⬇️ download {idx}/{valid_count}: {artist} - {title}"
                    )
                    _, download_lines = _capture_output(
                        lambda: youtube_to_mp3(f"{artist} {title}", str(song_path))
                    )
                    _emit_captured_lines(
                        download_lines,
                        logger=playlist_log.change,
                        include_plain=True,
                    )
                    _, tag_lines = _capture_output(
                        lambda: tag_mp3(
                            str(song_path),
                            artist,
                            title,
                            year,
                            playlist_name,
                            song.album,
                            log_prefix="      ",
                        )
                    )
                    _emit_captured_lines(tag_lines, logger=playlist_log.warning)
                    playlist_log.change(f"✅ downloaded and tagged: {artist} - {title}")
                    downloaded_count += 1
                except DownloadNoResultsError as exc:
                    playlist_log.error(f"no yt-dlp search results for {artist} - {title}: {exc}")
                    songs_to_remove_from_playlist.append(song)
                    download_removals.append(song)
                    failed_count += 1
                    continue
                except DownloadOutputMissingError as exc:
                    playlist_log.error(
                        f"download completed without an MP3 for {artist} - {title}: {exc}"
                    )
                    failed_count += 1
                    continue
                except CalledProcessError as e:
                    playlist_log.error(f"failed to download {artist} - {title}: {e}")
                    failed_count += 1
                    continue

            if download_removals:
                songs = _save_playlist_state(
                    playlist_file,
                    playlist_name,
                    songs,
                    entry["df"],
                    songs_to_remove=download_removals,
                    log=playlist_log.change,
                )

        # Final summary
        total_removed = len(songs_to_remove_from_playlist)
        final_song_count = len(songs)
        changed_parts: List[str] = []
        if added_from_files > 0:
            changed_parts.append(f"library backfill +{added_from_files}")
        if duplicates_removed > 0:
            changed_parts.append(f"duplicates removed {duplicates_removed}")
        if total_removed > 0:
            changed_parts.append(f"playlist removals {total_removed}")
        if downloaded_count > 0:
            changed_parts.append(f"downloads {downloaded_count}")
        if failed_count > 0:
            changed_parts.append(f"download failures {failed_count}")
        if override_updates:
            changed_parts.append("override updates")
        if validation_updates:
            changed_parts.append("validation updates")
        if pending_overrides > 0 and not dry_run:
            changed_parts.append(f"pending overrides {pending_overrides}")
        if changed_parts:
            playlist_log.info(
                "📋 summary: "
                + ", ".join(changed_parts)
                + f" | songs {total_songs} -> {final_song_count}"
            )

        all_songs_by_playlist[playlist_name] = [
            Song(
                artist=song.artist,
                title=song.title,
                year=song.year,
                album=song.album,
                validated=song.validated,
            )
            for song in songs
        ]

    # Analyze cross-playlist repetition
    # REPLACED prints with log-to-file
    analysis_lines: List[str] = []

    def log(line: str = ""):
        analysis_lines.append(line)

    log("\n" + "=" * 60)
    log("📊 CROSS-PLAYLIST REPETITION ANALYSIS")
    log("=" * 60)

    # Create a dictionary to track songs and which playlists they appear in
    song_appearances = {}

    for playlist_name, playlist_songs in all_songs_by_playlist.items():
        for song in playlist_songs:
            # Use a more robust key that handles case and whitespace
            song_key = (song.artist.lower().strip(), song.title.lower().strip())
            if song_key not in song_appearances:
                song_appearances[song_key] = {"song": song, "playlists": []}
            song_appearances[song_key]["playlists"].append(playlist_name)

    # Find duplicates (songs appearing in more than one playlist)
    duplicates = {k: v for k, v in song_appearances.items() if len(v["playlists"]) > 1}

    total_songs = sum(
        len(playlist_songs) for playlist_songs in all_songs_by_playlist.values()
    )
    total_unique_songs = len(song_appearances)
    duplicate_songs = len(duplicates)
    unique_songs = total_unique_songs - duplicate_songs

    log(f"\n📈 Summary:")
    log(f"   Total songs across all playlists: {total_songs}")
    log(f"   Total unique songs across all playlists: {total_unique_songs}")
    log(f"   Songs appearing in multiple playlists: {duplicate_songs}")
    log(f"   Songs appearing in only one playlist: {unique_songs}")

    if duplicate_songs > 0:
        duplication_percentage = (duplicate_songs / total_unique_songs) * 100
        log(f"   Duplication rate: {duplication_percentage:.1f}%")

        log(f"\n🔄 Songs appearing in multiple playlists:")

        # Sort duplicates by number of appearances (descending)
        sorted_duplicates = sorted(
            duplicates.items(), key=lambda x: len(x[1]["playlists"]), reverse=True
        )

        for song_key, info in sorted_duplicates:
            song = info["song"]
            playlists = info["playlists"]
            playlist_count = len(playlists)

            log(f"\n   🎵 {song.artist} - {song.title} ({song.year})")
            log(f"      Appears in {playlist_count} playlists: {', '.join(playlists)}")

        # Show statistics by number of appearances
        appearance_counts = {}
        for info in duplicates.values():
            count = len(info["playlists"])
            appearance_counts[count] = appearance_counts.get(count, 0) + 1

        log(f"\n📊 Breakdown by number of appearances:")
        for count in sorted(appearance_counts.keys(), reverse=True):
            songs_count = appearance_counts[count]
            log(f"   {songs_count} song(s) appear in {count} playlists")
    else:
        log(f"\n✅ No duplicate songs found across playlists!")

    log("\n" + "=" * 60)

    # Write analysis to a station-scoped log file
    analysis_log_file = STATION_PATH.parent / "duplicate_analysis.log"
    with open(analysis_log_file, "w", encoding="utf-8") as f:
        f.write("\n".join(analysis_lines) + "\n")
    print(f"📝 [sync] cross-playlist analysis written to {analysis_log_file}")

    if remote_sync and remote_sync.enabled:
        print("🌐 [remote-sync] preparing rsync...")
        remote_sync_config = build_remote_sync_config(
            station_slug=station_slug,
            local_songs_root=pathlib.Path(STATION_PATH),
            dry_run=dry_run,
            remote_host=remote_sync.remote_host,
            remote_user=remote_sync.remote_user,
            remote_port=remote_sync.remote_port,
            remote_media_root=remote_sync.remote_media_root,
            remote_ssh_key=remote_sync.remote_ssh_key,
            remote_rsync_bin=remote_sync.remote_rsync_bin,
            remote_extra_rsync_args=remote_sync.remote_extra_rsync_args,
            delete_remote=remote_sync.delete_remote,
            timeout_seconds=remote_sync.timeout_seconds,
        )
        mode_label = "preview" if dry_run else "apply"
        print(
            f"🌐 [remote-sync] Running rsync ({mode_label}) from "
            f"{remote_sync_config.local_songs_root} to "
            f"{remote_sync_config.remote_host}:{remote_sync_config.remote_media_root}/"
        )
        remote_result = run_remote_sync(remote_sync_config)
        if remote_result.stdout.strip():
            print(remote_result.stdout.rstrip())
        if remote_result.stderr.strip():
            print(remote_result.stderr.rstrip())
        print(
            f"✅ [remote-sync] Completed: {remote_result.changed_count} changed item(s), "
            f"{remote_result.deleted_count} deletion(s)."
        )


def list_playlists(station_slug: str):
    """List all available playlists."""
    global PLAYLISTS_PATH, STATION_PATH
    station_dir = station_dir_from_slug(station_slug)
    PLAYLISTS_PATH = station_dir / "playlists"
    STATION_PATH = station_dir / "songs"

    playlists_dir = pathlib.Path(PLAYLISTS_PATH)
    if not playlists_dir.exists():
        print(f"Playlists directory '{PLAYLISTS_PATH}' does not exist!")
        return

    playlist_files = list(playlists_dir.glob("*.csv"))
    if not playlist_files:
        print(f"No playlist files found in '{PLAYLISTS_PATH}' directory!")
        return

    print("Available playlists:")
    for idx, playlist_file in enumerate(playlist_files):
        playlist_name = playlist_file.stem
        songs, _, _, _ = load_playlist(playlist_file)
        print(f"{idx}: {playlist_name} ({len(songs)} songs)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="AI-assisted local-network radio pipeline."
    )
    parser.add_argument(
        "-s",
        "--station",
        type=str,
        choices=ALLOWED_STATION_SLUGS,
        default=DEFAULT_STATION_SLUG,
        help="Station slug (default: %(default)s).",
    )
    # dry-run flag
    parser.add_argument(
        "-n",
        "--dry-run",
        action="store_true",
        help="Dry run: validate and re-tag existing MP3s, but skip new downloads.",
    )
    add_remote_sync_args(parser)
    args = parser.parse_args()

    main(
        args.station,
        args.dry_run,
        remote_sync=remote_sync_request_from_args(args),
    )  # pass dry-run flag
