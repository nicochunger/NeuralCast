"""Shared models, protocols, and logging for station playlist synchronization."""

from __future__ import annotations

import pathlib
from dataclasses import dataclass
from typing import Callable, Protocol

from neuralcast.models import Song, ValidationResult
from neuralcast.playlists.catalog import PlaylistSnapshot


@dataclass(frozen=True)
class SyncRequest:
    station_slug: str
    dry_run: bool = False


@dataclass(frozen=True)
class PlaylistSyncReport:
    name: str
    initial_song_count: int
    final_song_count: int
    added_from_files: int = 0
    duplicates_removed: int = 0
    removed_count: int = 0
    downloaded_count: int = 0
    failed_count: int = 0
    validation_updated: bool = False
    override_updated: bool = False
    pending_overrides: int = 0
    planned_download_count: int = 0
    tag_repair_count: int = 0


@dataclass(frozen=True)
class SyncReport:
    station_slug: str
    dry_run: bool
    playlist_reports: list[PlaylistSyncReport]
    duplicate_analysis_log: pathlib.Path


class TrackResolver(Protocol):
    def is_available(self, song: Song) -> bool:
        ...

    def validate_song(self, song: Song) -> ValidationResult:
        ...

    def backfill_album(
        self,
        song: Song,
        *,
        log: Callable[[str], None] = print,
    ) -> tuple[Song, bool]:
        ...


class MediaLibrary(Protocol):
    def apply_override(
        self,
        song: Song,
        song_path: pathlib.Path | None,
        playlist_name: str,
        *,
        dry_run: bool,
        log: PlaylistLog,
    ) -> bool:
        ...

    def audit_existing_tags(
        self,
        existing_songs: list[tuple[Song, pathlib.Path]],
        playlist_name: str,
        *,
        repair: bool,
        log: PlaylistLog,
    ) -> int:
        ...

    def download_song(
        self,
        song: Song,
        song_path: pathlib.Path,
        playlist_name: str,
        *,
        log: PlaylistLog,
    ) -> None:
        ...

    def delete_file(
        self,
        song_path: pathlib.Path,
        *,
        log: PlaylistLog,
    ) -> None:
        ...


@dataclass
class _PlaylistEntry:
    snapshot: PlaylistSnapshot
    songs: list[Song]
    needs_save: bool
    deletions: list[Song]
    removed_via_marker: int = 0

    @property
    def file(self) -> pathlib.Path:
        return self.snapshot.path

    @property
    def name(self) -> str:
        return self.snapshot.name


@dataclass(frozen=True)
class _SongLocation:
    song: Song
    path: pathlib.Path


@dataclass(frozen=True)
class _PlaylistActions:
    existing_songs: list[_SongLocation]
    missing_songs: list[_SongLocation]
    pending_overrides: int


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


__all__ = [
    "MediaLibrary",
    "PlaylistLog",
    "PlaylistSyncReport",
    "SyncReport",
    "SyncRequest",
    "TrackResolver",
]
