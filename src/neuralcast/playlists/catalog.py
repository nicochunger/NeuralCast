"""Station-scoped playlist catalog with CSV and companion-metadata consistency."""

from __future__ import annotations

import json
import pathlib
import re
import unicodedata
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Mapping, Sequence

import pandas as pd

from neuralcast.metadata.constants import (
    NEW_RELEASES_METADATA_FILENAME,
    NEW_RELEASES_PLAYLIST_NAME,
)
from neuralcast.metadata.storage import (
    load_station_entry_mapping,
    metadata_key,
    save_station_entry_mapping,
)
from neuralcast.models import Song

DELETE_MARKER = "[DEL]"
NEW_RELEASES_PLAYLIST = NEW_RELEASES_PLAYLIST_NAME
_YOUTUBE_HOST_FRAGMENTS = ("youtube.com", "youtu.be")
_OVERRIDE_PATTERN = re.compile(r"^\[(https?://[^\]]+)\]\s*(.*)$")
_STANDARD_COLUMNS = ("Artist", "Title", "Year", "Album", "Validated")
_NEW_RELEASES_COLUMNS = ("Artist", "Title", "Album", "Year", "Validated")
_FLOAT_YEAR_PATTERN = re.compile(r"^(\d{4})\.0+$")
_ZEROED_DATE_YEAR_PATTERN = re.compile(r"^(\d{4})-00(?:-00)?$")


class CatalogFormatError(ValueError):
    """Raised when a playlist cannot satisfy the catalog format."""


class CatalogPersistenceError(OSError):
    """Raised when a catalog write cannot be completed."""


class CatalogWritePolicy(str, Enum):
    PERSIST = "persist"
    PREVIEW = "preview"


def normalize_year_value(value: object) -> str | None:
    """Normalize CSV and ID3 year values while preserving unknown years."""
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    if not text or text.casefold() == "nan":
        return None
    if text.casefold() == "unknown":
        return "Unknown"
    if match := _FLOAT_YEAR_PATTERN.fullmatch(text):
        return match.group(1)
    if match := _ZEROED_DATE_YEAR_PATTERN.fullmatch(text):
        return match.group(1)
    return text


def playlist_song_key(song: Song) -> tuple[str, str]:
    return (song.artist.lower().strip(), song.title.lower().strip())


def deduplicate_and_sort_songs(songs: list[Song]) -> tuple[list[Song], bool, int]:
    """Return stable playlist identity deduplication in canonical display order."""
    seen: dict[tuple[str, str], Song] = {}
    ordered_unique: list[Song] = []
    for song in songs:
        key = playlist_song_key(song)
        if key not in seen:
            seen[key] = song
            ordered_unique.append(song)
    duplicates_removed = len(songs) - len(ordered_unique)
    sorted_songs = sorted(
        ordered_unique,
        key=lambda song: (song.artist.lower().strip(), song.title.lower().strip()),
    )
    return sorted_songs, duplicates_removed > 0 or sorted_songs != songs, duplicates_removed


def replace_song_entry(songs: list[Song], updated_song: Song) -> None:
    target_key = playlist_song_key(updated_song)
    for index, existing in enumerate(songs):
        if playlist_song_key(existing) == target_key:
            songs[index] = updated_song
            return


@dataclass(frozen=True)
class CatalogTrack:
    song: Song
    metadata: Mapping[str, Any]


@dataclass
class PlaylistSnapshot:
    path: pathlib.Path
    songs: list[Song]
    deletion_requests: list[Song]
    needs_persist: bool
    _source_frame: pd.DataFrame

    @property
    def name(self) -> str:
        return self.path.stem

class StationPlaylistCatalog:
    """Own playlist parsing, round-tripping, identity, and companion metadata."""

    def __init__(
        self,
        playlists_dir: pathlib.Path,
        *,
        log: Callable[[str], None] = print,
    ) -> None:
        self.playlists_dir = pathlib.Path(playlists_dir)
        self._log = log

    def load(self, playlist: str | pathlib.Path) -> PlaylistSnapshot:
        path = self._resolve_playlist_path(playlist)
        try:
            frame = pd.read_csv(
                path,
                dtype={
                    "Year": "string",
                    "Artist": "string",
                    "Title": "string",
                    "Album": "string",
                },
                keep_default_na=False,
                na_filter=False,
            )
        except (OSError, pd.errors.ParserError) as exc:
            raise CatalogFormatError(f"Unable to read playlist {path}: {exc}") from exc

        column_lookup = {
            str(column).casefold(): str(column) for column in frame.columns
        }
        missing = [
            column for column in ("artist", "title") if column not in column_lookup
        ]
        if missing:
            raise CatalogFormatError(
                f"Playlist {path} is missing required column(s): {', '.join(missing)}"
            )

        needs_persist = False
        if "validated" not in column_lookup:
            frame["Validated"] = False
            column_lookup["validated"] = "Validated"
            needs_persist = True
            self._log(f"Added 'Validated' column to {path}")

        songs: list[Song] = []
        deletion_requests: list[Song] = []
        missing_year_count = 0
        for _, row in frame.iterrows():
            artist_raw = _normalize_csv_value(row[column_lookup["artist"]])
            title_raw = _normalize_csv_value(row[column_lookup["title"]])
            year_raw = row[column_lookup["year"]] if "year" in column_lookup else None
            year = normalize_year_value(year_raw)
            original_year = _normalize_csv_value(year_raw)
            if original_year and year and year != original_year:
                needs_persist = True
            album_raw = (
                _normalize_csv_value(row[column_lookup["album"]])
                if "album" in column_lookup
                else None
            )

            artist_without_override, override_url = _extract_override(artist_raw)
            artist, artist_marked = _strip_delete_prefix(artist_without_override)
            title, title_marked = _strip_delete_prefix(title_raw)
            album, _album_marked = _strip_delete_prefix(album_raw)
            validated = _as_bool(row[column_lookup["validated"]])

            if artist_marked or title_marked:
                if artist and title:
                    deletion_requests.append(
                        Song(
                            artist=artist,
                            title=title,
                            year=year or "",
                            album=album,
                            validated=False,
                        )
                    )
                else:
                    self._log(
                        f"Warning: Could not parse [DEL] row in {path}; "
                        "missing artist/title"
                    )
                needs_persist = True
                continue

            if artist and title:
                if not year:
                    missing_year_count += 1
                songs.append(
                    Song(
                        artist=artist,
                        title=title,
                        year=year or "",
                        album=album,
                        validated=validated,
                        override_url=override_url,
                    )
                )
            else:
                self._log(
                    f"Warning: Skipping incomplete row in {path}: "
                    f"Artist={artist}, Title={title}, Year={year}"
                )

        if missing_year_count:
            self._log(
                f"Warning: {missing_year_count} row(s) missing Year in {path}; "
                "leaving blank"
            )

        return PlaylistSnapshot(
            path=path,
            songs=songs,
            deletion_requests=deletion_requests,
            needs_persist=needs_persist,
            _source_frame=frame,
        )

    def save(
        self,
        snapshot: PlaylistSnapshot,
        songs: Sequence[Song],
        *,
        removed_songs: Sequence[Song] = (),
        policy: CatalogWritePolicy = CatalogWritePolicy.PERSIST,
    ) -> pathlib.Path:
        if policy is CatalogWritePolicy.PREVIEW:
            return snapshot.path

        frame = _merge_songs_into_frame(snapshot._source_frame, songs)
        self._write_csv(snapshot.path, frame)
        snapshot.songs = list(songs)
        snapshot.needs_persist = False
        snapshot._source_frame = frame
        self._log(f"Cleaned and sorted playlist saved to {snapshot.path}")

        is_new_releases = (
            snapshot.name.casefold() == NEW_RELEASES_PLAYLIST.casefold()
        )
        if removed_songs and is_new_releases:
            self.remove_companion_entries(removed_songs)
        return snapshot.path

    def append(
        self,
        playlist: str | pathlib.Path,
        song: Song,
        *,
        policy: CatalogWritePolicy = CatalogWritePolicy.PERSIST,
    ) -> bool:
        snapshot = self.load(playlist)
        key = playlist_song_key(song)
        if any(playlist_song_key(existing) == key for existing in snapshot.songs):
            return False
        new_song = song.model_copy(update={"validated": False})
        self.save(snapshot, [*snapshot.songs, new_song], policy=policy)
        return True

    def load_tracks_with_metadata(
        self,
        playlist: str | pathlib.Path = NEW_RELEASES_PLAYLIST,
        *,
        metadata_filename: str = NEW_RELEASES_METADATA_FILENAME,
    ) -> list[CatalogTrack]:
        snapshot = self.load(playlist)
        entries = self.load_companion_entries(metadata_filename)
        tracks: list[CatalogTrack] = []
        for song in snapshot.songs:
            exact_metadata = entries.get(_song_metadata_key(song))
            if exact_metadata is not None:
                tracks.append(CatalogTrack(song=song, metadata=exact_metadata))
                continue

            identity = _song_companion_identity(song)
            identity_matches = [
                value
                for key, value in entries.items()
                if _metadata_identity(key) == identity
            ]
            metadata = identity_matches[0] if len(identity_matches) == 1 else {}
            tracks.append(CatalogTrack(song=song, metadata=metadata))
        return tracks

    def replace_with_metadata(
        self,
        playlist: str | pathlib.Path,
        tracks: Sequence[CatalogTrack],
        *,
        metadata_filename: str = NEW_RELEASES_METADATA_FILENAME,
        policy: CatalogWritePolicy = CatalogWritePolicy.PERSIST,
    ) -> pathlib.Path:
        path = self._resolve_playlist_path(playlist)
        if policy is CatalogWritePolicy.PREVIEW:
            return path

        frame = pd.DataFrame(
            [_song_row(track.song) for track in tracks],
            columns=list(_NEW_RELEASES_COLUMNS),
        )
        entries = {
            _song_metadata_key(track.song): dict(track.metadata) for track in tracks
        }

        # Stage both artifacts before either destination is replaced.
        csv_tmp = path.with_suffix(path.suffix + ".tmp")
        metadata_path = self.playlists_dir.parent / "metadata" / metadata_filename
        metadata_tmp = metadata_path.with_suffix(metadata_path.suffix + ".tmp")
        csv_backup = _transaction_backup_path(path)
        metadata_backup = _transaction_backup_path(metadata_path)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            metadata_path.parent.mkdir(parents=True, exist_ok=True)
            if csv_backup.exists() or metadata_backup.exists():
                raise CatalogPersistenceError(
                    "Cannot start playlist catalog replacement while a prior "
                    "transaction backup exists."
                )
            frame.to_csv(csv_tmp, index=False)
            metadata_tmp.write_text(
                json.dumps({"entries": entries}, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            _replace_pair_atomically(
                csv_tmp=csv_tmp,
                csv_path=path,
                csv_backup=csv_backup,
                metadata_tmp=metadata_tmp,
                metadata_path=metadata_path,
                metadata_backup=metadata_backup,
            )
        except (OSError, TypeError, ValueError) as exc:
            csv_tmp.unlink(missing_ok=True)
            metadata_tmp.unlink(missing_ok=True)
            raise CatalogPersistenceError(
                f"Unable to replace playlist catalog artifacts for {path}: {exc}"
            ) from exc
        return path

    def load_companion_entries(
        self, filename: str = NEW_RELEASES_METADATA_FILENAME
    ) -> dict[str, dict[str, Any]]:
        entries, _resolved = load_station_entry_mapping(
            self.playlists_dir,
            filename,
            log_warning=lambda message: self._log(f"Warning: {message}"),
            warning_label="metadata file",
            legacy_fallback=False,
        )
        return {
            str(key): dict(value)
            for key, value in entries.items()
            if isinstance(key, str) and isinstance(value, Mapping)
        }

    def remove_companion_entries(
        self,
        songs: Sequence[Song],
        *,
        filename: str = NEW_RELEASES_METADATA_FILENAME,
    ) -> int:
        entries = self.load_companion_entries(filename)
        removed = 0
        for song in songs:
            primary_key = _song_metadata_key(song)
            if primary_key in entries:
                del entries[primary_key]
                removed += 1
                continue

            identity = _song_companion_identity(song)
            matches = [
                key
                for key in entries
                if _metadata_identity(key) == identity
            ]
            if len(matches) == 1:
                del entries[matches[0]]
                removed += 1

        if removed:
            try:
                save_station_entry_mapping(self.playlists_dir, filename, entries)
            except (OSError, TypeError, ValueError) as exc:
                raise CatalogPersistenceError(
                    f"Unable to update companion metadata {filename}: {exc}"
                ) from exc
        return removed

    def _resolve_playlist_path(self, playlist: str | pathlib.Path) -> pathlib.Path:
        path = pathlib.Path(playlist)
        if not path.is_absolute():
            filename = path.name if path.suffix else f"{path.name}.csv"
            path = self.playlists_dir / filename
        return path

    def _write_csv(self, path: pathlib.Path, frame: pd.DataFrame) -> None:
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            frame.to_csv(tmp_path, index=False)
            tmp_path.replace(path)
        except (OSError, ValueError) as exc:
            tmp_path.unlink(missing_ok=True)
            raise CatalogPersistenceError(
                f"Unable to save playlist {path}: {exc}"
            ) from exc


def _normalize_csv_value(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        return None if not text or text.casefold() == "nan" else text
    if pd.isna(value):
        return None
    text = str(value).strip()
    return None if not text or text.casefold() == "nan" else text


def _strip_delete_prefix(value: str | None) -> tuple[str | None, bool]:
    if value is None or not value.startswith(DELETE_MARKER):
        return value, False
    cleaned = value[len(DELETE_MARKER) :].strip()
    return cleaned or None, True


def _extract_override(value: str | None) -> tuple[str | None, str | None]:
    if value is None:
        return None, None
    match = _OVERRIDE_PATTERN.match(value.strip())
    if not match:
        return value, None
    url = match.group(1).strip()
    if not any(host in url.casefold() for host in _YOUTUBE_HOST_FRAGMENTS):
        return value, None
    remainder = match.group(2).strip()
    return remainder or None, url


def _as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value != 0
    return isinstance(value, str) and value.strip().casefold() in {
        "true",
        "1",
        "yes",
        "y",
    }


def _merge_songs_into_frame(frame: pd.DataFrame, songs: Sequence[Song]) -> pd.DataFrame:
    source = frame.copy(deep=True)
    for column in _STANDARD_COLUMNS:
        if column not in source.columns:
            source[column] = "" if column != "Validated" else False

    songs_by_key = {playlist_song_key(song): song for song in songs}
    updated_rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for _, row in source.iterrows():
        artist, _override_url = _extract_override(
            _normalize_csv_value(row.get("Artist", ""))
        )
        artist, _artist_marked = _strip_delete_prefix(artist)
        title, _title_marked = _strip_delete_prefix(
            _normalize_csv_value(row.get("Title", ""))
        )
        key = (
            (artist or "").strip().casefold(),
            (title or "").strip().casefold(),
        )
        song = songs_by_key.get(key)
        if song is None:
            continue
        values = row.to_dict()
        values.update(_song_row(song))
        updated_rows.append(values)
        seen.add(key)

    for song in songs:
        key = playlist_song_key(song)
        if key in seen:
            continue
        values = {str(column): "" for column in source.columns}
        values.update(_song_row(song))
        updated_rows.append(values)

    return pd.DataFrame(updated_rows, columns=source.columns)


def _song_row(song: Song) -> dict[str, Any]:
    return {
        "Artist": (
            f"[{song.override_url}] {song.artist}"
            if song.override_url
            else song.artist
        ),
        "Title": song.title,
        "Year": normalize_year_value(song.year) or "",
        "Album": song.album or "",
        "Validated": bool(song.validated),
    }


def _song_metadata_key(song: Song) -> str:
    return metadata_key(song.artist, song.title, song.album or "", song.year)


def _transaction_backup_path(path: pathlib.Path) -> pathlib.Path:
    return path.with_name(f".{path.name}.catalog-backup")


def _replace_pair_atomically(
    *,
    csv_tmp: pathlib.Path,
    csv_path: pathlib.Path,
    csv_backup: pathlib.Path,
    metadata_tmp: pathlib.Path,
    metadata_path: pathlib.Path,
    metadata_backup: pathlib.Path,
) -> None:
    """Replace the two artifacts, rolling back both if either commit fails."""

    csv_existed = csv_path.exists()
    metadata_existed = metadata_path.exists()
    csv_committed = False
    metadata_committed = False
    try:
        if csv_existed:
            csv_path.replace(csv_backup)
        csv_tmp.replace(csv_path)
        csv_committed = True

        if metadata_existed:
            metadata_path.replace(metadata_backup)
        metadata_tmp.replace(metadata_path)
        metadata_committed = True
    except OSError as exc:
        recovery_errors = _rollback_pair_replacement(
            csv_path=csv_path,
            csv_backup=csv_backup,
            csv_existed=csv_existed,
            csv_committed=csv_committed,
            metadata_path=metadata_path,
            metadata_backup=metadata_backup,
            metadata_existed=metadata_existed,
            metadata_committed=metadata_committed,
        )
        detail = f"; rollback failed: {' | '.join(recovery_errors)}" if recovery_errors else ""
        raise CatalogPersistenceError(
            f"Unable to commit paired playlist artifacts: {exc}{detail}"
        ) from exc
    else:
        _remove_transaction_backup(csv_backup)
        _remove_transaction_backup(metadata_backup)


def _rollback_pair_replacement(
    *,
    csv_path: pathlib.Path,
    csv_backup: pathlib.Path,
    csv_existed: bool,
    csv_committed: bool,
    metadata_path: pathlib.Path,
    metadata_backup: pathlib.Path,
    metadata_existed: bool,
    metadata_committed: bool,
) -> list[str]:
    errors: list[str] = []
    _restore_artifact(
        path=metadata_path,
        backup=metadata_backup,
        existed=metadata_existed,
        committed=metadata_committed,
        errors=errors,
    )
    _restore_artifact(
        path=csv_path,
        backup=csv_backup,
        existed=csv_existed,
        committed=csv_committed,
        errors=errors,
    )
    return errors


def _restore_artifact(
    *,
    path: pathlib.Path,
    backup: pathlib.Path,
    existed: bool,
    committed: bool,
    errors: list[str],
) -> None:
    try:
        if committed and path.exists():
            path.unlink()
        if existed and backup.exists():
            backup.replace(path)
        elif not existed and path.exists():
            path.unlink()
    except OSError as exc:
        errors.append(f"{path}: {exc}")


def _remove_transaction_backup(path: pathlib.Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        # A leftover backup is recoverable and must not turn a completed commit
        # into a reported failure.
        pass


def _metadata_identity(key: str) -> tuple[str, str] | None:
    parts = key.split("|")
    if len(parts) < 2:
        return None
    return (
        _normalize_companion_identity_component(parts[0]),
        _normalize_companion_identity_component(parts[1]),
    )


def _song_companion_identity(song: Song) -> tuple[str, str]:
    return (
        _normalize_companion_identity_component(song.artist),
        _normalize_companion_identity_component(song.title),
    )


def _normalize_companion_identity_component(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value or "")
    stripped = "".join(char for char in normalized if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]", "", stripped.casefold())


__all__ = [
    "CatalogFormatError",
    "CatalogPersistenceError",
    "CatalogTrack",
    "CatalogWritePolicy",
    "deduplicate_and_sort_songs",
    "NEW_RELEASES_METADATA_FILENAME",
    "NEW_RELEASES_PLAYLIST",
    "PlaylistSnapshot",
    "playlist_song_key",
    "normalize_year_value",
    "replace_song_entry",
    "StationPlaylistCatalog",
]
