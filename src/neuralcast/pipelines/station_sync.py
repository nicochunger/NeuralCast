"""Station sync service and compatibility helpers."""

from __future__ import annotations

import contextlib
import io
import pathlib
from dataclasses import dataclass
from subprocess import CalledProcessError
from typing import Callable, Dict, List, Optional, Protocol, Tuple

import pandas as pd
from mutagen.easyid3 import EasyID3

from neuralcast.config import station_dir_from_slug
from neuralcast.audio.download import (
    DownloadNoResultsError,
    DownloadOutputMissingError,
    tag_mp3,
    youtube_to_mp3,
)
from neuralcast.metadata.album_lookup import guess_album
from neuralcast.metadata.storage import (
    load_station_entry_mapping,
    metadata_key,
    normalize_metadata_component,
    save_station_entry_mapping,
)
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
from neuralcast.models import ValidationResult
from neuralcast.services.validation import (
    perform_song_validation,
    verified,
    verified_album,
)
from neuralcast.pipelines.media_sync import (
    RemoteSyncRequest,
    build_remote_sync_config,
    RemoteSyncResult,
    run_remote_sync,
)
_METADATA_FILENAME = "New Releases.metadata.json"


@dataclass(frozen=True)
class SyncRequest:
    station_slug: str
    dry_run: bool = False
    remote_sync: RemoteSyncRequest | None = None


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


@dataclass(frozen=True)
class SyncReport:
    station_slug: str
    dry_run: bool
    playlist_reports: list[PlaylistSyncReport]
    duplicate_analysis_log: pathlib.Path
    remote_sync_result: RemoteSyncResult | None = None


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
    file: pathlib.Path
    name: str
    songs: list[Song]
    needs_save: bool
    deletions: list[Song]
    df: pd.DataFrame
    removed_via_marker: int = 0


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


def remove_new_releases_metadata_entries(
    playlists_dir: pathlib.Path, songs_to_remove: List[Song]
) -> int:
    if not songs_to_remove:
        return 0
    entries, resolved = load_station_entry_mapping(
        playlists_dir,
        _METADATA_FILENAME,
        log_warning=lambda message: print(f"⚠️ {message}"),
        log_info=lambda message: print(f"ℹ️ {message}"),
        warning_label="metadata file",
    )
    metadata_path = resolved.read_path if resolved.read_path.exists() else resolved.write_path
    if not metadata_path.exists():
        print(
            f"⚠️ Metadata file not found at {metadata_path}; skipping metadata cleanup for New Releases"
        )
        return 0

    def normalize_component(value: Optional[str]) -> str:
        return normalize_metadata_component(value)

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

        primary_key = metadata_key(
            artist_component,
            title_component,
            album_component,
            year_component,
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
        try:
            write_path = save_station_entry_mapping(
                playlists_dir,
                _METADATA_FILENAME,
                entries,
            )
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


class DefaultTrackResolver:
    def is_available(self, song: Song) -> bool:
        return verified(song.artist, song.title)

    def validate_song(self, song: Song) -> ValidationResult:
        return perform_song_validation(song)

    def backfill_album(
        self,
        song: Song,
        *,
        log: Callable[[str], None] = print,
    ) -> tuple[Song, bool]:
        return _backfill_album_for_missing_song(song, log=log)


class DefaultMediaLibrary:
    def apply_override(
        self,
        song: Song,
        song_path: pathlib.Path | None,
        playlist_name: str,
        *,
        dry_run: bool,
        log: PlaylistLog,
    ) -> bool:
        url = song.override_url
        if not song.artist or not song.title:
            log.warning(f"override skipped; missing artist/title for URL {url}")
            return False

        if not url or not any(host in url.lower() for host in ("youtube.com", "youtu.be")):
            log.warning(f"override skipped; unsupported URL {url}")
            return False

        if song_path is None:
            log.warning(
                f"override skipped; could not determine target path for {song.artist} - {song.title}"
            )
            return False

        log.change(f"🔁 forced YouTube override: {song.artist} - {song.title}")
        if dry_run:
            log.info(f"🧪 dry-run: would replace {song.artist} - {song.title} via override")
            return False

        file_existed = song_path.exists()
        backup_path: pathlib.Path | None = None
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
                logger=log.change,
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
            _emit_captured_lines(tag_lines, logger=log.warning)

            if backup_path and backup_path.exists():
                backup_path.unlink()

            song.override_url = None
            replacement_note = (
                "🔁 override replaced existing file"
                if file_existed
                else "⬇️ override downloaded new file"
            )
            log.change(f"{replacement_note}: {song.artist} - {song.title}")
            return True
        except (CalledProcessError, Exception) as exc:
            log.error(
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
                    log.warning(
                        f"failed to restore original file from backup: {restore_exc}"
                    )
            return False

    def audit_existing_tags(
        self,
        existing_songs: list[tuple[Song, pathlib.Path]],
        playlist_name: str,
        *,
        log: PlaylistLog,
    ) -> int:
        refreshed = 0
        for song, song_path in existing_songs:
            track_label = f"{song.artist or 'Unknown Artist'} - {song.title or song_path.stem}"
            status_lines: List[str] = []
            try:
                audio = EasyID3(str(song_path))
                cur_artist = audio.get("artist", [""])[0] if audio.get("artist") else ""
                cur_title = audio.get("title", [""])[0] if audio.get("title") else ""
                cur_year = audio.get("date", [""])[0] if audio.get("date") else ""
                cur_genre = audio.get("genre", [""])[0] if audio.get("genre") else ""
                cur_album = audio.get("album", [""])[0] if audio.get("album") else ""
            except Exception as exc:
                status_lines.append(
                    f"⚠️ Cannot read tags ({exc}); rewriting metadata + album art"
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
                _emit_captured_lines(tag_lines, logger=log.warning)
                refreshed += 1
                for line in status_lines:
                    log.change(f"{track_label}: {line}")
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

            if not needs:
                continue

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
            _emit_captured_lines(tag_lines, logger=log.warning)
            refreshed += 1
            for line in status_lines:
                log.change(f"{track_label}: {line}")

        return refreshed

    def download_song(
        self,
        song: Song,
        song_path: pathlib.Path,
        playlist_name: str,
        *,
        log: PlaylistLog,
    ) -> None:
        _, download_lines = _capture_output(
            lambda: youtube_to_mp3(f"{song.artist} {song.title}", str(song_path))
        )
        _emit_captured_lines(
            download_lines,
            logger=log.change,
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
        _emit_captured_lines(tag_lines, logger=log.warning)

    def delete_file(
        self,
        song_path: pathlib.Path,
        *,
        log: PlaylistLog,
    ) -> None:
        try:
            song_path.unlink()
            log.change(f"🗑️ deleted invalid file: {song_path.name}")
        except Exception as exc:
            log.error(f"failed to delete {song_path.name}: {exc}")


class StationSync:
    def __init__(
        self,
        *,
        resolver: TrackResolver | None = None,
        media_library: MediaLibrary | None = None,
        station_dir_resolver: Callable[[str], pathlib.Path] = station_dir_from_slug,
    ) -> None:
        self._resolver = resolver or DefaultTrackResolver()
        self._media_library = media_library or DefaultMediaLibrary()
        self._station_dir_resolver = station_dir_resolver

    def run(self, request: SyncRequest) -> SyncReport:
        station_dir = self._station_dir_resolver(request.station_slug)
        playlists_dir = station_dir / "playlists"
        songs_root = station_dir / "songs"

        mode_label = "dry-run" if request.dry_run else "apply"
        print(f"🎛️ [sync] station={request.station_slug} mode={mode_label}")

        if not playlists_dir.exists():
            print(f"Playlists directory '{playlists_dir}' does not exist!")
            return SyncReport(
                station_slug=request.station_slug,
                dry_run=request.dry_run,
                playlist_reports=[],
                duplicate_analysis_log=station_dir / "duplicate_analysis.log",
            )

        playlist_files = list(playlists_dir.glob("*.csv"))
        if not playlist_files:
            print(f"No playlist files found in '{playlists_dir}' directory!")
            return SyncReport(
                station_slug=request.station_slug,
                dry_run=request.dry_run,
                playlist_reports=[],
                duplicate_analysis_log=station_dir / "duplicate_analysis.log",
            )

        playlist_entries = self._load_playlist_entries(playlist_files)
        self._apply_deletion_markers(playlist_entries, songs_root)

        playlist_reports: list[PlaylistSyncReport] = []
        all_songs_by_playlist: dict[str, list[Song]] = {}

        for entry in playlist_entries:
            report, songs = self._sync_playlist(entry, songs_root, dry_run=request.dry_run)
            playlist_reports.append(report)
            all_songs_by_playlist[entry.name] = songs

        analysis_log_file = self._write_duplicate_analysis(
            station_dir / "duplicate_analysis.log",
            all_songs_by_playlist,
        )
        print(f"📝 [sync] cross-playlist analysis written to {analysis_log_file}")

        remote_result = self._run_remote_sync(
            request=request,
            songs_root=songs_root,
        )
        return SyncReport(
            station_slug=request.station_slug,
            dry_run=request.dry_run,
            playlist_reports=playlist_reports,
            duplicate_analysis_log=analysis_log_file,
            remote_sync_result=remote_result,
        )

    def _load_playlist_entries(
        self,
        playlist_files: list[pathlib.Path],
    ) -> list[_PlaylistEntry]:
        entries: list[_PlaylistEntry] = []
        for playlist_file in playlist_files:
            songs, playlist_needs_save, deletions, playlist_df = load_playlist(playlist_file)
            entries.append(
                _PlaylistEntry(
                    file=playlist_file,
                    name=playlist_file.stem,
                    songs=songs,
                    needs_save=playlist_needs_save,
                    deletions=deletions,
                    df=playlist_df,
                )
            )
        return entries

    def _apply_deletion_markers(
        self,
        playlist_entries: list[_PlaylistEntry],
        songs_root: pathlib.Path,
    ) -> None:
        deletion_targets: Dict[Tuple[str, str], Song] = {}
        deletion_sources: Dict[Tuple[str, str], set[str]] = {}

        for entry in playlist_entries:
            for song in entry.deletions:
                if not song.artist or not song.title:
                    continue
                key = playlist_song_key(song)
                if key not in deletion_targets:
                    deletion_targets[key] = song
                deletion_sources.setdefault(key, set()).add(entry.name)

        if not deletion_targets:
            return

        print(f"🗑️ [sync] processing {len(deletion_targets)} [DEL] marker(s)")
        for key, song in deletion_targets.items():
            playlists_list = sorted(deletion_sources.get(key, []))
            playlists_note = ", ".join(playlists_list)
            print(f"  📝 delete request: {song.artist} - {song.title} ({playlists_note})")

        deleted_files = delete_marked_mp3_files(
            deletion_targets,
            songs_root,
            log=lambda line: print(f"  {line}"),
        )
        if deleted_files:
            print(f"🗑️ [sync] deleted {deleted_files} MP3 file(s) due to [DEL] markers")

        for entry in playlist_entries:
            filtered_songs = [
                song
                for song in entry.songs
                if playlist_song_key(song) not in deletion_targets
            ]
            removed_count = len(entry.songs) - len(filtered_songs)
            if removed_count > 0:
                entry.songs = filtered_songs
                entry.needs_save = True
                entry.removed_via_marker = removed_count

            if entry.deletions and entry.name.casefold() == "new releases":
                remove_new_releases_metadata_entries(entry.file.parent, entry.deletions)

    def _sync_playlist(
        self,
        entry: _PlaylistEntry,
        songs_root: pathlib.Path,
        *,
        dry_run: bool,
    ) -> tuple[PlaylistSyncReport, list[Song]]:
        playlist_log = PlaylistLog(entry.name)
        songs = entry.songs
        if entry.removed_via_marker:
            playlist_log.change(
                f"🗑️ removed {entry.removed_via_marker} song(s) marked with [DEL] from playlist"
            )

        music_dir = songs_root / entry.name
        music_dir.mkdir(parents=True, exist_ok=True)

        songs, library_changed, added_from_files = backfill_songs_from_library(
            entry.name,
            songs,
            music_dir,
            log=playlist_log.change,
        )
        songs, normalized_changed, duplicates_removed = deduplicate_and_sort_songs(songs)
        if duplicates_removed > 0:
            playlist_log.change(f"🧹 removed {duplicates_removed} duplicate row(s)")

        if entry.needs_save or library_changed or normalized_changed:
            save_playlist_with_validation(
                entry.file,
                songs,
                entry.df,
                log=playlist_log.change,
            )

        if not songs:
            playlist_log.warning("playlist is empty after cleanup")
            return (
                PlaylistSyncReport(
                    name=entry.name,
                    initial_song_count=0,
                    final_song_count=0,
                    added_from_files=added_from_files,
                    duplicates_removed=duplicates_removed,
                    removed_count=entry.removed_via_marker,
                ),
                [],
            )

        initial_song_count = len(songs)
        override_updates = False
        for song, song_path in self._override_candidates(songs, music_dir):
            if self._media_library.apply_override(
                song,
                song_path,
                entry.name,
                dry_run=dry_run,
                log=playlist_log,
            ):
                override_updates = True

        if override_updates:
            save_playlist_with_validation(
                entry.file,
                songs,
                entry.df,
                log=playlist_log.change,
            )

        actions = self._build_playlist_actions(songs, music_dir)
        existing_pairs = [(item.song, item.path) for item in actions.existing_songs]
        if dry_run and existing_pairs:
            refreshed = self._media_library.audit_existing_tags(
                existing_pairs,
                entry.name,
                log=playlist_log,
            )
            if refreshed > 0:
                playlist_log.change(
                    f"🧪 dry-run retag audit would refresh {refreshed} file(s)"
                )

        songs_to_remove_from_playlist: list[Song] = []
        validation_updates = False

        unvalidated_existing = [
            item for item in actions.existing_songs if not item.song.validated
        ]
        invalid_existing: list[_SongLocation] = []
        for item in unvalidated_existing:
            result = self._resolver.validate_song(item.song)
            _log_album_validation_result(item.song, result, log=playlist_log.change)
            if result.song:
                replace_song_entry(songs, result.song)
                validation_updates = True
                playlist_log.change(
                    f"✅ validated existing track: {result.song.artist} - {result.song.title}"
                )
            else:
                invalid_existing.append(item)
                songs_to_remove_from_playlist.append(item.song)

        for item in invalid_existing:
            playlist_log.change(
                f"🗑️ removed invalid existing track: {item.song.artist} - {item.song.title} ({item.path.name})"
            )
            self._media_library.delete_file(item.path, log=playlist_log)

        available_missing: list[_SongLocation] = []
        for item in actions.missing_songs:
            if self._resolver.is_available(item.song):
                available_missing.append(item)
            else:
                songs_to_remove_from_playlist.append(item.song)
                playlist_log.change(
                    f"🗑️ removed unavailable track before download: {item.song.artist} - {item.song.title}"
                )

        updated_missing: list[_SongLocation] = []
        album_backfilled = 0
        for item in available_missing:
            updated_song, album_changed = self._resolver.backfill_album(
                item.song,
                log=playlist_log.change,
            )
            if album_changed and updated_song.validated:
                updated_song = updated_song.model_copy(update={"validated": False})
            if album_changed:
                replace_song_entry(songs, updated_song)
                validation_updates = True
                album_backfilled += 1
            updated_missing.append(_SongLocation(updated_song, item.path))

        if album_backfilled:
            playlist_log.change(
                f"📝 updated album metadata for {album_backfilled} pending download track(s)"
            )

        pre_validated_missing = [item for item in updated_missing if item.song.validated]
        unvalidated_missing = [item for item in updated_missing if not item.song.validated]

        newly_validated: list[_SongLocation] = []
        for item in unvalidated_missing:
            result = self._resolver.validate_song(item.song)
            _log_album_validation_result(item.song, result, log=playlist_log.change)
            if result.song:
                replace_song_entry(songs, result.song)
                newly_validated.append(_SongLocation(result.song, item.path))
                validation_updates = True
                playlist_log.change(
                    f"✅ validated for download: {result.song.artist} - {result.song.title}"
                )
            else:
                songs_to_remove_from_playlist.append(item.song)
                playlist_log.change(
                    f"🗑️ removed invalid/unavailable track before download: {item.song.artist} - {item.song.title}"
                )

        valid_songs = pre_validated_missing + newly_validated
        valid_count = len(valid_songs)

        if validation_updates or songs_to_remove_from_playlist:
            songs = _save_playlist_state(
                entry.file,
                entry.name,
                songs,
                entry.df,
                songs_to_remove=songs_to_remove_from_playlist,
                save_validation_updates=validation_updates,
                log=playlist_log.change,
            )

        downloaded_count = 0
        failed_count = 0
        if dry_run:
            if valid_count > 0:
                playlist_log.info(
                    f"🧪 dry-run: would download {valid_count} track(s); downloads skipped"
                )
        else:
            download_removals: list[Song] = []
            for idx, item in enumerate(valid_songs, start=1):
                try:
                    playlist_log.change(
                        f"⬇️ download {idx}/{valid_count}: {item.song.artist} - {item.song.title}"
                    )
                    self._media_library.download_song(
                        item.song,
                        item.path,
                        entry.name,
                        log=playlist_log,
                    )
                    playlist_log.change(
                        f"✅ downloaded and tagged: {item.song.artist} - {item.song.title}"
                    )
                    downloaded_count += 1
                except DownloadNoResultsError as exc:
                    playlist_log.error(
                        f"no yt-dlp search results for {item.song.artist} - {item.song.title}: {exc}"
                    )
                    songs_to_remove_from_playlist.append(item.song)
                    download_removals.append(item.song)
                    failed_count += 1
                except DownloadOutputMissingError as exc:
                    playlist_log.error(
                        f"download completed without an MP3 for {item.song.artist} - {item.song.title}: {exc}"
                    )
                    failed_count += 1
                except CalledProcessError as exc:
                    playlist_log.error(
                        f"failed to download {item.song.artist} - {item.song.title}: {exc}"
                    )
                    failed_count += 1

            if download_removals:
                songs = _save_playlist_state(
                    entry.file,
                    entry.name,
                    songs,
                    entry.df,
                    songs_to_remove=download_removals,
                    log=playlist_log.change,
                )

        removed_count = len({playlist_song_key(song) for song in songs_to_remove_from_playlist})
        final_song_count = len(songs)
        changed_parts: List[str] = []
        if added_from_files > 0:
            changed_parts.append(f"library backfill +{added_from_files}")
        if duplicates_removed > 0:
            changed_parts.append(f"duplicates removed {duplicates_removed}")
        if removed_count > 0:
            changed_parts.append(f"playlist removals {removed_count}")
        if downloaded_count > 0:
            changed_parts.append(f"downloads {downloaded_count}")
        if failed_count > 0:
            changed_parts.append(f"download failures {failed_count}")
        if override_updates:
            changed_parts.append("override updates")
        if validation_updates:
            changed_parts.append("validation updates")
        if actions.pending_overrides > 0 and not dry_run:
            changed_parts.append(f"pending overrides {actions.pending_overrides}")
        if changed_parts:
            playlist_log.info(
                "📋 summary: "
                + ", ".join(changed_parts)
                + f" | songs {initial_song_count} -> {final_song_count}"
            )

        normalized_songs = [
            Song(
                artist=song.artist,
                title=song.title,
                year=song.year,
                album=song.album,
                validated=song.validated,
            )
            for song in songs
        ]
        return (
            PlaylistSyncReport(
                name=entry.name,
                initial_song_count=initial_song_count,
                final_song_count=final_song_count,
                added_from_files=added_from_files,
                duplicates_removed=duplicates_removed,
                removed_count=removed_count,
                downloaded_count=downloaded_count,
                failed_count=failed_count,
                validation_updated=validation_updates,
                override_updated=override_updates,
                pending_overrides=actions.pending_overrides,
            ),
            normalized_songs,
        )

    def _override_candidates(
        self,
        songs: list[Song],
        music_dir: pathlib.Path,
    ) -> list[tuple[Song, pathlib.Path | None]]:
        candidates: list[tuple[Song, pathlib.Path | None]] = []
        for song in songs:
            if not song.override_url:
                continue
            safe_artist = sanitize_filename_component(song.artist) if song.artist else ""
            safe_title = sanitize_filename_component(song.title) if song.title else ""
            override_path = (
                music_dir / f"{safe_artist} - {safe_title}.mp3"
                if safe_artist and safe_title
                else None
            )
            candidates.append((song, override_path))
        return candidates

    def _build_playlist_actions(
        self,
        songs: list[Song],
        music_dir: pathlib.Path,
    ) -> _PlaylistActions:
        existing_songs: list[_SongLocation] = []
        missing_songs: list[_SongLocation] = []
        pending_overrides = 0

        for song in songs:
            safe_artist = sanitize_filename_component(song.artist)
            safe_title = sanitize_filename_component(song.title)
            song_path = music_dir / f"{safe_artist} - {safe_title}.mp3"
            location = _SongLocation(song, song_path)
            if song.override_url:
                pending_overrides += 1
                if song_path.exists():
                    existing_songs.append(location)
                continue
            if song_path.exists():
                existing_songs.append(location)
            else:
                missing_songs.append(location)

        return _PlaylistActions(
            existing_songs=existing_songs,
            missing_songs=missing_songs,
            pending_overrides=pending_overrides,
        )

    def _write_duplicate_analysis(
        self,
        analysis_log_file: pathlib.Path,
        all_songs_by_playlist: dict[str, list[Song]],
    ) -> pathlib.Path:
        analysis_lines: List[str] = []

        def log(line: str = "") -> None:
            analysis_lines.append(line)

        log("\n" + "=" * 60)
        log("📊 CROSS-PLAYLIST REPETITION ANALYSIS")
        log("=" * 60)

        song_appearances: dict[tuple[str, str], dict[str, object]] = {}
        for playlist_name, playlist_songs in all_songs_by_playlist.items():
            for song in playlist_songs:
                song_key = (song.artist.lower().strip(), song.title.lower().strip())
                if song_key not in song_appearances:
                    song_appearances[song_key] = {"song": song, "playlists": []}
                song_appearances[song_key]["playlists"].append(playlist_name)

        duplicates = {
            key: value
            for key, value in song_appearances.items()
            if len(value["playlists"]) > 1
        }

        total_songs = sum(len(playlist_songs) for playlist_songs in all_songs_by_playlist.values())
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

            sorted_duplicates = sorted(
                duplicates.items(),
                key=lambda item: len(item[1]["playlists"]),
                reverse=True,
            )
            for _, info in sorted_duplicates:
                song = info["song"]
                playlists = info["playlists"]
                playlist_count = len(playlists)
                log(f"\n   🎵 {song.artist} - {song.title} ({song.year})")
                log(f"      Appears in {playlist_count} playlists: {', '.join(playlists)}")

            appearance_counts: dict[int, int] = {}
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
        with analysis_log_file.open("w", encoding="utf-8") as handle:
            handle.write("\n".join(analysis_lines) + "\n")
        return analysis_log_file

    def _run_remote_sync(
        self,
        *,
        request: SyncRequest,
        songs_root: pathlib.Path,
    ) -> RemoteSyncResult | None:
        remote_sync = request.remote_sync
        if not remote_sync or not remote_sync.enabled:
            return None

        print("🌐 [remote-sync] preparing rsync...")
        remote_sync_config = build_remote_sync_config(
            station_slug=request.station_slug,
            local_songs_root=songs_root,
            dry_run=request.dry_run,
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
        mode_label = "preview" if request.dry_run else "apply"
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
        return remote_result


def main(
    station_slug: str,
    dry_run: bool = False,
    *,
    remote_sync: RemoteSyncRequest | None = None,
) -> SyncReport:
    return StationSync().run(
        SyncRequest(
            station_slug=station_slug,
            dry_run=dry_run,
            remote_sync=remote_sync,
        )
    )


def list_playlists(station_slug: str) -> None:
    """List all available playlists."""
    station_dir = station_dir_from_slug(station_slug)
    playlists_dir = station_dir / "playlists"
    if not playlists_dir.exists():
        print(f"Playlists directory '{playlists_dir}' does not exist!")
        return

    playlist_files = list(playlists_dir.glob("*.csv"))
    if not playlist_files:
        print(f"No playlist files found in '{playlists_dir}' directory!")
        return

    print("Available playlists:")
    for idx, playlist_file in enumerate(playlist_files):
        songs, _, _, _ = load_playlist(playlist_file)
        print(f"{idx}: {playlist_file.stem} ({len(songs)} songs)")


__all__ = [
    "DefaultMediaLibrary",
    "DefaultTrackResolver",
    "MediaLibrary",
    "PlaylistLog",
    "PlaylistSyncReport",
    "StationSync",
    "SyncReport",
    "SyncRequest",
    "TrackResolver",
    "_backfill_album_for_missing_song",
    "_save_playlist_state",
    "list_playlists",
    "main",
    "remove_new_releases_metadata_entries",
]
