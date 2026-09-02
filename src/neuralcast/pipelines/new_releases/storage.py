"""Playlist, cache, exclusion, and companion-metadata storage for New Releases."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable

import pandas as pd

from neuralcast.metadata.constants import (
    NEW_RELEASES_ARTIST_CACHE_FILENAME,
    NEW_RELEASES_EXCLUSIONS_FILENAME,
    NEW_RELEASES_METADATA_FILENAME,
    NEW_RELEASES_PLAYLIST_FILENAME,
)
from neuralcast.metadata.storage import load_station_json_dict, save_station_json_dict
from neuralcast.models import Song
from neuralcast.playlists.catalog import CatalogTrack, StationPlaylistCatalog

from .models import ArtistIDCache, ArtistRelease
from .logging import log_debug, log_error, log_info, log_success, log_warning
from .matching import _normalize_audio_label

_EXCLUDED_PLAYLIST_FILENAMES = {NEW_RELEASES_PLAYLIST_FILENAME.casefold()}


def load_artist_id_cache(playlists_dir: Path) -> ArtistIDCache:
    payload, _resolved = load_station_json_dict(
        playlists_dir,
        NEW_RELEASES_ARTIST_CACHE_FILENAME,
        log_warning=log_warning,
        warning_label="artist cache",
    )
    if not payload:
        return ArtistIDCache(entries={})
    entries: dict[str, str] = {}
    for key, value in payload.items():
        if isinstance(key, str) and isinstance(value, str):
            entries[key] = value
    return ArtistIDCache(entries=entries)


def save_artist_id_cache(
    playlists_dir: Path, cache: ArtistIDCache, *, dry_run: bool
) -> None:
    if not cache.dirty:
        return
    if dry_run:
        log_info("Dry run: not writing artist cache")
        return
    path = save_station_json_dict(
        playlists_dir,
        NEW_RELEASES_ARTIST_CACHE_FILENAME,
        cache.entries,
    )
    log_success(f"Cached {len(cache.entries)} artist IDs → {path}")


def load_release_exclusions(playlists_dir: Path) -> set[str]:
    path = playlists_dir.parent / "metadata" / NEW_RELEASES_EXCLUSIONS_FILENAME
    if not path.exists():
        return set()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log_warning(f"Could not read release exclusions from {path}: {exc}")
        return set()

    entries = payload.get("entries", []) if isinstance(payload, dict) else []
    exclusions: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        artist = str(entry.get("Artist") or "").strip()
        title = str(entry.get("Title") or "").strip()
        if artist and title:
            exclusions.add(_normalize_audio_label(artist, title))
    return exclusions


def load_station_artists(
    playlists_dir: Path,
) -> tuple[list[str], dict[str, set[str]], dict[str, dict[Path, set[str]]]]:
    artists: set[str] = set()
    artist_tracks: dict[str, set[str]] = {}
    artist_playlist_map: dict[str, dict[Path, set[str]]] = {}
    log_debug(f"Scanning playlists directory: {playlists_dir}")
    for csv_path in playlists_dir.glob("*.csv"):
        if csv_path.name.lower() in _EXCLUDED_PLAYLIST_FILENAMES:
            log_debug(f"Skipping excluded playlist file: {csv_path.name}")
            continue
        try:
            df = pd.read_csv(csv_path)
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Failed reading {csv_path}: {exc}")
            continue
        if "Artist" not in df.columns:
            continue
        titles_col = "Title" if "Title" in df.columns else None
        for _, row in df.iterrows():
            value = row.get("Artist")
            if pd.isna(value):
                continue
            artist_name = str(value).strip()
            if not artist_name:
                continue
            artists.add(artist_name)
            playlist_tracks = artist_playlist_map.setdefault(artist_name, {}).setdefault(
                csv_path, set()
            )
            if not titles_col:
                artist_tracks.setdefault(artist_name, set())
                continue
            title_val = row.get(titles_col)
            if pd.isna(title_val):
                continue
            title_str = str(title_val).strip()
            if not title_str:
                continue
            artist_tracks.setdefault(artist_name, set()).add(title_str)
            playlist_tracks.add(title_str)
    return sorted(artists), artist_tracks, artist_playlist_map


def _coerce_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return False


def load_existing_new_releases(playlists_dir: Path) -> list[ArtistRelease]:
    path = playlists_dir / NEW_RELEASES_PLAYLIST_FILENAME
    if not path.exists():
        log_debug("New Releases.csv not found; starting from empty state")
        return []
    try:
        tracks = StationPlaylistCatalog(
            playlists_dir,
            log=log_warning,
        ).load_tracks_with_metadata(
            path,
            metadata_filename=NEW_RELEASES_METADATA_FILENAME,
        )
    except Exception as exc:  # noqa: BLE001
        log_error(f"Failed reading {path}: {exc}")
        return []
    releases: list[ArtistRelease] = []
    for track in tracks:
        song = track.song
        metadata = track.metadata
        year_raw = str(song.year or "").strip()
        try:
            year = int(year_raw)
        except ValueError:
            year = datetime.now(UTC).year
        release_dt = datetime.min.replace(tzinfo=UTC)
        release_raw = str(metadata.get("ReleaseDate", "")).strip()
        if release_raw:
            try:
                release_dt = datetime.fromisoformat(release_raw)
                if release_dt.tzinfo is None:
                    release_dt = release_dt.replace(tzinfo=UTC)
            except ValueError:
                log_debug(
                    f"Invalid ReleaseDate '{release_raw}' for {song.artist} - {song.title}"
                )
        track_id = str(metadata.get("TrackID", "")).strip()
        rank = None
        rank_raw = metadata.get("Rank")
        if rank_raw not in (None, ""):
            try:
                rank = int(rank_raw)
            except (TypeError, ValueError):
                rank = None
        album_type = str(metadata.get("AlbumType", "")).strip() or None
        is_single = _coerce_bool(metadata.get("IsSingle", False))
        validated = bool(song.validated)
        if not validated:
            validated = _coerce_bool(metadata.get("Validated", False))
        releases.append(
            ArtistRelease(
                artist=song.artist,
                title=song.title,
                year=year,
                album=song.album or "",
                release_date=release_dt,
                track_id=track_id,
                rank=rank,
                is_single=is_single,
                album_type=album_type,
                validated=validated,
            )
        )
    return releases


def partition_releases_by_cutoff(
    releases: Iterable[ArtistRelease], cutoff: datetime
) -> tuple[list[ArtistRelease], list[ArtistRelease]]:
    valid: list[ArtistRelease] = []
    expired: list[ArtistRelease] = []
    for release in releases:
        if release.release_date >= cutoff:
            valid.append(release)
        else:
            expired.append(release)
    return valid, expired


def save_new_releases(playlists_dir: Path, releases: list[ArtistRelease], dry_run: bool) -> None:
    output_path = playlists_dir / NEW_RELEASES_PLAYLIST_FILENAME
    if not releases:
        log_info("No new releases to write.")
        print("No new releases to write.", file=sys.stderr)
        return

    sorted_releases = sorted(
        releases, key=lambda item: (item.release_date, item.rank or 0), reverse=True
    )
    preview_rows: list[dict[str, object]] = []
    for item in sorted_releases:
        preview_rows.append(
            {
                "Artist": item.artist,
                "Title": item.title,
                "Album": item.album,
                "Year": item.year,
                "ReleaseDate": item.release_date.isoformat(),
                "TrackID": item.track_id,
                "AlbumType": item.album_type or "",
                "IsSingle": item.is_single,
                "Rank": item.rank if item.rank is not None else "",
                "Validated": item.validated,
            }
        )
    df_preview = pd.DataFrame(preview_rows)
    if dry_run:
        log_info("Dry run: not writing CSV")
        print("Dry run: not writing CSV", file=sys.stderr)
        if not df_preview.empty:
            print(df_preview.to_string(index=False), flush=True)
        return

    tracks = [_release_catalog_track(item) for item in sorted_releases]
    StationPlaylistCatalog(playlists_dir, log=log_debug).replace_with_metadata(
        output_path,
        tracks,
        metadata_filename=NEW_RELEASES_METADATA_FILENAME,
    )
    log_success(f"Wrote {len(tracks)} tracks → {output_path}")
    print(f"Wrote {len(tracks)} tracks to {output_path}", flush=True)


def _release_metadata(release: ArtistRelease) -> dict[str, object]:
    return {
        "ReleaseDate": release.release_date.isoformat(),
        "TrackID": release.track_id,
        "AlbumType": release.album_type or "",
        "IsSingle": release.is_single,
        "Rank": release.rank if release.rank is not None else "",
        "Validated": release.validated,
    }


def _release_catalog_track(release: ArtistRelease) -> CatalogTrack:
    return CatalogTrack(
        song=Song(
            artist=release.artist,
            title=release.title,
            album=release.album or None,
            year=str(release.year),
            validated=release.validated,
        ),
        metadata=_release_metadata(release),
    )


__all__ = [
    "load_artist_id_cache",
    "load_existing_new_releases",
    "load_release_exclusions",
    "load_station_artists",
    "partition_releases_by_cutoff",
    "save_artist_id_cache",
    "save_new_releases",
]
