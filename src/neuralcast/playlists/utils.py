"""Playlist parsing and library management helpers."""
from __future__ import annotations

import pathlib
import re
from typing import Callable, Dict, List, Optional, Tuple

import pandas as pd
from mutagen.easyid3 import EasyID3

from neuralcast.models import Song

DELETE_MARKER = "[DEL]"
_FLOAT_YEAR_PATTERN = re.compile(r"^(\d{4})\.0+$")
_ZEROED_DATE_YEAR_PATTERN = re.compile(r"^(\d{4})-00(?:-00)?$")


def _normalize_csv_value(value: object) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text or text.lower() == "nan":
            return None
        return text
    if pd.isna(value):
        return None
    text = str(value).strip()
    if not text or text.lower() == "nan":
        return None
    return text


def normalize_year_value(value: object) -> Optional[str]:
    text = _normalize_csv_value(value)
    if text is None:
        return None
    if text.casefold() == "unknown":
        return "Unknown"

    float_match = _FLOAT_YEAR_PATTERN.fullmatch(text)
    if float_match:
        return float_match.group(1)

    zeroed_date_match = _ZEROED_DATE_YEAR_PATTERN.fullmatch(text)
    if zeroed_date_match:
        return zeroed_date_match.group(1)

    return text


def sanitize_filename_component(value: str) -> str:
    return value.replace("/", " ").replace("\\", " ").strip()


def playlist_song_key(song: Song) -> Tuple[str, str]:
    return (song.artist.lower().strip(), song.title.lower().strip())


def load_playlist(
    playlist_path: pathlib.Path,
) -> Tuple[List[Song], bool, List[Song], pd.DataFrame]:
    from .catalog import StationPlaylistCatalog

    snapshot = StationPlaylistCatalog(playlist_path.parent).load(playlist_path)
    return (
        snapshot.songs,
        snapshot.needs_persist,
        snapshot.deletion_requests,
        snapshot._copy_source_frame(),
    )


def backfill_songs_from_library(
    playlist_name: str,
    songs: List[Song],
    music_dir: Optional[pathlib.Path],
    *,
    log: Callable[[str], None] = print,
) -> Tuple[List[Song], bool, int]:
    if not music_dir:
        log("Warning: STATION_PATH is not set; skipping MP3 file check")
        return songs, False, 0

    if not music_dir.exists():
        log(
            f"Warning: Music directory '{music_dir}' does not exist, skipping MP3 file check"
        )
        return songs, False, 0

    songs_by_key: Dict[Tuple[str, str], Song] = {
        playlist_song_key(song): song for song in songs
    }
    updated_songs = list(songs)
    added_from_files = 0
    changes = False

    for mp3_file in music_dir.glob("*.mp3"):
        try:
            audio = EasyID3(str(mp3_file))
            file_artist = audio.get("artist", [""])[0] if audio.get("artist") else ""
            file_title = audio.get("title", [""])[0] if audio.get("title") else ""
            file_year = audio.get("date", [""])[0] if audio.get("date") else ""
            file_album = audio.get("album", [""])[0] if audio.get("album") else ""
        except Exception as exc:
            log(f"Warning: Could not read metadata from {mp3_file}: {exc}")
            continue

        if not file_artist or not file_title:
            filename = mp3_file.stem
            if " - " in filename:
                parts = filename.split(" - ", 1)
                file_artist = file_artist or parts[0].strip()
                file_title = file_title or parts[1].strip()

        if not file_artist or not file_title:
            continue

        key = (file_artist.lower().strip(), file_title.lower().strip())
        if key in songs_by_key:
            continue

        safe_artist = sanitize_filename_component(file_artist)
        safe_title = sanitize_filename_component(file_title)
        expected_name = f"{safe_artist} - {safe_title}.mp3"
        target_path = mp3_file.with_name(expected_name)
        if mp3_file.name != expected_name:
            try:
                if target_path.exists():
                    log(
                        f"Warning: Target exists, cannot rename {mp3_file.name} -> {expected_name}"
                    )
                else:
                    mp3_file.rename(target_path)
                    mp3_file = target_path
                    log(f"Renamed file: {target_path.name}")
                    changes = True
            except Exception as exc:
                log(f"Warning: Could not rename {mp3_file.name} -> {expected_name}: {exc}")

        year_to_use = normalize_year_value(file_year) or "Unknown"
        new_song = Song(
            artist=file_artist,
            title=file_title,
            year=year_to_use,
            album=file_album or None,
            validated=False,
        )
        updated_songs.append(new_song)
        songs_by_key[key] = new_song
        added_from_files += 1
        changes = True
        log(f"Added from existing file: {file_artist} - {file_title}")

    if added_from_files > 0:
        log(f"Added {added_from_files} song(s) from existing MP3 files")

    return updated_songs, changes, added_from_files


def deduplicate_and_sort_songs(songs: List[Song]) -> Tuple[List[Song], bool, int]:
    seen: Dict[Tuple[str, str], Song] = {}
    ordered_unique: List[Song] = []
    for song in songs:
        key = playlist_song_key(song)
        if key not in seen:
            seen[key] = song
            ordered_unique.append(song)

    duplicates_removed = len(songs) - len(ordered_unique)
    sorted_songs = sorted(
        ordered_unique, key=lambda s: (s.artist.lower().strip(), s.title.lower().strip())
    )
    changed = duplicates_removed > 0 or sorted_songs != songs
    return sorted_songs, changed, duplicates_removed


def replace_song_entry(songs: List[Song], updated_song: Song) -> None:
    target_key = playlist_song_key(updated_song)
    for idx, existing in enumerate(songs):
        if playlist_song_key(existing) == target_key:
            songs[idx] = updated_song
            return


def save_playlist_with_validation(
    playlist_path: pathlib.Path,
    songs: List[Song],
    df: pd.DataFrame,
    *,
    log: Callable[[str], None] = print,
):
    from .catalog import StationPlaylistCatalog

    StationPlaylistCatalog.save_legacy_frame(
        playlist_path,
        songs,
        df,
        log=log,
    )


def delete_marked_mp3_files(
    delete_targets: Dict[Tuple[str, str], Song],
    songs_root: pathlib.Path,
    *,
    log: Callable[[str], None] = print,
) -> int:
    if not delete_targets:
        return 0

    if songs_root is None or not songs_root.exists():
        log("Warning: Songs directory does not exist; cannot delete marked MP3 files")
        return 0

    removed = 0
    for playlist_dir in songs_root.iterdir():
        if not playlist_dir.is_dir():
            continue

        for song in delete_targets.values():
            safe_artist = sanitize_filename_component(song.artist)
            safe_title = sanitize_filename_component(song.title)
            target_file = playlist_dir / f"{safe_artist} - {safe_title}.mp3"
            if not target_file.exists():
                continue

            try:
                target_file.unlink()
                removed += 1
                try:
                    relative_path = target_file.relative_to(songs_root)
                except ValueError:
                    relative_path = target_file
                log(f"🗑️ Deleted MP3 due to [DEL]: {relative_path}")
            except Exception as exc:
                log(f"❌ Failed to delete MP3 {target_file}: {exc}")

    return removed


__all__ = [
    "DELETE_MARKER",
    "load_playlist",
    "backfill_songs_from_library",
    "deduplicate_and_sort_songs",
    "replace_song_entry",
    "save_playlist_with_validation",
    "delete_marked_mp3_files",
    "sanitize_filename_component",
    "playlist_song_key",
    "normalize_year_value",
]
