"""Update station's New Releases playlist with latest tracks via Spotify."""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from difflib import SequenceMatcher
from pathlib import Path
from typing import Iterable, Optional, Set

import pandas as pd
from dotenv import load_dotenv
from spotipy import Spotify, SpotifyException
from spotipy.oauth2 import SpotifyClientCredentials
from tqdm import tqdm
import unicodedata

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    # handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)


_METADATA_FILENAME = "New Releases.metadata.json"


@dataclass
class ArtistRelease:
    artist: str
    title: str
    year: int
    album: str
    release_date: datetime
    track_id: str
    # New optional metadata used for ranking/filters
    popularity: Optional[int] = None
    is_single: bool = False
    album_type: Optional[str] = None
    validated: bool = False

    def __repr__(self) -> str:
        return (
            f"ArtistRelease(\n"
            f"    artist={self.artist!r},\n"
            f"    title={self.title!r},\n"
            f"    year={self.year},\n"
            f"    album={self.album!r},\n"
            f"    release_date={self.release_date!r},\n"
            f"    track_id={self.track_id!r}\n"
            f")"
        )


def load_station_artists(
    playlists_dir: Path,
) -> tuple[list[str], dict[str, set[str]], dict[str, dict[Path, set[str]]]]:
    artists: set[str] = set()
    artist_tracks: dict[str, set[str]] = {}
    artist_playlist_map: dict[str, dict[Path, set[str]]] = {}
    logger.debug(f"Scanning playlists directory: {playlists_dir}")
    for csv_path in playlists_dir.glob("*.csv"):
        logger.debug(f"Checking file: {csv_path}")
        if csv_path.name.lower() == "new releases.csv":
            logger.debug(f"Skipping New Releases file: {csv_path}")
            continue
        try:
            df = pd.read_csv(csv_path)
            logger.debug(f"Loaded CSV: {csv_path} with columns: {df.columns.tolist()}")
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Failed reading {csv_path}: {exc}")
            continue
        if "Artist" not in df.columns:
            logger.debug(f"No 'Artist' column in {csv_path}, skipping file.")
            continue
        titles_col = "Title" if "Title" in df.columns else None
        for _, row in df.iterrows():
            value = row.get("Artist")
            if pd.isna(value):
                continue
            name = str(value).strip()
            if not name:
                continue
            artists.add(name)
            playlist_tracks = artist_playlist_map.setdefault(name, {}).setdefault(csv_path, set())
            if titles_col:
                title_val = row.get(titles_col)
                if pd.isna(title_val):
                    continue
                title_str = str(title_val).strip()
                if title_str:
                    artist_tracks.setdefault(name, set()).add(title_str)
                    playlist_tracks.add(title_str)
            else:
                artist_tracks.setdefault(name, set())
    logger.debug(f"Found {len(artists)} unique artists: {sorted(artists)}")
    return sorted(artists), artist_tracks, artist_playlist_map


def parse_release_date(date_str: str, precision: str) -> Optional[datetime]:
    logger.debug(f"Parsing release date '{date_str}' with precision '{precision}'")
    if not date_str:
        logger.debug("No date string provided, returning None")
        return None
    try:
        match precision:
            case "day":
                dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=UTC)
            case "month":
                dt = datetime.strptime(date_str, "%Y-%m").replace(day=1, tzinfo=UTC)
            case "year":
                dt = datetime.strptime(date_str, "%Y").replace(
                    month=1, day=1, tzinfo=UTC
                )
            case _:
                logger.debug(f"Unknown precision '{precision}', returning None")
                return None
        logger.debug(f"Parsed date: {dt}")
        return dt
    except ValueError as e:
        logger.debug(
            f"ValueError parsing date '{date_str}' with precision '{precision}': {e}"
        )
        return None


def _artist_has_known_track(
    sp: Spotify, artist_id: str, artist_name: str, known_titles: set[str]
) -> bool:
    if not known_titles:
        return False
    for title in list(known_titles)[:5]:
        query = f'track:"{title}" artist:"{artist_name}"'
        while True:
            try:
                res = sp.search(q=query, type="track", limit=5)
                break
            except SpotifyException as exc:
                if exc.http_status == 429:
                    retry_after = int(exc.headers.get("Retry-After", "5"))
                    logger.warning(
                        f"Rate limited during known track search, sleeping {retry_after}s"
                    )
                    time.sleep(retry_after)
                    continue
                logger.error(
                    f"Spotify search failed for known track '{title}' ({artist_name}): {exc}"
                )
                return False
        for track in res.get("tracks", {}).get("items", []):
            artists = track.get("artists", [])
            if any(art.get("id") == artist_id for art in artists if art.get("id")):
                return True
    return False


def _best_artist_match(
    sp: Spotify, artist_name: str, known_titles: Optional[set[str]] = None
) -> Optional[dict]:
    query = f'artist:"{artist_name}"'
    logger.debug(f"Searching for artist: {artist_name} with query: {query}")
    while True:
        try:
            res = sp.search(q=query, type="artist", limit=10)
            break
        except SpotifyException as exc:
            if exc.http_status == 429:
                retry_after = int(exc.headers.get("Retry-After", "5"))
                logger.warning(f"Rate limited during artist search, sleeping {retry_after}s")
                time.sleep(retry_after)
                continue
            logger.error(f"Spotify search failed for {artist_name}: {exc}")
            return None
    items = res.get("artists", {}).get("items", [])
    logger.debug(
        f"Artist search returned {len(items)} items for {artist_name}: {[item.get('name') for item in items]}"
    )
    exact_matches = [
        item for item in items if item.get("name", "").casefold() == artist_name.casefold()
    ]
    if not exact_matches:
        logger.debug(f"No exact match found for artist: {artist_name}")
        return None
    if len(exact_matches) == 1 or not known_titles:
        return exact_matches[0]
    for candidate in exact_matches:
        artist_id = candidate.get("id")
        if not artist_id:
            continue
        if _artist_has_known_track(sp, artist_id, artist_name, known_titles):
            logger.debug(f"Disambiguated artist '{artist_name}' using known titles")
            return candidate
    logger.debug(f"Multiple exact matches for '{artist_name}', returning the first one by default")
    return exact_matches[0]


# Robust filters for non-new variants
_TITLE_EXCLUDE_PATTERNS = [
    re.compile(r"\blive\b", re.I),
    re.compile(r"\bremaster(?:ed|s|ing)?\b", re.I),
    re.compile(r"\bre[-\s]?record(?:ed|ing)?\b", re.I),  # re-recorded, rerecorded
    re.compile(r"\bre[-\s]?imagined\b", re.I),
    re.compile(r"\bredux\b", re.I),
    re.compile(r"\bacoustic\b", re.I),
    re.compile(r"\bdemo\b", re.I),
    re.compile(r"\bradio\s+edit\b", re.I),
    re.compile(r"\bedit\b", re.I),
    re.compile(r"\bremix\b", re.I),
    re.compile(r"\bversion\b", re.I),
    re.compile(r"\bon\s+stage\.?\b", re.I),
]
_ALBUM_EXCLUDE_PATTERNS = _TITLE_EXCLUDE_PATTERNS + [
    re.compile(r"\banniversary\b", re.I),
    re.compile(r"\bdeluxe\b", re.I),
    re.compile(r"\bexpanded\b", re.I),
    re.compile(r"\breissue\b", re.I),
    re.compile(r"\bmono\b", re.I),
    re.compile(r"\bstereo\b", re.I),
    re.compile(r"\bgreatest\s+hits\b", re.I),
    re.compile(r"\bbest\s+of\b", re.I),
    re.compile(r"\bcollection\b", re.I),
    re.compile(r"\banthology\b", re.I),
]


def _is_alt_or_reissue(title: str, album_name: str) -> bool:
    """Return True if the track likely represents a non-new version (live, remaster, re-recorded, deluxe, anniversary, etc.)."""
    t = title or ""
    a = album_name or ""
    for pat in _TITLE_EXCLUDE_PATTERNS:
        if pat.search(t):
            return True
    for pat in _ALBUM_EXCLUDE_PATTERNS:
        if pat.search(a):
            return True
    return False


def _album_tracks_by_artist(sp: Spotify, album_id: str, artist_id: str) -> list[dict]:
    tracks: list[dict] = []
    offset = 0
    logger.debug(f"Fetching tracks for album {album_id} and artist {artist_id}")
    while True:
        page = sp.album_tracks(album_id, limit=50, offset=offset)
        items = page.get("items", [])
        logger.debug(
            f"Fetched {len(items)} tracks at offset {offset} for album {album_id}"
        )
        if not items:
            logger.debug(
                f"No more tracks found for album {album_id} at offset {offset}"
            )
            break
        for track in items:
            artists = track.get("artists", [])
            logger.debug(
                f"Track '{track.get('name', '')}' artists: {[art.get('id') for art in artists]}"
            )
            if any(art.get("id") == artist_id for art in artists if art.get("id")):
                logger.debug(
                    f"Track '{track.get('name', '')}' matches artist {artist_id}"
                )
                tracks.append(track)
        if not page.get("next"):
            logger.debug(f"No next page for album tracks in album {album_id}")
            break
        offset += len(items)
    logger.debug(
        f"Total tracks found for artist {artist_id} in album {album_id}: {len(tracks)}"
    )
    return tracks


def _iter_recent_albums(
    sp: Spotify, artist_id: str, cutoff: datetime
) -> Iterable[tuple[datetime, dict]]:
    offset = 0
    seen_albums: set[str] = set()
    logger.debug(f"Fetching recent albums for artist {artist_id} after {cutoff}")
    while True:
        try:
            page = sp.artist_albums(
                artist_id,
                include_groups="album,single",
                limit=50,
                offset=offset,
                country="US",
            )
        except SpotifyException as exc:
            if exc.http_status == 429:
                retry_after = int(exc.headers.get("Retry-After", "5"))
                logger.warning(
                    f"Rate limited during albums fetch, sleeping {retry_after}s"
                )
                time.sleep(retry_after)
                continue
            logger.error(f"Spotify albums failed for {artist_id}: {exc}")
            break
        items = page.get("items", [])
        logger.debug(
            f"Fetched {len(items)} albums at offset {offset} for artist {artist_id}"
        )
        if not items:
            logger.debug(
                f"No more albums found for artist {artist_id} at offset {offset}"
            )
            break
        for album in items:
            album_id = album.get("id")
            logger.debug(f"Processing album: {album_id}")
            if not album_id or album_id in seen_albums:
                logger.debug(f"Skipping album {album_id}: already seen or missing ID")
                continue
            seen_albums.add(album_id)
            release = parse_release_date(
                album.get("release_date", ""), album.get("release_date_precision", "")
            )
            logger.debug(f"Album {album_id} release date: {release}")
            if not release or release < cutoff:
                logger.debug(
                    f"Skipping album {album_id}: release date {release} before cutoff {cutoff}"
                )
                continue
            logger.debug(f"Yielding album {album_id} released on {release}")
            yield release, album
        if not page.get("next"):
            logger.debug(f"No next page for artist albums for artist {artist_id}")
            break
        offset += len(items)


def _fetch_popularity_bulk(sp: Spotify, track_ids: list[str]) -> dict[str, int]:
    """Fetch popularity for up to len(track_ids) tracks in batches."""
    result: dict[str, int] = {}
    i = 0
    while i < len(track_ids):
        chunk = track_ids[i : i + 50]
        try:
            resp = sp.tracks(chunk)
        except SpotifyException as exc:
            if exc.http_status == 429:
                retry_after = int(exc.headers.get("Retry-After", "5"))
                logger.warning(
                    f"Rate limited during tracks fetch, sleeping {retry_after}s"
                )
                time.sleep(retry_after)
                continue
            logger.error(f"Spotify tracks batch failed: {exc}")
            break
        for t in resp.get("tracks") or []:
            if not t:
                continue
            tid = t.get("id")
            if tid:
                result[tid] = int(t.get("popularity") or 0)
        i += len(chunk)
    return result


def _annotate_popularity(sp: Spotify, releases: list[ArtistRelease]) -> None:
    """Mutates releases to fill popularity using batch lookup."""
    ids = [r.track_id for r in releases if r.track_id]
    if not ids:
        return
    pops = _fetch_popularity_bulk(sp, ids)
    for r in releases:
        r.popularity = pops.get(r.track_id, 0)


def _coerce_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return False


def _normalize_audio_label(*parts: str) -> str:
    text = " ".join(part or "" for part in parts)
    normalized = unicodedata.normalize("NFKD", text)
    return re.sub(r"[^a-z0-9]", "", normalized.casefold())


def _metadata_path(playlists_dir: Path) -> Path:
    return playlists_dir / _METADATA_FILENAME


def _normalize_metadata_component(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value or "")
    return normalized.strip().casefold()


def _metadata_key(artist: str, title: str, album: str, year: int) -> str:
    return "|".join(
        (
            _normalize_metadata_component(artist),
            _normalize_metadata_component(title),
            _normalize_metadata_component(album),
            str(year),
        )
    )


def _load_metadata_entries(playlists_dir: Path) -> dict[str, dict]:
    path = _metadata_path(playlists_dir)
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Failed reading metadata file {path}: {exc}")
        return {}
    if isinstance(raw, dict):
        entries = raw.get("entries", raw)
        if isinstance(entries, dict):
            return entries
    logger.warning(f"Unexpected metadata structure in {path}")
    return {}


def _save_metadata_entries(
    playlists_dir: Path, releases: list[ArtistRelease], dry_run: bool
) -> None:
    if dry_run:
        logger.info("Dry run: not writing metadata JSON")
        return
    entries: dict[str, dict] = {}
    for item in releases:
        key = _metadata_key(item.artist, item.title, item.album, item.year)
        entries[key] = {
            "ReleaseDate": item.release_date.isoformat()
            if isinstance(item.release_date, datetime)
            else "",
            "TrackID": item.track_id,
            "AlbumType": item.album_type or "",
            "IsSingle": item.is_single,
            "Popularity": item.popularity if item.popularity is not None else "",
            "Validated": item.validated,
        }
    payload = {"entries": entries}
    path = _metadata_path(playlists_dir)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
    logger.info(f"Wrote metadata for {len(entries)} tracks to {path}")


def load_existing_new_releases(playlists_dir: Path) -> list[ArtistRelease]:
    path = playlists_dir / "New Releases.csv"
    if not path.exists():
        logger.debug("New Releases.csv not found; starting from empty state")
        return []
    metadata_entries = _load_metadata_entries(playlists_dir)
    try:
        df = pd.read_csv(path)
    except Exception as exc:  # noqa: BLE001
        logger.error(f"Failed reading {path}: {exc}")
        return []
    releases: list[ArtistRelease] = []
    for _, row in df.iterrows():
        artist = str(row.get("Artist", "")).strip()
        title = str(row.get("Title", "")).strip()
        if not artist or not title:
            continue
        album = str(row.get("Album", "")).strip()
        year_raw = str(row.get("Year", "")).strip()
        try:
            year = int(year_raw)
        except ValueError:
            year = datetime.now(UTC).year
        lookup_key = _metadata_key(artist, title, album, year)
        metadata = metadata_entries.get(lookup_key, {})
        release_dt = datetime.min.replace(tzinfo=UTC)
        release_raw = str(row.get("ReleaseDate", "")).strip()
        if release_raw:
            try:
                release_dt = datetime.fromisoformat(release_raw)
                if release_dt.tzinfo is None:
                    release_dt = release_dt.replace(tzinfo=UTC)
            except ValueError:
                logger.debug(f"Invalid ReleaseDate '{release_raw}' for {artist} - {title}")
        elif isinstance(metadata, dict):
            meta_release_raw = metadata.get("ReleaseDate")
            if isinstance(meta_release_raw, str) and meta_release_raw:
                try:
                    release_dt = datetime.fromisoformat(meta_release_raw)
                    if release_dt.tzinfo is None:
                        release_dt = release_dt.replace(tzinfo=UTC)
                except ValueError:
                    logger.debug(
                        f"Invalid metadata ReleaseDate '{meta_release_raw}' for {artist} - {title}"
                    )
        track_id = str(row.get("TrackID", "")).strip()
        if not track_id and isinstance(metadata, dict):
            track_id = str(metadata.get("TrackID", "")).strip()
        popularity_val = row.get("Popularity")
        if pd.isna(popularity_val) and isinstance(metadata, dict):
            popularity_val = metadata.get("Popularity")
        popularity = None
        if popularity_val not in (None, ""):
            try:
                popularity = int(popularity_val)
            except (TypeError, ValueError):
                popularity = None
        album_type_val = row.get("AlbumType")
        if pd.isna(album_type_val) or not str(album_type_val).strip():
            album_type_val = metadata.get("AlbumType") if isinstance(metadata, dict) else ""
        album_type = str(album_type_val).strip() or None
        is_single_source = row.get("IsSingle", False)
        if isinstance(metadata, dict) and not is_single_source:
            is_single_source = metadata.get("IsSingle", False)
        is_single = _coerce_bool(is_single_source)
        validated_source = row.get("Validated", False)
        if isinstance(metadata, dict) and not validated_source:
            validated_source = metadata.get("Validated", False)
        validated = _coerce_bool(validated_source)
        releases.append(
            ArtistRelease(
                artist=artist,
                title=title,
                year=year,
                album=album,
                release_date=release_dt,
                track_id=track_id,
                popularity=popularity,
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


def _resolve_destination_playlist(
    release: ArtistRelease, artist_playlist_map: dict[str, dict[Path, set[str]]]
) -> Optional[Path]:
    candidates = artist_playlist_map.get(release.artist)
    if not candidates:
        return None
    title_key = release.title.casefold()
    for path, titles in candidates.items():
        if any((title or "").casefold() == title_key for title in titles):
            return path
    return sorted(candidates.keys())[0]


def _append_release_to_playlist(csv_path: Path, release: ArtistRelease) -> None:
    try:
        df = pd.read_csv(csv_path)
    except Exception as exc:  # noqa: BLE001
        logger.error(f"Failed reading {csv_path}: {exc}")
        return
    if {"Artist", "Title"}.issubset(df.columns):
        duplicate = (
            df["Artist"].fillna("").str.strip().str.casefold() == release.artist.casefold()
        ) & (df["Title"].fillna("").str.strip().str.casefold() == release.title.casefold())
        if duplicate.any():
            logger.debug(
                f"Track already present in {csv_path.name}: {release.artist} - {release.title}"
            )
            return
    row = {}
    for column in df.columns:
        match column:
            case "Artist":
                row[column] = release.artist
            case "Title":
                row[column] = release.title
            case "Year":
                row[column] = str(release.year)
            case "Album":
                row[column] = release.album
            case "Validated":
                row[column] = release.validated
            case _:
                row[column] = ""
    if "Artist" not in row:
        row["Artist"] = release.artist
    if "Title" not in row:
        row["Title"] = release.title
    appended = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
    appended.to_csv(csv_path, index=False)
    logger.debug(f"Appended '{release.title}' to {csv_path.name}")


def _move_track_audio(
    audio_root: Optional[Path],
    source_dir_name: str,
    destination_dir_name: str,
    release: ArtistRelease,
) -> None:
    if not audio_root:
        return
    src_dir = audio_root / source_dir_name
    if not src_dir.exists():
        logger.debug(f"Audio source directory missing: {src_dir}")
        return
    dest_dir = audio_root / destination_dir_name
    dest_dir.mkdir(parents=True, exist_ok=True)
    target_key = _normalize_audio_label(release.artist, release.title)
    for candidate in src_dir.iterdir():
        if not candidate.is_file():
            continue
        if candidate.suffix.lower() not in {".mp3", ".flac", ".wav"}:
            continue
        candidate_key = _normalize_audio_label(candidate.stem)
        if candidate_key == target_key or target_key in candidate_key:
            dest_path = dest_dir / candidate.name
            candidate.replace(dest_path)
            logger.debug(f"Moved {candidate.name} to {dest_dir}")
            return
    logger.debug(f"No audio found for {release.artist} - {release.title} in {src_dir}")


def move_outdated_releases(
    releases: list[ArtistRelease],
    artist_playlist_map: dict[str, dict[Path, set[str]]],
    audio_root: Optional[Path],
    new_releases_dir_name: str,
) -> None:
    if not releases:
        return
    logger.info(f"Archiving {len(releases)} expired tracks from New Releases")
    for release in releases:
        destination = _resolve_destination_playlist(release, artist_playlist_map)
        if not destination:
            logger.warning(f"No destination playlist for {release.artist} - {release.title}")
            continue
        _append_release_to_playlist(destination, release)
        _move_track_audio(audio_root, new_releases_dir_name, destination.stem, release)


def fetch_recent_releases(
    sp: Spotify, artist_name: str, cutoff: datetime, known_titles: Optional[set[str]] = None
) -> list[ArtistRelease]:
    logger.debug(f"Fetching recent releases for artist: {artist_name}")
    artist = _best_artist_match(sp, artist_name, known_titles)
    if not artist:
        logger.debug(f"No artist found for {artist_name}")
        return []

    artist_id = artist.get("id")
    logger.debug(f"Artist ID for '{artist_name}': {artist_id}")
    if not artist_id:
        logger.debug(f"No artist ID found for {artist_name}")
        return []

    candidates: list[ArtistRelease] = []
    for release_date, album in _iter_recent_albums(sp, artist_id, cutoff):
        album_id = album.get("id")
        if not album_id:
            continue
        tracks = _album_tracks_by_artist(sp, album_id, artist_id)
        if not tracks:
            continue

        # Choose the first track in album order to represent the release (skip "Live")
        tracks.sort(key=lambda t: (t.get("disc_number", 0), t.get("track_number", 0)))
        track = tracks[0]
        title = track.get("name", "").strip()
        album_name = album.get("name", "").strip()
        # Skip non-new variants by robust patterns
        if _is_alt_or_reissue(title, album_name):
            continue
        if not title or not album_name:
            continue
        track_id = track.get("id")
        if not track_id:
            continue

        album_type = (album.get("album_group") or album.get("album_type") or "").lower()
        is_single = album_type == "single"

        candidates.append(
            ArtistRelease(
                artist=artist_name,
                title=title,
                year=release_date.year,
                album=album_name,
                release_date=release_date,
                track_id=track_id,
                album_type=album_type,
                is_single=is_single,
            )
        )

    # Sort newest first (popularity annotation/ranking happens later)
    candidates.sort(key=lambda item: item.release_date, reverse=True)
    if not candidates:
        logger.debug(f"No recent releases found for {artist_name}")
    else:
        logger.debug(f"Found {len(candidates)} candidates for {artist_name}")
    return candidates


def build_new_releases(
    sp: Spotify,
    artists: Iterable[str],
    days: int,
    per_artist: int = 1,
    min_popularity: int = 0,
    prefer_singles: bool = False,
    known_tracks: Optional[dict[str, set[str]]] = None,
    cutoff: Optional[datetime] = None,
    seen_tracks: Optional[Set[str]] = None,
    seen_keys: Optional[Set[str]] = None,
) -> list[ArtistRelease]:
    cutoff = cutoff or datetime.now(UTC) - timedelta(days=days)
    releases: list[ArtistRelease] = []
    seen_track_ids: Set[str] = set(seen_tracks or set())
    seen_title_keys: Set[str] = set(seen_keys or set())
    artists_list = list(artists)
    logger.debug(f"Building new releases for {len(artists_list)} artists with cutoff {cutoff}")

    for idx, artist in enumerate(
        tqdm(artists_list, desc="Artists", unit="artist", disable=not sys.stdout.isatty()),
        start=1,
    ):
        artist_titles = (known_tracks or {}).get(artist, set())
        candidates = fetch_recent_releases(sp, artist, cutoff, artist_titles)
        if not candidates:
            continue
        _annotate_popularity(sp, candidates)
        filtered = [c for c in candidates if (c.popularity or 0) >= min_popularity]
        if not filtered:
            logger.debug(f"No candidates passed min_popularity for {artist}")
            continue

        def rank_key(r: ArtistRelease):
            single_score = 1 if (prefer_singles and r.is_single) else 0
            return (single_score, r.popularity or 0, r.release_date)

        filtered.sort(key=rank_key, reverse=True)

        kept = 0
        for cand in filtered:
            if cand.track_id and cand.track_id in seen_track_ids:
                continue
            title_key = _normalize_audio_label(cand.artist, cand.title)
            if title_key in seen_title_keys:
                continue
            releases.append(cand)
            seen_title_keys.add(title_key)
            if cand.track_id:
                seen_track_ids.add(cand.track_id)
            kept += 1
            if kept >= per_artist:
                break

    releases.sort(key=lambda item: (item.release_date, item.popularity or 0), reverse=True)
    logger.debug(f"Total new releases collected this run: {len(releases)}")
    return releases


def save_new_releases(
    playlists_dir: Path, releases: list[ArtistRelease], dry_run: bool
) -> None:
    output_path = playlists_dir / "New Releases.csv"
    logger.debug(f"Saving new releases to {output_path}, dry_run={dry_run}")
    if not releases:
        logger.info("No new releases to write.")
        print("No new releases to write.", file=sys.stderr)
        return
    sorted_releases = sorted(
        releases, key=lambda item: (item.release_date, item.popularity or 0), reverse=True
    )
    csv_rows: list[dict[str, str]] = []
    preview_rows: list[dict[str, object]] = []
    for item in sorted_releases:
        csv_rows.append(
            {
                "Artist": item.artist,
                "Title": item.title,
                "Album": item.album,
                "Year": str(item.year),
            }
        )
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
                "Popularity": item.popularity if item.popularity is not None else "",
                "Validated": item.validated,
            }
        )
    df_preview = pd.DataFrame(preview_rows)
    logger.debug(f"Preview DataFrame to be written:\n{df_preview}")
    if dry_run:
        logger.info("Dry run: not writing CSV")
        print("Dry run: not writing CSV", file=sys.stderr)
        if not df_preview.empty:
            print(df_preview.to_string(index=False), flush=True)
        return
    df_csv = pd.DataFrame(csv_rows)
    df_csv.to_csv(output_path, index=False)
    _save_metadata_entries(playlists_dir, sorted_releases, dry_run)
    logger.info(f"Wrote {len(df_csv)} tracks to {output_path}")
    print(f"Wrote {len(df_csv)} tracks to {output_path}", flush=True)


def build_spotify_client() -> Spotify:
    logger.debug("Loading Spotify credentials from .env")
    load_dotenv(dotenv_path=".env", override=False)
    cid = os.getenv("SPOTIFY_CLIENT_ID")
    secret = os.getenv("SPOTIFY_CLIENT_SECRET")
    logger.debug(
        f"SPOTIFY_CLIENT_ID: {cid}, SPOTIFY_CLIENT_SECRET: {'set' if secret else 'missing'}"
    )
    if not cid or not secret:
        logger.error(
            "Spotify credentials missing. Set SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET."
        )
        raise SystemExit(
            "Spotify credentials missing. Set SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET."
        )
    auth = SpotifyClientCredentials(client_id=cid, client_secret=secret)
    logger.debug("Spotify client initialized")
    return Spotify(auth_manager=auth)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Refresh the New Releases playlist for a station."
    )
    parser.add_argument(
        "station",
        metavar="STATION",
        help="Station directory name (e.g., NeuralForge or NeuralCast)",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=120,
        help="Lookback window in days for releases (default: 120)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Collect and display results without writing the CSV",
    )
    # New knobs for multi-track selection and ranking
    parser.add_argument(
        "--per-artist",
        type=int,
        default=3,
        help="Max tracks to keep per artist (default: 3)",
    )
    parser.add_argument(
        "--min-popularity",
        type=int,
        default=0,
        help="Minimum Spotify popularity (0-100) to keep (default: 0)",
    )
    parser.add_argument(
        "--prefer-singles",
        action="store_true",
        help="Prefer singles when ranking candidates (default: off)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    logger.info(f"Starting update for station: {args.station}")
    station_dir = Path(args.station)
    playlists_dir = station_dir / "playlists"
    logger.debug(
        f"Station directory: {station_dir}, Playlists directory: {playlists_dir}"
    )
    if not playlists_dir.exists():
        logger.error(f"Playlists directory not found: {playlists_dir}")
        raise SystemExit(f"Playlists directory not found: {playlists_dir}")

    artists, artist_tracks, artist_playlist_map = load_station_artists(playlists_dir)
    logger.info(f"Loaded {len(artists)} artists from {playlists_dir}")

    # Always use [station]/songs as audio root, and playlist name as subdirectory
    audio_root = station_dir / "songs"
    if not audio_root.exists():
        logger.debug(f"Audio root not found; skipping audio moves: {audio_root}")
        audio_root = None

    cutoff = datetime.now(UTC) - timedelta(days=args.days)
    existing_releases = load_existing_new_releases(playlists_dir)
    valid_existing, outdated_existing = partition_releases_by_cutoff(existing_releases, cutoff)
    existing_ids = {r.track_id for r in valid_existing if r.track_id}
    existing_keys = {_normalize_audio_label(r.artist, r.title) for r in valid_existing}

    sp = build_spotify_client()
    new_releases = build_new_releases(
        sp,
        artists,
        days=args.days,
        per_artist=args.per_artist,
        min_popularity=args.min_popularity,
        prefer_singles=args.prefer_singles,
        known_tracks=artist_tracks,
        cutoff=cutoff,
        seen_tracks=existing_ids,
        seen_keys=existing_keys,
    )

    combined = valid_existing + new_releases
    combined.sort(key=lambda item: (item.release_date, item.popularity or 0), reverse=True)
    final_releases: list[ArtistRelease] = []
    seen_ids_final: set[str] = set()
    seen_keys_final: set[str] = set()
    for release in combined:
        title_key = _normalize_audio_label(release.artist, release.title)
        if (
            release.track_id and release.track_id in seen_ids_final
        ) or title_key in seen_keys_final:
            continue
        final_releases.append(release)
        seen_keys_final.add(title_key)
        if release.track_id:
            seen_ids_final.add(release.track_id)

    if outdated_existing:
        move_outdated_releases(
            outdated_existing,
            artist_playlist_map,
            audio_root,
            "New Releases",  # always use this for the source dir
        )

    if final_releases:
        logger.info(f"Collected {len(final_releases)} recent tracks")
        print(f"Collected {len(final_releases)} recent tracks", flush=True)
    else:
        logger.info("No releases found within the window")
        print("No releases found within the window", flush=True)
    save_new_releases(playlists_dir, final_releases, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
