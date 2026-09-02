"""MP3 library reconciliation and deletion helpers for station playlists."""
from __future__ import annotations

import pathlib
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

from mutagen.easyid3 import EasyID3

from neuralcast.models import Song

from .catalog import normalize_year_value, playlist_song_key


def sanitize_filename_component(value: str) -> str:
    return value.replace("/", " ").replace("\\", " ").strip()


@dataclass(frozen=True)
class LibraryRenameAction:
    source: pathlib.Path
    target: pathlib.Path
    song_key: Tuple[str, str]


@dataclass(frozen=True)
class LibraryBackfillPlan:
    songs: List[Song]
    changed: bool
    added_from_files: int
    existing_paths: Dict[Tuple[str, str], pathlib.Path]
    renames: List[LibraryRenameAction]


def plan_songs_from_library(
    playlist_name: str,
    songs: List[Song],
    music_dir: Optional[pathlib.Path],
    *,
    ignored_paths: set[pathlib.Path] | None = None,
    log: Callable[[str], None] = print,
) -> LibraryBackfillPlan:
    if not music_dir:
        log("Warning: STATION_PATH is not set; skipping MP3 file check")
        return LibraryBackfillPlan(list(songs), False, 0, {}, [])

    if not music_dir.exists():
        log(
            f"Warning: Music directory '{music_dir}' does not exist, "
            "skipping MP3 file check"
        )
        return LibraryBackfillPlan(list(songs), False, 0, {}, [])

    songs_by_key: Dict[Tuple[str, str], Song] = {
        playlist_song_key(song): song for song in songs
    }
    updated_songs = list(songs)
    added_from_files = 0
    changes = False
    existing_paths: Dict[Tuple[str, str], pathlib.Path] = {}
    renames: List[LibraryRenameAction] = []
    ignored_paths = ignored_paths or set()

    for mp3_file in music_dir.glob("*.mp3"):
        if mp3_file in ignored_paths:
            continue
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
        existing_paths.setdefault(key, mp3_file)
        if key in songs_by_key:
            continue

        safe_artist = sanitize_filename_component(file_artist)
        safe_title = sanitize_filename_component(file_title)
        expected_name = f"{safe_artist} - {safe_title}.mp3"
        target_path = mp3_file.with_name(expected_name)
        if mp3_file.name != expected_name:
            if target_path.exists():
                log(
                    f"Warning: Target exists, cannot rename "
                    f"{mp3_file.name} -> {expected_name}"
                )
                existing_paths[key] = target_path
            else:
                renames.append(
                    LibraryRenameAction(
                        source=mp3_file,
                        target=target_path,
                        song_key=key,
                    )
                )
                changes = True

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

    return LibraryBackfillPlan(
        songs=updated_songs,
        changed=changes,
        added_from_files=added_from_files,
        existing_paths=existing_paths,
        renames=renames,
    )


def apply_library_renames(
    plan: LibraryBackfillPlan,
    *,
    log: Callable[[str], None] = print,
) -> Dict[Tuple[str, str], pathlib.Path]:
    existing_paths = dict(plan.existing_paths)
    for action in plan.renames:
        try:
            if action.target.exists():
                log(
                    f"Warning: Target exists, cannot rename "
                    f"{action.source.name} -> {action.target.name}"
                )
                existing_paths[action.song_key] = action.target
                continue
            action.source.rename(action.target)
            existing_paths[action.song_key] = action.target
            log(f"Renamed file: {action.target.name}")
        except Exception as exc:
            log(
                f"Warning: Could not rename "
                f"{action.source.name} -> {action.target.name}: {exc}"
            )
    return existing_paths


def delete_marked_mp3_files(
    delete_targets: Dict[Tuple[str, str], Song],
    songs_root: pathlib.Path,
    *,
    log: Callable[[str], None] = print,
) -> int:
    target_files = find_marked_mp3_files(delete_targets, songs_root, log=log)
    removed = 0
    for target_file in target_files:
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


def find_marked_mp3_files(
    delete_targets: Dict[Tuple[str, str], Song],
    songs_root: pathlib.Path,
    *,
    log: Callable[[str], None] = print,
) -> List[pathlib.Path]:
    if not delete_targets:
        return []

    if songs_root is None or not songs_root.exists():
        log("Warning: Songs directory does not exist; cannot locate marked MP3 files")
        return []

    target_files: List[pathlib.Path] = []
    for playlist_dir in songs_root.iterdir():
        if not playlist_dir.is_dir():
            continue
        for song in delete_targets.values():
            safe_artist = sanitize_filename_component(song.artist)
            safe_title = sanitize_filename_component(song.title)
            target_file = playlist_dir / f"{safe_artist} - {safe_title}.mp3"
            if target_file.exists():
                target_files.append(target_file)
    return target_files


__all__ = [
    "LibraryBackfillPlan",
    "LibraryRenameAction",
    "plan_songs_from_library",
    "apply_library_renames",
    "delete_marked_mp3_files",
    "find_marked_mp3_files",
    "sanitize_filename_component",
]
