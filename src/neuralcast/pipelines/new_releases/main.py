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
from tqdm import tqdm

from neuralcast.config import (
    ALLOWED_STATION_SLUGS,
    DEFAULT_STATION_SLUG,
    station_dir_from_slug,
)
from neuralcast.metadata.album_lookup import guess_album
from neuralcast.metadata.storage import (
    load_station_entry_mapping,
    load_station_json_dict,
    metadata_key,
    normalize_metadata_component,
    save_station_entry_mapping,
    save_station_json_dict,
)

_DEBUG_ENABLED = False
_PLAYLIST_FILENAME = "New Releases.csv"
_METADATA_FILENAME = "New Releases.metadata.json"
_ARTIST_CACHE_FILENAME = "ArtistIDs.json"
_EXCLUDED_PLAYLIST_FILENAMES = {"new releases.csv"}
_KNOWN_TRACK_SAMPLE_SIZE = 8
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


def _normalize_musicbrainz_label(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value or "")
    stripped = "".join(char for char in normalized if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]", "", stripped.casefold())


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


def _track_matches_artist(track: dict, artist_id: str, artist_name: str) -> bool:
    artist = track.get("artist") if isinstance(track.get("artist"), dict) else {}
    if str(artist.get("id") or "") == str(artist_id):
        return True
    return _artist_names_match(str(artist.get("name") or ""), artist_name)


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


def _artist_has_known_track(
    artist_id: str, artist_name: str, known_titles: Optional[set[str]]
) -> bool:
    if not artist_id or not known_titles:
        return False
    for title in sorted(title for title in known_titles if title)[:_KNOWN_TRACK_SAMPLE_SIZE]:
        for query in (f'artist:"{artist_name}" track:"{title}"', f"{artist_name} {title}"):
            payload = _deezer_get("/search/track", params={"q": query, "limit": 5})
            if not payload:
                continue
            for track in payload.get("data", []):
                if not isinstance(track, dict):
                    continue
                if not _track_matches_artist_id(track, artist_id):
                    continue
                if _close_enough(str(track.get("title") or ""), title):
                    return True
    return False


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
                if not _close_enough(str(track.get("title") or ""), title):
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
    for candidate in exact_matches:
        candidate_id = str(candidate.get("id") or "").strip()
        if candidate_id and _artist_has_known_track(
            candidate_id, artist_name, known_titles
        ):
            return candidate
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
            cached_matches_known = False
            if not name_matches and known_titles:
                cached_matches_known = _artist_has_known_track(
                    cached_id, artist_name, known_titles
                )
            if name_matches or cached_matches_known:
                exact_matches = _exact_artist_matches(artist_name) if known_titles else []
                if len(exact_matches) > 1 and not cached_matches_known:
                    cached_matches_known = _artist_has_known_track(
                        cached_id, artist_name, known_titles
                    )
                if len(exact_matches) > 1 and not cached_matches_known:
                    log_warning(
                        f"Cached Deezer artist ID for ambiguous '{artist_name}' "
                        "does not match known tracks; refreshing lookup"
                    )
                    cache.remove(artist_name)
                else:
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
    for pattern in _TITLE_EXCLUDE_PATTERNS:
        if pattern.search(title or ""):
            return True
    for pattern in _ALBUM_EXCLUDE_PATTERNS:
        if pattern.search(album_name or ""):
            return True
    return False


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


def _album_tracks_by_artist(album_id: str, artist_id: str, artist_name: str) -> list[dict]:
    tracks: list[dict] = []
    for track in _paginate_deezer(f"/album/{album_id}/tracks", params={"limit": 100}):
        if _track_matches_artist(track, artist_id, artist_name):
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

    candidates: list[ArtistRelease] = []
    for release_date, album in _iter_recent_albums(artist_id, cutoff):
        album_id = str(album.get("id") or "").strip()
        if not album_id:
            continue
        album_name = str(album.get("title") or "").strip()
        if _is_alt_or_reissue("", album_name):
            continue
        tracks = _album_tracks_by_artist(album_id, artist_id, artist_name)
        if not tracks:
            continue
        chosen_track: Optional[dict] = None
        for track in tracks:
            if not _is_track_currently_available(track):
                log_debug(
                    f"Skipping Deezer track '{track.get('title', '')}' from '{album_name}' "
                    "because it is not currently readable"
                )
                continue
            title = str(track.get("title") or "").strip()
            if title and not _is_alt_or_reissue(title, album_name):
                chosen_track = track
                break
        if not chosen_track:
            continue
        title = str(chosen_track.get("title") or "").strip()
        track_id = str(chosen_track.get("id") or "").strip()
        if not title or not track_id:
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


def _load_metadata_entries(playlists_dir: Path) -> dict[str, dict]:
    entries, _resolved = load_station_entry_mapping(
        playlists_dir,
        _METADATA_FILENAME,
        log_warning=log_warning,
        warning_label="metadata file",
        legacy_fallback=False,
    )
    return {
        key: value for key, value in entries.items() if isinstance(key, str) and isinstance(value, dict)
    }


def _save_metadata_entries(
    playlists_dir: Path, releases: list[ArtistRelease], dry_run: bool
) -> None:
    if dry_run:
        log_info("Dry run: not writing metadata JSON")
        return
    entries: dict[str, dict] = {}
    for item in releases:
        key = _metadata_key(item.artist, item.title, item.album, item.year)
        entries[key] = {
            "ReleaseDate": item.release_date.isoformat(),
            "TrackID": item.track_id,
            "AlbumType": item.album_type or "",
            "IsSingle": item.is_single,
            "Rank": item.rank if item.rank is not None else "",
            "Validated": item.validated,
        }
    path = save_station_entry_mapping(playlists_dir, _METADATA_FILENAME, entries)
    log_success(f"Stored metadata for {len(entries)} tracks → {path}")


def load_existing_new_releases(playlists_dir: Path) -> list[ArtistRelease]:
    path = playlists_dir / _PLAYLIST_FILENAME
    if not path.exists():
        log_debug("New Releases.csv not found; starting from empty state")
        return []
    metadata_entries = _load_metadata_entries(playlists_dir)
    try:
        df = pd.read_csv(path)
    except Exception as exc:  # noqa: BLE001
        log_error(f"Failed reading {path}: {exc}")
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
        release_raw = str(metadata.get("ReleaseDate", "")).strip() if isinstance(metadata, dict) else ""
        if release_raw:
            try:
                release_dt = datetime.fromisoformat(release_raw)
                if release_dt.tzinfo is None:
                    release_dt = release_dt.replace(tzinfo=UTC)
            except ValueError:
                log_debug(f"Invalid ReleaseDate '{release_raw}' for {artist} - {title}")
        track_id = str(metadata.get("TrackID", "")).strip() if isinstance(metadata, dict) else ""
        rank = None
        if isinstance(metadata, dict):
            rank_raw = metadata.get("Rank")
            if rank_raw not in (None, ""):
                try:
                    rank = int(rank_raw)
                except (TypeError, ValueError):
                    rank = None
        album_type = (
            str(metadata.get("AlbumType", "")).strip() or None
            if isinstance(metadata, dict)
            else None
        )
        is_single = _coerce_bool(metadata.get("IsSingle", False)) if isinstance(metadata, dict) else False
        validated = _coerce_bool(row.get("Validated", False))
        if isinstance(metadata, dict) and not validated:
            validated = _coerce_bool(metadata.get("Validated", False))
        releases.append(
            ArtistRelease(
                artist=artist,
                title=title,
                year=year,
                album=album,
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
    if dry_run:
        return
    try:
        df = pd.read_csv(csv_path)
    except Exception as exc:  # noqa: BLE001
        log_error(f"Failed reading {csv_path}: {exc}")
        return
    if {"Artist", "Title"}.issubset(df.columns):
        duplicate = (
            df["Artist"].fillna("").str.strip().str.casefold()
            == release.artist.casefold()
        ) & (
            df["Title"].fillna("").str.strip().str.casefold()
            == release.title.casefold()
        )
        if duplicate.any():
            log_debug(
                f"Track already present in {csv_path.name}: {release.artist} - {release.title}"
            )
            return
    row: dict[str, object] = {}
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
    log_debug(f"Appended '{release.title}' to {csv_path.name}")


def _promote_release_album(release: ArtistRelease) -> bool:
    """Update release.album to the studio album when a confident match exists."""

    current_album = (release.album or "").strip()
    current_type = (release.album_type or "").strip().casefold()
    should_attempt = release.is_single or not current_album or current_type != "album"
    if not should_attempt:
        return False

    try:
        match = guess_album(
            release.artist,
            release.title,
            prefer_spotify=False,
            prefer_deezer=True,
            min_confidence=0.55,
            allow_fallback=True,
        )
    except Exception as exc:  # noqa: BLE001
        log_warning(f"Album lookup failed for {release.artist} - {release.title}: {exc}")
        return False

    if not match:
        return False

    new_album = (match.album or "").strip()
    if not new_album:
        return False

    new_type = (match.album_type or "").strip().casefold()
    if new_type != "album":
        return False

    normalized_current = current_album.casefold()
    normalized_new = new_album.casefold()
    album_changed = normalized_current != normalized_new
    type_changed = current_type != "album"

    if not album_changed and not type_changed and not release.is_single:
        return False

    previous_label = current_album or "single"
    release.album = new_album
    release.album_type = match.album_type or "album"
    release.is_single = False
    if match.release_date:
        release.year = match.release_date.year
    if match.track_id:
        release.track_id = match.track_id

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
) -> None:
    if not audio_root:
        return
    src_dir = audio_root / source_dir_name
    if not src_dir.exists():
        log_debug(f"Audio source directory missing: {src_dir}")
        return
    dest_dir = audio_root / destination_dir_name
    if dry_run:
        log_info(
            f"Dry run: would move audio for '{release.artist} - {release.title}'"
            f" from {src_dir} to {dest_dir}"
        )
        return
    dest_dir.mkdir(parents=True, exist_ok=True)
    log_info(
        f"Moving audio for '{release.artist} - {release.title}' from {src_dir} to {dest_dir}"
    )
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
            log_info(f"Moved {candidate.name} to {dest_dir}")
            return
    log_warning(
        f"No audio found for {release.artist} - {release.title} in {src_dir}; nothing moved"
    )


def move_outdated_releases(
    releases: list[ArtistRelease],
    artist_playlist_map: dict[str, dict[Path, set[str]]],
    audio_root: Optional[Path],
    new_releases_dir_name: str,
    dry_run: bool,
) -> None:
    if not releases:
        return
    migrations: list[tuple[ArtistRelease, Path]] = []
    for release in releases:
        destination = _resolve_destination_playlist(release, artist_playlist_map)
        if not destination:
            log_warning(f"No destination playlist for {release.artist} - {release.title}")
            continue
        _promote_release_album(release)
        migrations.append((release, destination))
        _append_release_to_playlist(destination, release, dry_run=dry_run)
        _move_track_audio(
            audio_root,
            new_releases_dir_name,
            destination.stem,
            release,
            dry_run=dry_run,
        )
    if not migrations:
        return
    action_phrase = (
        "Dry run: would move the following tracks to permanent playlists"
        if dry_run
        else "Moved the following tracks to permanent playlists"
    )
    log_info(action_phrase)
    for release, destination in migrations:
        log_info(f"  • {release.artist} – {release.title} → {destination.name}")


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
) -> list[ArtistRelease]:
    cutoff = cutoff or datetime.now(UTC) - timedelta(days=days)
    releases: list[ArtistRelease] = []
    seen_track_ids: Set[str] = set(seen_tracks or set())
    seen_title_keys: Set[str] = set(seen_keys or set())
    artists_list = list(artists)

    for artist in tqdm(
        artists_list, desc="Artists", unit="artist", disable=not sys.stdout.isatty()
    ):
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
            if kept >= per_artist:
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
    csv_rows: list[dict[str, object]] = []
    preview_rows: list[dict[str, object]] = []
    for item in sorted_releases:
        csv_rows.append(
            {
                "Artist": item.artist,
                "Title": item.title,
                "Album": item.album,
                "Year": str(item.year),
                "Validated": item.validated,
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

    df_csv = pd.DataFrame(csv_rows)
    df_csv.to_csv(output_path, index=False)
    _save_metadata_entries(playlists_dir, sorted_releases, dry_run=False)
    log_success(f"Wrote {len(df_csv)} tracks → {output_path}")
    print(f"Wrote {len(df_csv)} tracks to {output_path}", flush=True)


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
    set_debug_mode(args.verbose)
    station_dir, playlists_dir = _resolve_station_paths(args.station)
    if not playlists_dir.exists():
        raise SystemExit(f"Playlists directory not found: {playlists_dir}")

    artists, artist_tracks, artist_playlist_map = load_station_artists(playlists_dir)
    artist_cache = load_artist_id_cache(playlists_dir)
    audio_root = station_dir / "songs"
    if not audio_root.exists():
        log_debug(f"Audio root not found; skipping audio moves: {audio_root}")
        audio_root = None
    cutoff = datetime.now(UTC) - timedelta(days=args.days)

    existing_releases = load_existing_new_releases(playlists_dir)
    valid_existing, outdated_existing = partition_releases_by_cutoff(existing_releases, cutoff)
    existing_ids = {release.track_id for release in valid_existing if release.track_id}
    existing_keys = {
        _normalize_audio_label(release.artist, release.title) for release in valid_existing
    }

    new_releases = build_new_releases(
        artists,
        days=args.days,
        per_artist=args.per_artist,
        min_rank=args.min_rank,
        prefer_singles=args.prefer_singles,
        known_tracks=artist_tracks,
        artist_cache=artist_cache,
        cutoff=cutoff,
        seen_tracks=existing_ids,
        seen_keys=existing_keys,
    )
    save_artist_id_cache(playlists_dir, artist_cache, dry_run=args.dry_run)

    combined = valid_existing + new_releases
    combined.sort(key=lambda item: (item.release_date, item.rank or 0), reverse=True)
    final_releases: list[ArtistRelease] = []
    seen_ids_final: set[str] = set()
    seen_keys_final: set[str] = set()
    for release in combined:
        title_key = _normalize_audio_label(release.artist, release.title)
        if (release.track_id and release.track_id in seen_ids_final) or title_key in seen_keys_final:
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
            "New Releases",
            dry_run=args.dry_run,
        )

    if final_releases:
        log_info(f"Collected {len(final_releases)} recent tracks")
        print(f"Collected {len(final_releases)} recent tracks", flush=True)
    else:
        log_info("No releases found within the window")
        print("No releases found within the window", flush=True)
    save_new_releases(playlists_dir, final_releases, dry_run=args.dry_run)


__all__ = [
    "ArtistIDCache",
    "ArtistRelease",
    "build_arg_parser",
    "build_new_releases",
    "fetch_recent_releases",
    "load_existing_new_releases",
    "load_station_artists",
    "main",
    "parse_release_date",
    "save_new_releases",
]
