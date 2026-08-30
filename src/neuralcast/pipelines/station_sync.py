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
from neuralcast.metadata.constants import NEW_RELEASES_METADATA_FILENAME
from neuralcast.metadata.track_resolution import (
    CallableAlbumResolutionPort,
    CallableTrackValidationPort,
    ResolutionMode,
    TrackMetadataResolver,
    TrackResolutionRequest,
)
from neuralcast.models import Song
from neuralcast.playlists.catalog import (
    CatalogWritePolicy,
    PlaylistSnapshot,
    StationPlaylistCatalog,
)
from neuralcast.playlists.utils import (
    apply_library_renames,
    deduplicate_and_sort_songs,
    delete_marked_mp3_files,
    find_marked_mp3_files,
    normalize_year_value,
    plan_songs_from_library,
    playlist_song_key,
    replace_song_entry,
    sanitize_filename_component,
    save_playlist_with_validation,
)
from neuralcast.models import ValidationResult
from neuralcast.services.validation import (
    verified,
    verified_album,
)


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
    """Compatibility entrypoint for catalog-owned companion metadata cleanup."""

    if not songs_to_remove:
        return 0
    return StationPlaylistCatalog(playlists_dir).remove_companion_entries(
        songs_to_remove,
        filename=NEW_RELEASES_METADATA_FILENAME,
    )


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
    def __init__(self, resolver: TrackMetadataResolver | None = None) -> None:
        self._resolver = resolver or TrackMetadataResolver(
            validator=CallableTrackValidationPort(
                track_exists_func=verified,
                album_matches_func=verified_album,
            ),
            album_resolver=CallableAlbumResolutionPort(guess_album_func=guess_album),
        )

    def is_available(self, song: Song) -> bool:
        return self._resolver.resolve(
            TrackResolutionRequest(song=song, mode=ResolutionMode.CHECK_AVAILABLE)
        ).available

    def validate_song(self, song: Song) -> ValidationResult:
        resolution = self._resolver.resolve(
            TrackResolutionRequest(song=song, mode=ResolutionMode.VALIDATE)
        )
        return resolution.to_validation_result(song)

    def backfill_album(
        self,
        song: Song,
        *,
        log: Callable[[str], None] = print,
    ) -> tuple[Song, bool]:
        resolution = self._resolver.resolve(
            TrackResolutionRequest(
                song=song,
                mode=ResolutionMode.BACKFILL_ALBUM,
                prefer_spotify=False,
                prefer_deezer=True,
                min_confidence=0.55,
                allow_fallback=True,
            )
        )
        for note in resolution.notes:
            if note == "album_unconfirmed":
                log(
                    "⚠️ album could not be confirmed for "
                    f"{song.artist} - {song.title}: {song.album}"
                )
            elif note == "album_lookup_no_match":
                log(f"⚠️ album lookup found no match for {song.artist} - {song.title}")
            elif note.startswith("album_validation_failed:"):
                detail = note.removeprefix("album_validation_failed:")
                log(f"⚠️ album check failed for {song.artist} - {song.title}: {detail}")
            elif note.startswith("album_lookup_failed:"):
                detail = note.removeprefix("album_lookup_failed:")
                log(f"⚠️ album lookup failed for {song.artist} - {song.title}: {detail}")
        if resolution.album_cleared and song.album:
            log(
                f"🧹 album metadata cleared: "
                f"{song.artist} - {song.title} ({song.album})"
            )
        if resolution.album_changed and resolution.song and resolution.song.album:
            source = resolution.source or "unknown"
            confidence = (
                resolution.confidence if resolution.confidence is not None else 0.0
            )
            log(
                f"📝 album updated: {song.artist} - {song.title} -> {resolution.song.album} "
                f"(source {source}, confidence {confidence:.2f})"
            )
        if (
            resolution.song
            and resolution.song.year != song.year
            and resolution.release_date is not None
        ):
            log(
                f"📝 year updated: "
                f"{song.artist} - {song.title} -> {resolution.song.year}"
            )
        return resolution.song or song, resolution.changed


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
        repair: bool,
        log: PlaylistLog,
    ) -> int:
        mismatches = 0
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
                    f"⚠️ Cannot read tags ({exc}); metadata + album art need repair"
                )
                if repair:
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
                mismatches += 1
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

            action = "Updating" if repair else "Would update"
            status_lines.append(f"{action} fields: {', '.join(needs)}")
            refresh_art = "album" in needs
            if refresh_art:
                status_lines.append(
                    "Reapplying album art" if repair else "Would reapply album art"
                )
            if repair:
                _, tag_lines = _capture_output(
                    lambda: tag_mp3(
                        str(song_path),
                        song.artist,
                        song.title,
                        song.year,
                        playlist_name,
                        song.album,
                        log_prefix="      ",
                        refresh_art=refresh_art,
                        apply_replaygain=False,
                    )
                )
                _emit_captured_lines(tag_lines, logger=log.warning)
            mismatches += 1
            for line in status_lines:
                log.change(f"{track_label}: {line}")

        return mismatches

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

        catalog = StationPlaylistCatalog(playlists_dir)
        playlist_entries = self._load_playlist_entries(catalog, playlist_files)
        deletion_paths = self._apply_deletion_markers(
            playlist_entries,
            songs_root,
            dry_run=request.dry_run,
        )

        playlist_reports: list[PlaylistSyncReport] = []
        all_songs_by_playlist: dict[str, list[Song]] = {}

        for entry in playlist_entries:
            report, songs = self._sync_playlist(
                catalog,
                entry,
                songs_root,
                dry_run=request.dry_run,
                planned_deleted_paths=deletion_paths,
            )
            playlist_reports.append(report)
            all_songs_by_playlist[entry.name] = songs

        analysis_log_file = self._write_duplicate_analysis(
            station_dir / "duplicate_analysis.log",
            all_songs_by_playlist,
            dry_run=request.dry_run,
        )
        if request.dry_run:
            print(
                f"🧪 [sync] dry-run: would write cross-playlist analysis to "
                f"{analysis_log_file}"
            )
        else:
            print(f"📝 [sync] cross-playlist analysis written to {analysis_log_file}")

        return SyncReport(
            station_slug=request.station_slug,
            dry_run=request.dry_run,
            playlist_reports=playlist_reports,
            duplicate_analysis_log=analysis_log_file,
        )

    def _load_playlist_entries(
        self,
        catalog: StationPlaylistCatalog,
        playlist_files: list[pathlib.Path],
    ) -> list[_PlaylistEntry]:
        entries: list[_PlaylistEntry] = []
        for playlist_file in playlist_files:
            snapshot = catalog.load(playlist_file)
            entries.append(
                _PlaylistEntry(
                    snapshot=snapshot,
                    songs=snapshot.songs,
                    needs_save=snapshot.needs_persist,
                    deletions=snapshot.deletion_requests,
                )
            )
        return entries

    def _apply_deletion_markers(
        self,
        playlist_entries: list[_PlaylistEntry],
        songs_root: pathlib.Path,
        *,
        dry_run: bool,
    ) -> set[pathlib.Path]:
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
            return set()

        print(f"🗑️ [sync] processing {len(deletion_targets)} [DEL] marker(s)")
        for key, song in deletion_targets.items():
            playlists_list = sorted(deletion_sources.get(key, []))
            playlists_note = ", ".join(playlists_list)
            print(
                f"  📝 delete request: "
                f"{song.artist} - {song.title} ({playlists_note})"
            )

        if dry_run:
            deletion_paths = find_marked_mp3_files(
                deletion_targets,
                songs_root,
                log=lambda line: print(f"  {line}"),
            )
            deleted_files = len(deletion_paths)
            for path in deletion_paths:
                try:
                    relative_path = path.relative_to(songs_root)
                except ValueError:
                    relative_path = path
                print(f"  🧪 Would delete MP3 due to [DEL]: {relative_path}")
        else:
            deletion_paths = find_marked_mp3_files(
                deletion_targets,
                songs_root,
                log=lambda line: print(f"  {line}"),
            )
            deleted_files = delete_marked_mp3_files(
                deletion_targets,
                songs_root,
                log=lambda line: print(f"  {line}"),
            )
        if deleted_files:
            action = "would delete" if dry_run else "deleted"
            print(
                f"🗑️ [sync] {action} {deleted_files} MP3 file(s) "
                "due to [DEL] markers"
            )

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
        return set(deletion_paths)

    def _sync_playlist(
        self,
        catalog: StationPlaylistCatalog,
        entry: _PlaylistEntry,
        songs_root: pathlib.Path,
        *,
        dry_run: bool,
        planned_deleted_paths: set[pathlib.Path] | None = None,
    ) -> tuple[PlaylistSyncReport, list[Song]]:
        playlist_log = PlaylistLog(entry.name)
        songs = entry.songs
        if entry.removed_via_marker:
            playlist_log.change(
                f"🗑️ removed {entry.removed_via_marker} song(s) marked with "
                "[DEL] from playlist"
            )

        music_dir = songs_root / entry.name
        if not dry_run:
            music_dir.mkdir(parents=True, exist_ok=True)

        backfill_plan = plan_songs_from_library(
            entry.name,
            songs,
            music_dir,
            ignored_paths=planned_deleted_paths,
            log=playlist_log.change,
        )
        songs = backfill_plan.songs
        library_changed = backfill_plan.changed
        added_from_files = backfill_plan.added_from_files
        if dry_run:
            existing_paths = dict(backfill_plan.existing_paths)
            for rename in backfill_plan.renames:
                playlist_log.change(
                    f"🧪 dry-run: would rename "
                    f"{rename.source.name} -> {rename.target.name}"
                )
        else:
            existing_paths = apply_library_renames(
                backfill_plan,
                log=playlist_log.change,
            )
        songs, normalized_changed, duplicates_removed = deduplicate_and_sort_songs(
            songs
        )
        if duplicates_removed > 0:
            playlist_log.change(f"🧹 removed {duplicates_removed} duplicate row(s)")

        if entry.needs_save or library_changed or normalized_changed:
            catalog.save(
                entry.snapshot,
                songs,
                removed_songs=entry.deletions,
                policy=(
                    CatalogWritePolicy.PREVIEW
                    if dry_run
                    else CatalogWritePolicy.PERSIST
                ),
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
            catalog.save(entry.snapshot, songs)

        actions = self._build_playlist_actions(
            songs,
            music_dir,
            existing_paths=existing_paths,
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
            action = "would remove" if dry_run else "removed"
            playlist_log.change(
                f"🗑️ {action} invalid existing track: "
                f"{item.song.artist} - {item.song.title} ({item.path.name})"
            )
            if not dry_run:
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
            songs = self._save_catalog_state(
                catalog,
                entry.snapshot,
                songs,
                songs_to_remove=songs_to_remove_from_playlist,
                save_validation_updates=validation_updates,
                dry_run=dry_run,
                log=playlist_log.change,
            )

        removed_keys = {
            playlist_song_key(song) for song in songs_to_remove_from_playlist
        }
        current_songs = {playlist_song_key(song): song for song in songs}
        existing_pairs = [
            (current_songs.get(playlist_song_key(item.song), item.song), item.path)
            for item in actions.existing_songs
            if playlist_song_key(item.song) not in removed_keys
        ]
        tag_repair_count = 0
        if existing_pairs:
            tag_repair_count = self._media_library.audit_existing_tags(
                existing_pairs,
                entry.name,
                repair=not dry_run,
                log=playlist_log,
            )
            if tag_repair_count > 0:
                action = "would refresh" if dry_run else "refreshed"
                prefix = "🧪 " if dry_run else ""
                playlist_log.change(
                    f"{prefix}tag audit {action} {tag_repair_count} file(s)"
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
                songs = self._save_catalog_state(
                    catalog,
                    entry.snapshot,
                    songs,
                    songs_to_remove=download_removals,
                    dry_run=False,
                    log=playlist_log.change,
                )

        removed_count = entry.removed_via_marker + len(
            {playlist_song_key(song) for song in songs_to_remove_from_playlist}
        )
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
                planned_download_count=valid_count,
                tag_repair_count=tag_repair_count,
            ),
            normalized_songs,
        )

    @staticmethod
    def _save_catalog_state(
        catalog: StationPlaylistCatalog,
        snapshot: PlaylistSnapshot,
        songs: list[Song],
        *,
        songs_to_remove: list[Song] | None = None,
        save_validation_updates: bool = False,
        dry_run: bool = False,
        log: Callable[[str], None] = print,
    ) -> list[Song]:
        songs_to_remove = songs_to_remove or []
        removed_keys = {playlist_song_key(song) for song in songs_to_remove}
        updated_songs = [
            song for song in songs if playlist_song_key(song) not in removed_keys
        ]
        removed_count = len(songs) - len(updated_songs)
        if removed_count:
            log(f"🗑️ playlist rows removed: {removed_count}")
        if save_validation_updates or removed_count:
            catalog.save(
                snapshot,
                updated_songs,
                removed_songs=songs_to_remove,
                policy=(
                    CatalogWritePolicy.PREVIEW
                    if dry_run
                    else CatalogWritePolicy.PERSIST
                ),
            )
            action = "would update" if dry_run else "updated"
            log(f"📝 playlist CSV {action}")
        return updated_songs

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
        *,
        existing_paths: dict[tuple[str, str], pathlib.Path] | None = None,
    ) -> _PlaylistActions:
        existing_songs: list[_SongLocation] = []
        missing_songs: list[_SongLocation] = []
        pending_overrides = 0
        existing_paths = existing_paths or {}

        for song in songs:
            safe_artist = sanitize_filename_component(song.artist)
            safe_title = sanitize_filename_component(song.title)
            song_path = music_dir / f"{safe_artist} - {safe_title}.mp3"
            known_path = existing_paths.get(playlist_song_key(song))
            if not song_path.exists() and known_path is not None:
                song_path = known_path
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
        *,
        dry_run: bool = False,
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

        total_songs = sum(
            len(playlist_songs)
            for playlist_songs in all_songs_by_playlist.values()
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
        if not dry_run:
            with analysis_log_file.open("w", encoding="utf-8") as handle:
                handle.write("\n".join(analysis_lines) + "\n")
        return analysis_log_file

def main(
    station_slug: str,
    dry_run: bool = False,
) -> SyncReport:
    return StationSync().run(
        SyncRequest(
            station_slug=station_slug,
            dry_run=dry_run,
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
    catalog = StationPlaylistCatalog(playlists_dir)
    for idx, playlist_file in enumerate(playlist_files):
        snapshot = catalog.load(playlist_file)
        print(f"{idx}: {playlist_file.stem} ({len(snapshot.songs)} songs)")


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
