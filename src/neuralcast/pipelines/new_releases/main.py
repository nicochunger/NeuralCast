"""Update station's New Releases playlist with latest tracks."""

from __future__ import annotations

import argparse
import musicbrainzngs
import os
import re
import sys
import time
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from difflib import SequenceMatcher
from pathlib import Path
from typing import Iterable, Optional, Set

import pandas as pd
import requests
from mutagen.easyid3 import EasyID3
from tqdm import tqdm

from neuralcast.audio.download import tag_mp3
from neuralcast.config import (
    ALLOWED_STATION_SLUGS,
    DEFAULT_STATION_SLUG,
    station_dir_from_slug,
)
from neuralcast.metadata.album_lookup import guess_album
from neuralcast.metadata.storage import (
    load_station_json_dict,
    metadata_key,
    normalize_metadata_component,
    save_station_json_dict,
)
from neuralcast.metadata.track_resolution import (
    CallableAlbumResolutionPort,
    CallableTrackValidationPort,
    ResolutionMode,
    TrackMetadataResolver,
    TrackResolutionRequest,
)
from neuralcast.models import Song
from neuralcast.playlists.catalog import (
    CatalogTrack,
    CatalogWritePolicy,
    StationPlaylistCatalog,
)
from neuralcast.playlists.utils import sanitize_filename_component
from neuralcast.services.validation import verified, verified_album

_DEBUG_ENABLED = False
_PLAYLIST_FILENAME = "New Releases.csv"
_METADATA_FILENAME = "New Releases.metadata.json"
_ARTIST_CACHE_FILENAME = "ArtistIDs.json"
_EXCLUDED_PLAYLIST_FILENAMES = {"new releases.csv"}
_KNOWN_TRACK_SAMPLE_SIZE = 8
_KNOWN_ALBUM_SAMPLE_SIZE = 3
_KNOWN_IDENTITY_MATCH_SAMPLE_SIZE = 3
_REQUEST_TIMEOUT = 15
_API_BASE_URL = "https://api.deezer.com"
_MAX_API_RETRIES = 3
_MAX_PAGES = 20
_ALLOWED_RECORD_TYPES = {"album", "single"}
_MIN_REQUEST_INTERVAL_SECONDS = 0.25
_QUOTA_BACKOFF_SECONDS = (5, 15, 30)

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "NeuralCast/1.0"})
_LAST_REQUEST_TS = 0.0
_QUOTA_PAUSE_UNTIL = 0.0
_MB_RELEASE_CACHE: dict[tuple[str, str, str], Optional[datetime]] = {}
_KNOWN_TRACK_MATCH_CACHE: dict[
    tuple[str, str, tuple[str, ...]], Optional[list[dict]]
] = {}
_ALBUM_GENRE_CACHE: dict[str, Optional[frozenset[int]]] = {}
_MB_RECORDING_ARTIST_CACHE: dict[
    tuple[str, str], Optional[frozenset[str]]
] = {}

# Deezer sometimes groups distinct same-named artists under one artist ID. Genre
# evidence from known station tracks gives us a release-level identity check. The
# rock family is intentionally broad because Deezer moves metal releases between
# Rock, Alternative, Indie Rock, and Heavy Metal.
_COMPATIBLE_GENRE_FAMILIES = (
    frozenset({85, 87, 152, 464}),
    frozenset({106, 113}),
)

musicbrainzngs.set_useragent("NeuralCast", "0.1", "neuralcast@example.com")


def _env_flag(value: str | None) -> bool:
    normalized = (value or "").strip().lower()
    return normalized in {"1", "true", "yes", "on"}


def set_debug_mode(enabled: bool) -> None:
    global _DEBUG_ENABLED
    _DEBUG_ENABLED = enabled or _env_flag(os.getenv("NC_DEBUG"))


def _emit(icon: str, message: str) -> None:
    print(f"{icon} {message}")


def log_info(message: str) -> None:
    _emit("💡", message)


def log_success(message: str) -> None:
    _emit("✅", message)


def log_warning(message: str) -> None:
    _emit("⚠️", message)


def log_error(message: str) -> None:
    _emit("❌", message)


def log_debug(message: str) -> None:
    if _DEBUG_ENABLED:
        _emit("⋯", message)


set_debug_mode(False)


def _normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value or "")
    collapsed = re.sub(r"\s+", " ", normalized).strip()
    return normalize_metadata_component(collapsed)


def _normalize_artist_match_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value or "")
    stripped = "".join(char for char in normalized if not unicodedata.combining(char))
    collapsed = re.sub(r"\s+", " ", stripped).strip()
    return normalize_metadata_component(collapsed)


def _artist_names_match(a: str, b: str) -> bool:
    return _normalize_artist_match_key(a) == _normalize_artist_match_key(b)


def _normalize_audio_label(*parts: str) -> str:
    text = " ".join(part or "" for part in parts)
    normalized = unicodedata.normalize("NFKD", text)
    return re.sub(r"[^a-z0-9]", "", normalized.casefold())


def _normalize_track_match_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value or "")
    stripped = "".join(char for char in normalized if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]", "", stripped.casefold())


def _track_titles_match(candidate: str, expected: str) -> bool:
    candidate_key = _normalize_track_match_key(candidate)
    expected_key = _normalize_track_match_key(expected)
    if not candidate_key or not expected_key:
        return False
    if candidate_key == expected_key:
        return True
    return (
        min(len(candidate_key), len(expected_key)) >= 8
        and _ratio(candidate_key, expected_key) >= 0.9
    )


def _normalize_musicbrainz_label(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value or "")
    stripped = "".join(char for char in normalized if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]", "", stripped.casefold())


def _musicbrainz_recording_artist_ids(
    artist_name: str, title: str
) -> Optional[frozenset[str]]:
    cache_key = (
        _normalize_artist_match_key(artist_name),
        _normalize_musicbrainz_label(title),
    )
    if cache_key in _MB_RECORDING_ARTIST_CACHE:
        return _MB_RECORDING_ARTIST_CACHE[cache_key]
    try:
        response = musicbrainzngs.search_recordings(
            artist=artist_name,
            recording=title,
            limit=25,
        )
    except Exception:
        _MB_RECORDING_ARTIST_CACHE[cache_key] = None
        return None

    expected_title = _normalize_musicbrainz_label(title)
    artist_ids: set[str] = set()
    for item in response.get("recording-list", []) or []:
        if not isinstance(item, dict):
            continue
        if _normalize_musicbrainz_label(str(item.get("title") or "")) != expected_title:
            continue
        for credit in item.get("artist-credit", []) or []:
            if not isinstance(credit, dict):
                continue
            artist = credit.get("artist") if isinstance(credit.get("artist"), dict) else {}
            candidate_name = str(artist.get("name") or "")
            artist_id = str(artist.get("id") or "").strip()
            if artist_id and _artist_names_match(candidate_name, artist_name):
                artist_ids.add(artist_id)

    result = frozenset(artist_ids)
    _MB_RECORDING_ARTIST_CACHE[cache_key] = result
    return result


def _known_musicbrainz_artist_ids(
    artist_name: str, known_titles: Optional[set[str]]
) -> frozenset[str]:
    support: dict[str, int] = {}
    for title in sorted(title for title in (known_titles or set()) if title)[
        :_KNOWN_TRACK_SAMPLE_SIZE
    ]:
        artist_ids = _musicbrainz_recording_artist_ids(artist_name, title)
        for artist_id in artist_ids or frozenset():
            support[artist_id] = support.get(artist_id, 0) + 1
    if not support:
        return frozenset()
    strongest_support = max(support.values())
    return frozenset(
        artist_id
        for artist_id, count in support.items()
        if count == strongest_support
    )


def _normalize_metadata_component(value: str) -> str:
    return normalize_metadata_component(value)


def _metadata_key(artist: str, title: str, album: str, year: int) -> str:
    return metadata_key(artist, title, album, year)


def _ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, (a or "").casefold(), (b or "").casefold()).ratio()


def _close_enough(a: str, b: str, minimum: float = 0.72) -> bool:
    return _ratio(a, b) >= minimum


@dataclass
class ArtistIDCache:
    entries: dict[str, str]
    dirty: bool = False

    def get(self, artist_name: str) -> Optional[str]:
        return self.entries.get(_normalize_text(artist_name))

    def set(self, artist_name: str, artist_id: str) -> None:
        artist_id = str(artist_id or "").strip()
        if not artist_id:
            return
        key = _normalize_text(artist_name)
        if self.entries.get(key) == artist_id:
            return
        self.entries[key] = artist_id
        self.dirty = True

    def remove(self, artist_name: str) -> None:
        key = _normalize_text(artist_name)
        if key in self.entries:
            del self.entries[key]
            self.dirty = True


@dataclass
class ArtistRelease:
    artist: str
    title: str
    year: int
    album: str
    release_date: datetime
    track_id: str
    rank: Optional[int] = None
    is_single: bool = False
    album_type: Optional[str] = None
    validated: bool = False


def load_artist_id_cache(playlists_dir: Path) -> ArtistIDCache:
    payload, _resolved = load_station_json_dict(
        playlists_dir,
        _ARTIST_CACHE_FILENAME,
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
    path = save_station_json_dict(playlists_dir, _ARTIST_CACHE_FILENAME, cache.entries)
    log_success(f"Cached {len(cache.entries)} artist IDs → {path}")


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


def parse_release_date(date_str: str) -> Optional[datetime]:
    value = (date_str or "").strip()
    if not value:
        return None
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=UTC)
        if re.fullmatch(r"\d{4}-\d{2}", value):
            return datetime.strptime(value, "%Y-%m").replace(day=1, tzinfo=UTC)
        if re.fullmatch(r"\d{4}", value):
            return datetime.strptime(value, "%Y").replace(month=1, day=1, tzinfo=UTC)
    except ValueError:
        return None
    return None


def _deezer_get(
    resource: str, *, params: Optional[dict[str, object]] = None
) -> Optional[dict]:
    global _LAST_REQUEST_TS
    global _QUOTA_PAUSE_UNTIL

    url = resource if resource.startswith("http") else f"{_API_BASE_URL}{resource}"
    for attempt in range(1, _MAX_API_RETRIES + 1):
        now = time.monotonic()
        if _QUOTA_PAUSE_UNTIL > now:
            sleep_for = _QUOTA_PAUSE_UNTIL - now
            log_warning(
                f"Deezer quota cooldown active, sleeping {sleep_for:.1f}s before retrying"
            )
            time.sleep(sleep_for)

        now = time.monotonic()
        elapsed = now - _LAST_REQUEST_TS
        if elapsed < _MIN_REQUEST_INTERVAL_SECONDS:
            time.sleep(_MIN_REQUEST_INTERVAL_SECONDS - elapsed)

        try:
            response = SESSION.get(url, params=params, timeout=_REQUEST_TIMEOUT)
            _LAST_REQUEST_TS = time.monotonic()
            if response.status_code == 429:
                retry_after = int(response.headers.get("Retry-After", "3"))
                log_warning(
                    f"Rate limited by Deezer, sleeping {retry_after}s (attempt {attempt}/{_MAX_API_RETRIES})"
                )
                time.sleep(retry_after)
                continue
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException as exc:
            if attempt >= _MAX_API_RETRIES:
                log_warning(f"Deezer request failed for {url}: {exc}")
                return None
            time.sleep(attempt)
            continue
        except ValueError as exc:
            log_warning(f"Invalid Deezer JSON for {url}: {exc}")
            return None

        if isinstance(payload, dict) and payload.get("error"):
            error = payload["error"]
            error_code = str(error.get("code", "")).strip()
            if error_code == "4":
                backoff = _QUOTA_BACKOFF_SECONDS[min(attempt - 1, len(_QUOTA_BACKOFF_SECONDS) - 1)]
                _QUOTA_PAUSE_UNTIL = max(_QUOTA_PAUSE_UNTIL, time.monotonic() + backoff)
                if attempt < _MAX_API_RETRIES:
                    log_warning(
                        f"Deezer quota limit hit for {url}; backing off {backoff}s "
                        f"(attempt {attempt}/{_MAX_API_RETRIES})"
                    )
                    continue
            log_warning(f"Deezer API error for {url}: {error}")
            return None
        if not isinstance(payload, dict):
            log_warning(f"Unexpected Deezer payload for {url}")
            return None
        return payload
    return None


def _paginate_deezer(
    resource: str, *, params: Optional[dict[str, object]] = None
) -> list[dict]:
    items: list[dict] = []
    next_resource = resource
    next_params = params
    page_count = 0
    while next_resource and page_count < _MAX_PAGES:
        payload = _deezer_get(next_resource, params=next_params)
        if not payload:
            break
        page_items = payload.get("data")
        if isinstance(page_items, list):
            items.extend(item for item in page_items if isinstance(item, dict))
        next_url = payload.get("next")
        if not isinstance(next_url, str) or not next_url or next_url == next_resource:
            break
        next_resource = next_url
        next_params = None
        page_count += 1
    return items


def _fetch_artist_by_id(artist_id: str) -> Optional[dict]:
    artist_id = str(artist_id or "").strip()
    if not artist_id:
        return None
    payload = _deezer_get(f"/artist/{artist_id}")
    if not payload:
        return None
    if not payload.get("id") or not payload.get("name"):
        return None
    return payload


def _track_matches_artist_id(track: dict, artist_id: str) -> bool:
    artist = track.get("artist") if isinstance(track.get("artist"), dict) else {}
    return bool(artist_id) and str(artist.get("id") or "") == str(artist_id)


def _is_track_currently_available(track: dict) -> bool:
    readable = track.get("readable")
    if isinstance(readable, bool):
        return readable
    if isinstance(readable, (int, float)):
        return bool(readable)
    if isinstance(readable, str):
        return readable.strip().lower() in {"1", "true", "yes", "y"}
    return False


def _known_track_cache_key(
    artist_id: str, artist_name: str, known_titles: set[str]
) -> tuple[str, str, tuple[str, ...]]:
    normalized_titles = {
        normalized_title
        for title in known_titles
        if (normalized_title := _normalize_track_match_key(title))
    }
    title_keys = tuple(sorted(normalized_titles))
    return str(artist_id), _normalize_artist_match_key(artist_name), title_keys


def _find_known_artist_tracks(
    artist_id: str, artist_name: str, known_titles: Optional[set[str]]
) -> Optional[list[dict]]:
    if not artist_id or not known_titles:
        return []
    cache_key = _known_track_cache_key(artist_id, artist_name, known_titles)
    if cache_key in _KNOWN_TRACK_MATCH_CACHE:
        cached = _KNOWN_TRACK_MATCH_CACHE[cache_key]
        return list(cached) if cached is not None else None

    matches: list[dict] = []
    seen_track_ids: set[str] = set()
    seen_album_ids: set[str] = set()
    received_response = False
    for title in sorted(title for title in known_titles if title)[:_KNOWN_TRACK_SAMPLE_SIZE]:
        for query in (f'artist:"{artist_name}" track:"{title}"', f"{artist_name} {title}"):
            payload = _deezer_get("/search/track", params={"q": query, "limit": 5})
            if payload is None:
                continue
            received_response = True
            query_matched = False
            for track in payload.get("data", []):
                if not isinstance(track, dict):
                    continue
                if not _track_matches_artist_id(track, artist_id):
                    continue
                track_titles = (
                    str(track.get("title_short") or ""),
                    str(track.get("title") or ""),
                )
                if not any(_track_titles_match(value, title) for value in track_titles):
                    continue
                track_id = str(track.get("id") or "").strip()
                if track_id and track_id in seen_track_ids:
                    query_matched = True
                    continue
                matches.append(track)
                if track_id:
                    seen_track_ids.add(track_id)
                album = track.get("album") if isinstance(track.get("album"), dict) else {}
                album_id = str(album.get("id") or "").strip()
                if album_id:
                    seen_album_ids.add(album_id)
                query_matched = True
                break
            if query_matched:
                break
        if (
            len(matches) >= _KNOWN_IDENTITY_MATCH_SAMPLE_SIZE
            or len(seen_album_ids) >= _KNOWN_ALBUM_SAMPLE_SIZE
        ):
            break

    if not received_response:
        _KNOWN_TRACK_MATCH_CACHE[cache_key] = None
        return None
    _KNOWN_TRACK_MATCH_CACHE[cache_key] = list(matches)
    return matches


def _artist_has_known_track(
    artist_id: str, artist_name: str, known_titles: Optional[set[str]]
) -> Optional[bool]:
    matches = _find_known_artist_tracks(artist_id, artist_name, known_titles)
    return bool(matches) if matches is not None else None


def _genre_ids_from_album(album: dict) -> frozenset[int]:
    genre_ids: set[int] = set()
    values: list[object] = [album.get("genre_id")]
    genres = album.get("genres") if isinstance(album.get("genres"), dict) else {}
    for item in genres.get("data", []) or []:
        if isinstance(item, dict):
            values.append(item.get("id"))
    for value in values:
        try:
            genre_id = int(value)
        except (TypeError, ValueError):
            continue
        if genre_id > 0:
            genre_ids.add(genre_id)
    return frozenset(genre_ids)


def _album_genre_ids(album_id: str) -> Optional[frozenset[int]]:
    album_id = str(album_id or "").strip()
    if not album_id:
        return frozenset()
    if album_id in _ALBUM_GENRE_CACHE:
        return _ALBUM_GENRE_CACHE[album_id]
    payload = _deezer_get(f"/album/{album_id}")
    if not payload:
        _ALBUM_GENRE_CACHE[album_id] = None
        return None
    genre_ids = _genre_ids_from_album(payload)
    _ALBUM_GENRE_CACHE[album_id] = genre_ids
    return genre_ids


def _known_artist_genre_ids(
    artist_id: str, artist_name: str, known_titles: Optional[set[str]]
) -> frozenset[int]:
    album_match_counts: dict[str, int] = {}
    for track in _find_known_artist_tracks(artist_id, artist_name, known_titles) or []:
        album = track.get("album") if isinstance(track.get("album"), dict) else {}
        album_id = str(album.get("id") or "").strip()
        if not album_id:
            continue
        album_match_counts[album_id] = album_match_counts.get(album_id, 0) + 1

    if not album_match_counts:
        return frozenset()

    strongest_support = max(album_match_counts.values())
    genre_ids: set[int] = set()
    for album_id, support in album_match_counts.items():
        if support != strongest_support:
            continue
        album_genres = _album_genre_ids(album_id)
        if album_genres:
            genre_ids.update(album_genres)
    return frozenset(genre_ids)


def _known_artist_genres_are_ambiguous(
    artist_id: str, artist_name: str, known_titles: Optional[set[str]]
) -> bool:
    album_genres: list[frozenset[int]] = []
    seen_album_ids: set[str] = set()
    for track in _find_known_artist_tracks(artist_id, artist_name, known_titles) or []:
        album = track.get("album") if isinstance(track.get("album"), dict) else {}
        album_id = str(album.get("id") or "").strip()
        if not album_id or album_id in seen_album_ids:
            continue
        seen_album_ids.add(album_id)
        genres = _album_genre_ids(album_id)
        if genres:
            album_genres.append(genres)

    return any(
        not _genre_sets_are_compatible(left, right)
        for index, left in enumerate(album_genres)
        for right in album_genres[index + 1 :]
    )


def _genre_sets_are_compatible(
    known_genres: frozenset[int], candidate_genres: frozenset[int]
) -> bool:
    if known_genres & candidate_genres:
        return True
    return any(
        bool(known_genres & family) and bool(candidate_genres & family)
        for family in _COMPATIBLE_GENRE_FAMILIES
    )


def _album_matches_known_genres(
    album: dict, known_genres: frozenset[int]
) -> bool:
    if not known_genres:
        return True
    candidate_genres = _genre_ids_from_album(album)
    if candidate_genres and _genre_sets_are_compatible(
        known_genres, candidate_genres
    ):
        return True

    album_id = str(album.get("id") or "").strip()
    detailed_genres = _album_genre_ids(album_id)
    if detailed_genres is None:
        # A provider outage should not make a valid release disappear.
        return True
    return bool(
        detailed_genres
        and _genre_sets_are_compatible(known_genres, detailed_genres)
    )


def _search_artist_using_known_tracks(
    artist_name: str, known_titles: Optional[set[str]]
) -> Optional[dict]:
    if not known_titles:
        return None
    artist_match_key = _normalize_artist_match_key(artist_name)
    for title in sorted(title for title in known_titles if title)[:_KNOWN_TRACK_SAMPLE_SIZE]:
        for query in (f'artist:"{artist_name}" track:"{title}"', f"{artist_name} {title}"):
            payload = _deezer_get("/search/track", params={"q": query, "limit": 5})
            if not payload:
                continue
            for track in payload.get("data", []):
                if not isinstance(track, dict):
                    continue
                artist = track.get("artist") if isinstance(track.get("artist"), dict) else {}
                artist_label = str(artist.get("name") or "")
                if _normalize_artist_match_key(artist_label) != artist_match_key:
                    continue
                if not _track_titles_match(str(track.get("title") or ""), title):
                    continue
                artist_id = str(artist.get("id") or "").strip()
                if not artist_id:
                    continue
                return _fetch_artist_by_id(artist_id) or {
                    "id": artist_id,
                    "name": artist_label,
                }
    return None


def _best_artist_match(
    artist_name: str, known_titles: Optional[set[str]] = None
) -> Optional[dict]:
    exact_matches = _exact_artist_matches(artist_name)
    if not exact_matches:
        return None
    if not known_titles:
        return exact_matches[0]
    verification_unavailable = False
    for candidate in exact_matches:
        candidate_id = str(candidate.get("id") or "").strip()
        matches_known = (
            _artist_has_known_track(candidate_id, artist_name, known_titles)
            if candidate_id
            else False
        )
        if matches_known is True:
            return candidate
        if matches_known is None:
            verification_unavailable = True
    if verification_unavailable:
        log_warning(
            f"Could not verify Deezer artist '{artist_name}' against known tracks; skipping"
        )
        return None
    log_warning(
        f"Deezer artist '{artist_name}' has no exact known-track match; skipping"
    )
    return None


def _exact_artist_matches(artist_name: str) -> list[dict]:
    payload = _deezer_get("/search/artist", params={"q": artist_name, "limit": 10})
    if not payload:
        return []
    items = [item for item in payload.get("data", []) if isinstance(item, dict)]
    target = _normalize_artist_match_key(artist_name)
    return [
        item
        for item in items
        if _normalize_artist_match_key(str(item.get("name") or "")) == target
    ]


def _resolve_artist(
    artist_name: str, known_titles: Optional[set[str]], cache: Optional[ArtistIDCache]
) -> Optional[dict]:
    cached_id = cache.get(artist_name) if cache else None
    if cached_id:
        artist = _fetch_artist_by_id(cached_id)
        if artist:
            artist_label = str(artist.get("name") or "")
            name_matches = _artist_names_match(artist_label, artist_name) or _close_enough(
                artist_label, artist_name, minimum=0.9
            )
            if known_titles:
                cached_matches_known = _artist_has_known_track(
                    cached_id, artist_name, known_titles
                )
                if cached_matches_known is True:
                    log_debug(f"Resolved Deezer artist '{artist_name}' from cache")
                    return artist
                if cached_matches_known is None:
                    if name_matches:
                        log_warning(
                            f"Could not revalidate cached Deezer artist '{artist_name}'; "
                            "using the name-matching cached ID"
                        )
                        return artist
                    log_warning(
                        f"Could not revalidate cached Deezer artist alias '{artist_name}'; "
                        "skipping until Deezer is available"
                    )
                    return None
                log_warning(
                    f"Cached Deezer artist ID for '{artist_name}' does not match "
                    "known tracks; refreshing lookup"
                )
                cache.remove(artist_name)
            elif name_matches:
                log_debug(f"Resolved Deezer artist '{artist_name}' from cache")
                return artist
            else:
                log_warning(
                    f"Cached Deezer artist ID mismatch for '{artist_name}', refreshing lookup"
                )
                cache.remove(artist_name)
        else:
            cache.remove(artist_name)

    artist = _best_artist_match(artist_name, known_titles)
    if artist:
        if cache and artist.get("id"):
            cache.set(artist_name, str(artist["id"]))
        return artist

    artist = _search_artist_using_known_tracks(artist_name, known_titles)
    if artist and cache and artist.get("id"):
        cache.set(artist_name, str(artist["id"]))
    return artist


_TITLE_EXCLUDE_PATTERNS = [
    re.compile(r"\blive\b", re.I),
    re.compile(r"\bmix\b", re.I),
    re.compile(r"\bremaster(?:ed|s|ing)?\b", re.I),
    re.compile(r"\bre[-\s]?record(?:ed|ing)?\b", re.I),
    re.compile(r"\bre[-\s]?imagined\b", re.I),
    re.compile(r"\bredux\b", re.I),
    re.compile(r"\bacoustic\b", re.I),
    re.compile(r"\bdemos?\b", re.I),
    re.compile(r"\binstrumentals?\b", re.I),
    re.compile(r"\bouttakes?\b", re.I),
    re.compile(r"\bunreleased\b", re.I),
    re.compile(r"\bcovers?\b", re.I),
    re.compile(r"\bbonus\s+tracks?\b", re.I),
    re.compile(r"\bsound\s*checks?\b", re.I),
    re.compile(r"\bsessions?\b", re.I),
    re.compile(r"\boriginal\s+drums?\b", re.I),
    re.compile(r"\b(?:19|20)\d{2}\s*\)", re.I),
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
    re.compile(r"\bbonus\s+track\s+edition\b", re.I),
    re.compile(r"\boriginal\s+soundtrack\b", re.I),
    re.compile(r"\bset\s*list\b", re.I),
    re.compile(r"\barchive(?:d|s)?\b", re.I),
]


def _is_alt_or_reissue(title: str, album_name: str) -> bool:
    for pattern in _TITLE_EXCLUDE_PATTERNS:
        if pattern.search(title or ""):
            return True
    for pattern in _ALBUM_EXCLUDE_PATTERNS:
        if pattern.search(album_name or ""):
            return True
    return False


def _is_known_catalog_title(title: str, known_titles: Optional[set[str]]) -> bool:
    title_key = _normalize_track_match_key(title)
    return bool(
        title_key
        and known_titles
        and any(
            title_key == _normalize_track_match_key(known_title)
            for known_title in known_titles
        )
    )


def _parse_musicbrainz_date(date_str: str | None) -> Optional[datetime]:
    value = (date_str or "").strip()
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m", "%Y"):
        try:
            parsed = datetime.strptime(value, fmt)
            if fmt == "%Y":
                parsed = parsed.replace(month=1, day=1)
            elif fmt == "%Y-%m":
                parsed = parsed.replace(day=1)
            return parsed.replace(tzinfo=UTC)
        except ValueError:
            continue
    return None


def _musicbrainz_item_matches_artist(item: dict, artist_name: str, *, entity: str) -> bool:
    target = _normalize_text(artist_name)
    if not target:
        return False
    if entity == "release":
        artist_credits = item.get("artist-credit", []) or []
        for credit in artist_credits:
            if not isinstance(credit, dict):
                continue
            candidate = str((credit.get("artist") or {}).get("name") or "").strip()
            if candidate and _close_enough(candidate, artist_name, minimum=0.9):
                return True
        artist_phrase = str(item.get("artist-credit-phrase") or "").strip()
        return bool(artist_phrase and _close_enough(artist_phrase, artist_name, minimum=0.9))

    artist_credits = item.get("artist-credit", []) or []
    for credit in artist_credits:
        if not isinstance(credit, dict):
            continue
        candidate = str((credit.get("artist") or {}).get("name") or "").strip()
        if candidate and _close_enough(candidate, artist_name, minimum=0.9):
            return True
    artist_phrase = str(item.get("artist-credit-phrase") or "").strip()
    return bool(artist_phrase and _close_enough(artist_phrase, artist_name, minimum=0.9))


def _musicbrainz_earliest_release_date(
    artist_name: str, value: str, *, entity: str
) -> Optional[datetime]:
    artist_name = str(artist_name or "").strip()
    value = str(value or "").strip()
    if not artist_name or not value:
        return None
    cache_key = (_normalize_text(artist_name), entity, _normalize_musicbrainz_label(value))
    if cache_key in _MB_RELEASE_CACHE:
        return _MB_RELEASE_CACHE[cache_key]

    normalized_value = _normalize_musicbrainz_label(value)
    search = (
        musicbrainzngs.search_releases
        if entity == "release"
        else musicbrainzngs.search_recordings
    )
    key = "release-list" if entity == "release" else "recording-list"

    try:
        response = search(artist=artist_name, **{entity: value}, limit=25)
    except Exception:
        _MB_RELEASE_CACHE[cache_key] = None
        return None

    earliest: Optional[datetime] = None
    for item in response.get(key, []) or []:
        if not isinstance(item, dict):
            continue
        if not _musicbrainz_item_matches_artist(item, artist_name, entity=entity):
            continue
        title_key = "title" if entity == "release" else "title"
        item_value = str(item.get(title_key) or "").strip()
        if _normalize_musicbrainz_label(item_value) != normalized_value:
            continue

        if entity == "release":
            release_dates = [_parse_musicbrainz_date(item.get("date"))]
        else:
            release_dates = [
                _parse_musicbrainz_date(release.get("date"))
                for release in item.get("release-list", []) or []
                if isinstance(release, dict)
            ]

        for release_date in release_dates:
            if not release_date:
                continue
            if earliest is None or release_date < earliest:
                earliest = release_date

    _MB_RELEASE_CACHE[cache_key] = earliest
    return earliest


def _is_probable_old_catalog_release(
    artist_name: str, title: str, album_name: str, release_date: datetime
) -> bool:
    cutoff = timedelta(days=365 * 2)
    checks = (
        ("album", album_name, _musicbrainz_earliest_release_date(artist_name, album_name, entity="release")),
        ("recording", title, _musicbrainz_earliest_release_date(artist_name, title, entity="recording")),
    )
    for label, value, earliest_release in checks:
        if not earliest_release or release_date - earliest_release <= cutoff:
            continue
        log_debug(
            f"Skipping probable old catalog release for {artist_name} - {title}: "
            f"MusicBrainz {label} '{value}' dates back to {earliest_release.date().isoformat()}"
        )
        return True
    return False


def _iter_recent_albums(artist_id: str, cutoff: datetime) -> list[tuple[datetime, dict]]:
    seen_ids: set[str] = set()
    albums: list[tuple[datetime, dict]] = []
    for album in _paginate_deezer(f"/artist/{artist_id}/albums", params={"limit": 100}):
        album_id = str(album.get("id") or "").strip()
        if not album_id or album_id in seen_ids:
            continue
        seen_ids.add(album_id)
        record_type = str(album.get("record_type") or "").strip().lower()
        if record_type not in _ALLOWED_RECORD_TYPES:
            continue
        release_date = parse_release_date(str(album.get("release_date") or ""))
        if not release_date or release_date < cutoff:
            continue
        albums.append((release_date, album))
    albums.sort(key=lambda item: item[0], reverse=True)
    return albums


def _album_tracks_by_artist(album_id: str, artist_id: str) -> list[dict]:
    tracks: list[dict] = []
    for track in _paginate_deezer(f"/album/{album_id}/tracks", params={"limit": 100}):
        if _track_matches_artist_id(track, artist_id):
            tracks.append(track)
    tracks.sort(
        key=lambda item: (
            int(item.get("disk_number") or 0),
            int(item.get("track_position") or 0),
        )
    )
    return tracks


def fetch_recent_releases(
    artist_name: str,
    cutoff: datetime,
    known_titles: Optional[set[str]] = None,
    artist_cache: Optional[ArtistIDCache] = None,
) -> list[ArtistRelease]:
    log_debug(f"Fetching Deezer releases for artist: {artist_name}")
    artist = _resolve_artist(artist_name, known_titles, artist_cache)
    if not artist:
        return []

    artist_id = str(artist.get("id") or "").strip()
    if not artist_id:
        return []
    known_genres = _known_artist_genre_ids(artist_id, artist_name, known_titles)
    known_musicbrainz_ids = (
        _known_musicbrainz_artist_ids(artist_name, known_titles)
        if known_genres
        and _known_artist_genres_are_ambiguous(
            artist_id, artist_name, known_titles
        )
        else frozenset()
    )

    candidates: list[ArtistRelease] = []
    for release_date, album in _iter_recent_albums(artist_id, cutoff):
        album_id = str(album.get("id") or "").strip()
        if not album_id:
            continue
        album_name = str(album.get("title") or "").strip()
        if _is_alt_or_reissue("", album_name):
            continue
        if not _album_matches_known_genres(album, known_genres):
            log_debug(
                f"Skipping release '{album_name}' for {artist_name}: its Deezer "
                "genres do not match known station tracks"
            )
            continue
        tracks = _album_tracks_by_artist(album_id, artist_id)
        if not tracks:
            continue
        eligible_tracks: list[dict] = []
        for track in tracks:
            if not _is_track_currently_available(track):
                log_debug(
                    f"Skipping Deezer track '{track.get('title', '')}' from '{album_name}' "
                    "because it is not currently readable"
                )
                continue
            title = str(track.get("title") or "").strip()
            if not title or _is_alt_or_reissue(title, album_name):
                continue
            if _is_known_catalog_title(title, known_titles):
                log_debug(
                    f"Skipping already-cataloged track for {artist_name}: {title}"
                )
                continue
            eligible_tracks.append(track)
        if not eligible_tracks:
            continue
        chosen_track = max(
            eligible_tracks,
            key=lambda item: (
                int(item.get("rank") or 0),
                -int(item.get("disk_number") or 0),
                -int(item.get("track_position") or 0),
            ),
        )
        title = str(chosen_track.get("title") or "").strip()
        track_id = str(chosen_track.get("id") or "").strip()
        if not title or not track_id:
            continue
        if known_musicbrainz_ids:
            candidate_musicbrainz_ids = _musicbrainz_recording_artist_ids(
                artist_name, title
            )
            if (
                candidate_musicbrainz_ids
                and known_musicbrainz_ids.isdisjoint(candidate_musicbrainz_ids)
            ):
                log_debug(
                    f"Skipping release for a different same-named artist: "
                    f"{artist_name} - {title}"
                )
                continue
        if _is_probable_old_catalog_release(artist_name, title, album_name, release_date):
            continue
        record_type = str(album.get("record_type") or "").strip().lower() or None
        rank_value = chosen_track.get("rank")
        rank = int(rank_value) if rank_value not in (None, "") else None
        candidates.append(
            ArtistRelease(
                artist=artist_name,
                title=title,
                year=release_date.year,
                album=album_name,
                release_date=release_date,
                track_id=track_id,
                rank=rank,
                is_single=record_type == "single",
                album_type=record_type,
            )
        )
    candidates.sort(key=lambda item: item.release_date, reverse=True)
    return candidates


def _coerce_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return False


def load_existing_new_releases(playlists_dir: Path) -> list[ArtistRelease]:
    path = playlists_dir / _PLAYLIST_FILENAME
    if not path.exists():
        log_debug("New Releases.csv not found; starting from empty state")
        return []
    try:
        tracks = StationPlaylistCatalog(
            playlists_dir,
            log=log_warning,
        ).load_tracks_with_metadata(path, metadata_filename=_METADATA_FILENAME)
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


def _append_release_to_playlist(
    csv_path: Path, release: ArtistRelease, dry_run: bool
) -> None:
    action = (
        f"Dry run: would append '{release.artist} - {release.title}' to {csv_path.name}"
        if dry_run
        else f"Appending '{release.artist} - {release.title}' to {csv_path.name}"
    )
    log_info(action)
    try:
        appended = StationPlaylistCatalog(
            csv_path.parent,
            log=log_debug,
        ).append(
            csv_path,
            Song(
                artist=release.artist,
                title=release.title,
                year=str(release.year),
                album=release.album or None,
                validated=False,
            ),
            policy=(
                CatalogWritePolicy.PREVIEW
                if dry_run
                else CatalogWritePolicy.PERSIST
            ),
        )
    except Exception as exc:  # noqa: BLE001
        log_error(f"Failed reading {csv_path}: {exc}")
        return
    if not appended:
        log_debug(
            f"Track already present in {csv_path.name}: "
            f"{release.artist} - {release.title}"
        )
    elif not dry_run:
        log_debug(f"Appended '{release.title}' to {csv_path.name}")


def _promote_release_album(release: ArtistRelease) -> bool:
    """Update release.album to the studio album when a confident match exists."""

    current_album = (release.album or "").strip()
    current_type = (release.album_type or "").strip().casefold()
    # Some providers label a pre-album single as an "album" whose title is the
    # track title.  Treat that as provisional metadata so aging a release also
    # gives a now-published studio album a chance to replace it.
    album_is_track_title = _track_titles_match(current_album, release.title)
    should_attempt = (
        release.is_single
        or not current_album
        or current_type != "album"
        or album_is_track_title
    )
    if not should_attempt:
        return False

    resolver = TrackMetadataResolver(
        validator=CallableTrackValidationPort(
            track_exists_func=verified,
            album_matches_func=verified_album,
        ),
        album_resolver=CallableAlbumResolutionPort(guess_album_func=guess_album),
    )
    resolution = resolver.resolve(
        TrackResolutionRequest(
            song=Song(
                artist=release.artist,
                title=release.title,
                album=release.album,
                year=str(release.year),
                validated=release.validated,
            ),
            mode=ResolutionMode.PROMOTE_RELEASE_ALBUM,
            prefer_spotify=False,
            prefer_deezer=True,
            min_confidence=0.55,
            allow_fallback=True,
        )
    )
    for note in resolution.notes:
        if note.startswith("album_lookup_failed:"):
            detail = note.removeprefix("album_lookup_failed:")
            log_warning(
                f"Album lookup failed for {release.artist} - {release.title}: {detail}"
            )

    if not resolution.album_promoted or not resolution.song:
        return False

    new_album = (resolution.song.album or "").strip()
    if not new_album:
        return False

    normalized_current = current_album.casefold()
    normalized_new = new_album.casefold()
    album_changed = normalized_current != normalized_new
    type_changed = current_type != "album"

    if not album_changed and not type_changed and not release.is_single:
        return False

    previous_label = current_album or "single"
    release.album = new_album
    release.album_type = resolution.album_type or "album"
    release.is_single = False
    if resolution.release_date:
        release.year = resolution.release_date.year
    if resolution.track_id:
        release.track_id = resolution.track_id

    log_info(
        f"Updated album metadata for {release.artist} - {release.title}: {previous_label} -> {new_album}"
    )
    return True


def _move_track_audio(
    audio_root: Optional[Path],
    source_dir_name: str,
    destination_dir_name: str,
    release: ArtistRelease,
    dry_run: bool,
    refresh_album_art: bool = False,
) -> bool:
    if not audio_root:
        return True
    src_dir = audio_root / source_dir_name
    if not src_dir.exists():
        log_debug(f"Audio source directory missing: {src_dir}")
        return True
    dest_dir = audio_root / destination_dir_name
    target_key = _normalize_audio_label(release.artist, release.title)
    for candidate in src_dir.iterdir():
        if not candidate.is_file():
            continue
        if candidate.suffix.lower() not in {".mp3", ".flac", ".wav"}:
            continue
        candidate_key = _normalize_audio_label(candidate.stem)
        if candidate_key != target_key and candidate.suffix.lower() == ".mp3":
            try:
                tags = EasyID3(str(candidate))
                tagged_artist = tags.get("artist", [""])[0]
                tagged_title = tags.get("title", [""])[0]
                candidate_key = _normalize_audio_label(tagged_artist, tagged_title)
            except Exception:  # noqa: BLE001 - filename matching remains available
                pass
        if candidate_key == target_key:
            canonical_name = (
                f"{sanitize_filename_component(release.artist)} - "
                f"{sanitize_filename_component(release.title)}{candidate.suffix.lower()}"
            )
            collision = next(
                (
                    path
                    for path in dest_dir.iterdir()
                    if path.is_file() and path.name.casefold() == canonical_name.casefold()
                ),
                None,
            ) if dest_dir.exists() else None
            if collision is not None:
                log_warning(
                    "Audio move blocked because the permanent playlist already has "
                    f"a case-insensitive filename match: {collision.name}. "
                    "The release will remain in New Releases for review."
                )
                return False
            dest_path = dest_dir / canonical_name
            if dry_run:
                log_info(
                    f"Dry run: would move {candidate.name} to "
                    f"{dest_dir / canonical_name}"
                )
                return True
            dest_dir.mkdir(parents=True, exist_ok=True)
            log_info(
                f"Moving audio for '{release.artist} - {release.title}' "
                f"from {src_dir} to {dest_dir}"
            )
            candidate.replace(dest_path)
            log_info(f"Moved {candidate.name} to {dest_path}")
            if dest_path.suffix.lower() == ".mp3":
                try:
                    tag_mp3(
                        str(dest_path),
                        release.artist,
                        release.title,
                        str(release.year),
                        destination_dir_name,
                        release.album,
                        log_prefix="      ",
                        refresh_art=refresh_album_art,
                        apply_replaygain=False,
                    )
                except Exception as exc:
                    log_warning(
                        "Moved audio but could not refresh destination metadata for "
                        f"{release.artist} - {release.title}: {exc}"
                    )
            else:
                log_warning(
                    f"Moved {dest_path.name}, but automatic destination tagging "
                    "currently supports MP3 files only"
                )
            return True
    log_warning(
        f"No audio found for {release.artist} - {release.title} in {src_dir}; nothing moved"
    )
    return True


def move_outdated_releases(
    releases: list[ArtistRelease],
    artist_playlist_map: dict[str, dict[Path, set[str]]],
    audio_root: Optional[Path],
    new_releases_dir_name: str,
    dry_run: bool,
) -> list[ArtistRelease]:
    if not releases:
        return []
    migrations: list[tuple[ArtistRelease, Path]] = []
    blocked_releases: list[ArtistRelease] = []
    for release in releases:
        destination = _resolve_destination_playlist(release, artist_playlist_map)
        if not destination:
            log_warning(f"No destination playlist for {release.artist} - {release.title}")
            blocked_releases.append(release)
            continue
        album_promoted = _promote_release_album(release)
        moved = _move_track_audio(
            audio_root,
            new_releases_dir_name,
            destination.stem,
            release,
            dry_run=dry_run,
            refresh_album_art=album_promoted,
        )
        if not moved:
            blocked_releases.append(release)
            continue
        migrations.append((release, destination))
        _append_release_to_playlist(destination, release, dry_run=dry_run)
    if not migrations:
        return blocked_releases
    action_phrase = (
        "Dry run: would move the following tracks to permanent playlists"
        if dry_run
        else "Moved the following tracks to permanent playlists"
    )
    log_info(action_phrase)
    for release, destination in migrations:
        log_info(f"  • {release.artist} – {release.title} → {destination.name}")
    return blocked_releases


def build_new_releases(
    artists: Iterable[str],
    days: int,
    per_artist: int = 1,
    min_rank: int = 0,
    prefer_singles: bool = False,
    known_tracks: Optional[dict[str, set[str]]] = None,
    artist_cache: Optional[ArtistIDCache] = None,
    cutoff: Optional[datetime] = None,
    seen_tracks: Optional[Set[str]] = None,
    seen_keys: Optional[Set[str]] = None,
    existing_artist_counts: Optional[dict[str, int]] = None,
) -> list[ArtistRelease]:
    cutoff = cutoff or datetime.now(UTC) - timedelta(days=days)
    releases: list[ArtistRelease] = []
    seen_track_ids: Set[str] = set(seen_tracks or set())
    seen_title_keys: Set[str] = set(seen_keys or set())
    artists_list = list(artists)
    normalized_existing_counts = {
        _normalize_text(artist): max(int(count), 0)
        for artist, count in (existing_artist_counts or {}).items()
    }

    for artist in tqdm(
        artists_list, desc="Artists", unit="artist", disable=not sys.stdout.isatty()
    ):
        remaining_slots = max(
            per_artist - normalized_existing_counts.get(_normalize_text(artist), 0),
            0,
        )
        if remaining_slots == 0:
            continue
        artist_titles = (known_tracks or {}).get(artist, set())
        candidates = fetch_recent_releases(
            artist, cutoff, artist_titles, artist_cache=artist_cache
        )
        if not candidates:
            continue
        filtered = [c for c in candidates if (c.rank or 0) >= min_rank]
        if not filtered:
            continue

        def rank_key(release: ArtistRelease) -> tuple[int, int, datetime]:
            single_score = 1 if (prefer_singles and release.is_single) else 0
            return (single_score, release.rank or 0, release.release_date)

        filtered.sort(key=rank_key, reverse=True)

        kept = 0
        for candidate in filtered:
            if candidate.track_id and candidate.track_id in seen_track_ids:
                continue
            title_key = _normalize_audio_label(candidate.artist, candidate.title)
            if title_key in seen_title_keys:
                continue
            releases.append(candidate)
            seen_title_keys.add(title_key)
            if candidate.track_id:
                seen_track_ids.add(candidate.track_id)
            kept += 1
            if kept >= remaining_slots:
                break

    releases.sort(key=lambda item: (item.release_date, item.rank or 0), reverse=True)
    return releases


def save_new_releases(playlists_dir: Path, releases: list[ArtistRelease], dry_run: bool) -> None:
    output_path = playlists_dir / _PLAYLIST_FILENAME
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
        metadata_filename=_METADATA_FILENAME,
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


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Refresh the New Releases playlist for a station."
    )
    parser.add_argument(
        "-s",
        "--station",
        choices=ALLOWED_STATION_SLUGS,
        default=DEFAULT_STATION_SLUG,
        help="Station slug (default: %(default)s).",
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
        help="Collect and display results without writing output files",
    )
    parser.add_argument(
        "--per-artist",
        type=int,
        default=3,
        help="Max tracks to keep per artist (default: 3)",
    )
    parser.add_argument(
        "--min-rank",
        "--min-popularity",
        type=int,
        dest="min_rank",
        default=0,
        help="Minimum rank to keep (default: 0)",
    )
    parser.add_argument(
        "--prefer-singles",
        action="store_true",
        help="Prefer singles when ranking candidates (default: off)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show detailed debug output",
    )
    return parser


def _resolve_station_paths(station_arg: str) -> tuple[Path, Path]:
    station_dir = station_dir_from_slug(station_arg)
    return station_dir, station_dir / "playlists"


def main() -> None:
    args = build_arg_parser().parse_args()
    from .runtime import NewReleasesRequest, NewReleasesRuntime

    NewReleasesRuntime().run(
        NewReleasesRequest(
            station=args.station,
            days=args.days,
            per_artist=args.per_artist,
            min_rank=args.min_rank,
            prefer_singles=args.prefer_singles,
            dry_run=args.dry_run,
            verbose=args.verbose,
        )
    )


__all__ = [
    "ArtistIDCache",
    "ArtistRelease",
    "NewReleasesRequest",
    "NewReleasesResult",
    "NewReleasesRuntime",
    "build_arg_parser",
    "build_new_releases",
    "fetch_recent_releases",
    "load_existing_new_releases",
    "load_station_artists",
    "main",
    "parse_release_date",
    "save_new_releases",
]


def __getattr__(name: str):
    if name in {"NewReleasesRequest", "NewReleasesResult", "NewReleasesRuntime"}:
        from . import runtime

        return getattr(runtime, name)
    raise AttributeError(name)
