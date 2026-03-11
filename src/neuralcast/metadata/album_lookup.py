"""High-quality album lookup helpers for artist/title pairs."""

from __future__ import annotations

import difflib
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from typing import List, Optional, Sequence

import dotenv
import musicbrainzngs
import requests
import spotipy
from requests import Session
from spotipy import Spotify
from spotipy.exceptions import SpotifyException
from spotipy.oauth2 import SpotifyClientCredentials

from neuralcast.services.ai_client import openai_text_completion
from neuralcast.services.deezer import get_album, parse_release_date, search_tracks

# Ensure environment variables (e.g., Spotify credentials) are available.
dotenv.load_dotenv()

# Configure MusicBrainz client once. The email can be customized by the caller.
musicbrainzngs.set_useragent("NeuralCast", "0.1", "neuralcast@example.com")

_LOGGER = logging.getLogger(__name__)
_ITUNES_SESSION: Session = requests.Session()
_ITUNES_SESSION.headers.update({"User-Agent": "NeuralCast/1.0"})
_REQUEST_TIMEOUT = 10


def _styled_warning(message: str, *, prefix: str = "   ") -> None:
    """Mirror the album art / ReplayGain log style for warnings."""
    formatted = f"{prefix}⚠️ {message}"
    print(formatted)
    _LOGGER.warning(message)


@dataclass(frozen=True)
class AlbumMatch:
    album: str
    source: str
    confidence: float
    album_type: Optional[str] = None
    raw_album: Optional[str] = None
    release_date: Optional[datetime] = None
    track_id: Optional[str] = None
    track_name: Optional[str] = None
    title_score: float = 0.0
    artist_score: float = 0.0
    album_artist_score: float = 0.0
    popularity: Optional[int] = None
    flags: Sequence[str] = ()

    @property
    def is_confident(self) -> bool:
        return self.confidence >= 0.55


_BAD_ALBUM_TERMS = (
    "deluxe",
    "expanded",
    "remaster",
    "remastered",
    "live",
    "anniversary",
    "bonus track",
    "special edition",
    "super deluxe",
    "karaoke",
    "collector's edition",
    "collectors edition",
    "platinum edition",
    "expanded edition",
    "redux",
    "tour edition",
)

_FEATURE_RE = re.compile(r"\s+(feat|featuring|ft|with)\.? .*$", re.IGNORECASE)
_PARENS_RE = re.compile(r"\s*[\(\[].*?[\)\]]")
_SUFFIX_RE = re.compile(
    r"\s*-\s*(live.*|acoustic.*|remaster.*|version.*|radio edit.*|mono|stereo)$",
    re.IGNORECASE,
)
_NON_ALNUM_RE = re.compile(r"[^0-9a-z]+")
_MULTISPACE_RE = re.compile(r"\s+")
_YEAR_REMASTER_RE = re.compile(
    r"\b(19|20)\d{2}\s+(remaster(?:ed)?|remix(?:es)?|edition)",
    re.IGNORECASE,
)
_CITY_YEAR_RE = re.compile(r"^[A-Za-z'’]+(?:\s+[A-Za-z'’]+){0,3}\s(19|20)\d{2}$")
_DATESTAMP_RE = re.compile(r"\b(19|20)\d{2}[-/](?:0?[1-9]|1[0-2])")
_ALBUM_ARTIST_MISMATCH_THRESHOLD = 0.7

LIVE_ALBUM_HINTS = (
    " live ",
    " live!",
    " live?",
    " live:",
    " live -",
    "- live",
    "(live",
    "[live",
    " live)",
    " live]",
    " live @",
    " live at ",
    " live in ",
    " live on ",
    " live from ",
    " live recording",
    " live version",
    " in concert",
    " on the road",
    " world tour",
    " tour edition",
    " tour live",
)


_CLEAN_KEYWORD_FRAGMENTS = (
    "remaster",
    "remastered",
    "mix",
    "mixes",
    "remix",
    "remixes",
    "deluxe",
    "expanded",
    "anniversary",
    "special edition",
    "bonus track",
    "bonus tracks",
    "bonus disc",
    "bonus edition",
    "tour edition",
    "collector's edition",
    "collectors edition",
    "super deluxe",
    "live",
    "version",
    "versions",
    "edition",
    "editions",
    "mono",
    "stereo",
    "mono mix",
    "stereo mix",
    "mono version",
    "stereo version",
    "mono edit",
    "stereo edit",
)

_CLEAN_PARENS_RE = re.compile(r"\s*[\(\[]([^)\]]+)[\)\]]", re.IGNORECASE)
_CLEAN_SUFFIX_RE = re.compile(
    r"\s*[-–—:,]\s*((?:\d{4}\s+)?.*?(?:remaster(?:ed)?|remix(?:es)?|deluxe|expanded|anniversary|special\s+edition|bonus\s+tracks?|bonus\s+disc|tour\s+edition|collector'?s\s+edition|super\s+deluxe|live(?:\s+.*)?|versions?(?:\s+.*)?|editions?(?:\s+.*)?))$",
    re.IGNORECASE,
)


def _normalize_title(value: str) -> str:
    value = value or ""
    lowered = value.lower().strip()
    lowered = _FEATURE_RE.sub("", lowered)
    lowered = _PARENS_RE.sub("", lowered)
    lowered = _SUFFIX_RE.sub("", lowered)
    lowered = _NON_ALNUM_RE.sub(" ", lowered)
    lowered = _MULTISPACE_RE.sub(" ", lowered)
    return lowered.strip()


def _has_live_indicator(value: str) -> bool:
    if not value:
        return False
    lower = value.lower()
    if lower.endswith(" live"):
        return True
    for marker in LIVE_ALBUM_HINTS:
        if marker in lower:
            return True
    stripped = value.strip()
    if _CITY_YEAR_RE.match(stripped):
        return True
    if _DATESTAMP_RE.search(stripped):
        return True
    return False


def _should_strip_section(section: str) -> bool:
    lowered = section.lower()
    return any(fragment in lowered for fragment in _CLEAN_KEYWORD_FRAGMENTS)


def _clean_album_name(name: str) -> str:
    if not name:
        return name

    cleaned = name

    def paren_replacer(match: re.Match[str]) -> str:
        inner = match.group(1)
        return "" if _should_strip_section(inner) else match.group(0)

    cleaned = _CLEAN_PARENS_RE.sub(paren_replacer, cleaned)

    # Remove trailing descriptors like "- 2015 Remaster"
    cleaned = _CLEAN_SUFFIX_RE.sub("", cleaned)

    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = cleaned.strip(" -–—:,")
    cleaned = cleaned.strip()
    paren_match = re.match(r"^\(([^)]+)\)\s*(.*)$", cleaned)
    if paren_match:
        inner, remainder = paren_match.groups()
        cleaned = f"{inner} {remainder}".strip()
    cleaned = cleaned.rstrip("?").strip()

    if not cleaned:
        return name.strip()
    return cleaned


def _normalize_artist_token(value: str) -> str:
    value = value or ""
    lowered = value.lower().strip()
    lowered = _FEATURE_RE.sub("", lowered)
    lowered = _NON_ALNUM_RE.sub(" ", lowered)
    lowered = _MULTISPACE_RE.sub(" ", lowered)
    return lowered.strip()


def _split_artist_aliases(value: str) -> List[str]:
    if not value:
        return []
    parts = re.split(r",|&|/| x | and ", value, flags=re.IGNORECASE)
    normalized = {_normalize_artist_token(part) for part in parts if part.strip()}
    normalized.add(_normalize_artist_token(value))
    return [token for token in normalized if token]


def _ratio(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def _album_type_rank(album_type: Optional[str]) -> int:
    if not album_type:
        return 3
    mapping = {"album": 0, "single": 1, "compilation": 2, "appears_on": 3}
    return mapping.get(album_type, 3)


def _is_reissue(name: str) -> bool:
    lowered = (name or "").lower()
    if any(term in lowered for term in _BAD_ALBUM_TERMS):
        return True
    # Check for year-based patterns like "2015 Remaster"
    if _YEAR_REMASTER_RE.search(name):
        return True
    return False


def _parse_spotify_release_date(
    date_str: Optional[str], precision: Optional[str]
) -> Optional[datetime]:
    if not date_str:
        return None
    try:
        if precision == "day" and len(date_str) == 10:
            return datetime.strptime(date_str, "%Y-%m-%d")
        if precision == "month" and len(date_str) >= 7:
            return datetime.strptime(date_str[:7], "%Y-%m")
        if len(date_str) >= 4:
            return datetime.strptime(date_str[:4], "%Y")
    except ValueError:
        return None
    return None


def _itunes_search(term: str, limit: int) -> list[dict]:
    url = "https://itunes.apple.com/search"
    try:
        response = _ITUNES_SESSION.get(
            url,
            params={"term": term, "entity": "song", "limit": limit},
            timeout=_REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception:
        return []

    results = payload.get("results")
    if not isinstance(results, list):
        return []
    return [item for item in results if isinstance(item, dict)]


@lru_cache(maxsize=1)
def _get_spotify_client() -> Optional[Spotify]:
    client_id = os.getenv("SPOTIFY_CLIENT_ID")
    client_secret = os.getenv("SPOTIFY_CLIENT_SECRET")
    if not client_id or not client_secret:
        return None
    try:
        credentials = SpotifyClientCredentials(
            client_id=client_id,
            client_secret=client_secret,
        )
        return spotipy.Spotify(auth_manager=credentials)
    except Exception:
        return None


def _spotify_candidates(artist: str, title: str, limit: int = 50) -> List[AlbumMatch]:
    client = _get_spotify_client()
    if client is None or not artist or not title:
        return []

    query = f'artist:"{artist}" track:"{title}"'
    # query = f"{artist} {title}"
    try:
        results = client.search(q=query, type="track", limit=limit)
    except SpotifyException:
        return []
    except Exception:
        return []

    items = results.get("tracks", {}).get("items", []) or []
    query_title = _normalize_title(title)
    artist_tokens = _split_artist_aliases(artist)

    matches: List[AlbumMatch] = []

    for item in items:
        track_name = item.get("name") or ""
        normalized_track = _normalize_title(track_name)
        title_score = _ratio(query_title, normalized_track)
        if title_score < 0.55:
            continue
        track_is_live = _has_live_indicator(track_name)

        candidate_artists = [
            entry.get("name", "") for entry in item.get("artists") or []
        ]
        candidate_tokens = [_normalize_artist_token(a) for a in candidate_artists if a]

        artist_score_candidates = [
            _ratio(query_artist, candidate_artist)
            for query_artist in artist_tokens
            for candidate_artist in candidate_tokens
            if query_artist and candidate_artist
        ]

        artist_score = max(artist_score_candidates, default=0.0)
        if artist_score < 0.40:
            if not any(
                query_artist in candidate_artist or candidate_artist in query_artist
                for query_artist in artist_tokens
                for candidate_artist in candidate_tokens
            ):
                continue

        album_obj = item.get("album") or {}
        album_name = album_obj.get("name") or ""
        album_type = album_obj.get("album_type") or album_obj.get("type")
        album_artists = [
            entry.get("name", "") for entry in album_obj.get("artists") or []
        ]
        album_is_live = _has_live_indicator(album_name)
        release_date = _parse_spotify_release_date(
            album_obj.get("release_date"),
            album_obj.get("release_date_precision"),
        )
        is_reissue = _is_reissue(album_name)
        album_rank = _album_type_rank(album_type)

        # Detect tribute albums and covers
        is_tribute = any(
            keyword in album_name.lower()
            for keyword in ["tribute", "cover", "karaoke", "in the style of"]
        )

        # Check if artist name strongly suggests tribute/cover
        artist_names_lower = " ".join(candidate_artists).lower()
        is_tribute = is_tribute or any(
            keyword in artist_names_lower
            for keyword in ["tribute", "karaoke", "orchestra", "ensemble"]
        )

        album_artist_tokens = [_normalize_artist_token(a) for a in album_artists if a]
        album_artist_score_candidates = [
            _ratio(query_artist, album_artist)
            for query_artist in artist_tokens
            for album_artist in album_artist_tokens
            if query_artist and album_artist
        ]
        album_artist_score = max(album_artist_score_candidates, default=0.0)

        popularity = int(item.get("popularity") or 0)
        penalty = 0.08 * album_rank
        if is_reissue:
            penalty += 0.15
        if track_is_live:
            penalty += 0.3
        if album_is_live:
            penalty += 0.2
        # Heavy penalty for tribute/cover albums
        if is_tribute:
            penalty += 0.4
        # Penalty for weak artist matches (likely covers/tributes)
        if artist_score < 0.60:
            penalty += 0.2
        if album_artist_score < _ALBUM_ARTIST_MISMATCH_THRESHOLD:
            penalty += 0.25
        exact_title = track_name.strip().lower() == title.strip().lower()
        if not exact_title:
            penalty += 0.05
        bonus = 0.05 if exact_title else 0.0
        # Bonus for strong artist match
        if artist_score >= 0.85:
            bonus += 0.05

        confidence = max(
            0.0,
            min(
                1.0,
                0.7 * title_score + 0.3 * artist_score - penalty + bonus,
            ),
        )

        flags = []
        if album_type and album_type != "album":
            flags.append(f"type:{album_type}")
        if is_reissue:
            flags.append("reissue")
        if track_is_live:
            flags.append("live_track")
        if album_is_live:
            flags.append("live_album")
        if popularity < 10:
            flags.append("low_popularity")
        if album_artist_score < _ALBUM_ARTIST_MISMATCH_THRESHOLD:
            flags.append("album_artist_mismatch")

        raw_album = album_name.strip()
        clean_album = _clean_album_name(raw_album)

        matches.append(
            AlbumMatch(
                album=clean_album,
                source="spotify",
                confidence=confidence,
                album_type=album_type,
                raw_album=raw_album,
                release_date=release_date,
                track_id=item.get("id"),
                track_name=track_name,
                title_score=title_score,
                artist_score=artist_score,
                album_artist_score=album_artist_score,
                popularity=popularity,
                flags=tuple(flags),
            )
        )

    matches.sort(
        key=lambda match: (
            _album_type_rank(match.album_type),
            -(match.popularity or 0),
            match.release_date or datetime(3000, 1, 1),
            -match.confidence,
            "reissue" in match.flags,
            "live_album" in match.flags,
            "live_track" in match.flags,
        )
    )
    return matches


def _deezer_artist_names(item: dict) -> List[str]:
    names: list[str] = []
    artist_obj = item.get("artist")
    if isinstance(artist_obj, dict):
        artist_name = str(artist_obj.get("name") or "").strip()
        if artist_name:
            names.append(artist_name)
    contributors = item.get("contributors")
    if isinstance(contributors, list):
        for contributor in contributors:
            if not isinstance(contributor, dict):
                continue
            contributor_name = str(contributor.get("name") or "").strip()
            if contributor_name and contributor_name not in names:
                names.append(contributor_name)
    return names


def _deezer_album_artist_names(item: dict) -> List[str]:
    names: list[str] = []
    artist_obj = item.get("artist")
    if isinstance(artist_obj, dict):
        artist_name = str(artist_obj.get("name") or "").strip()
        if artist_name:
            names.append(artist_name)
    contributors = item.get("contributors")
    if isinstance(contributors, list):
        for contributor in contributors:
            if not isinstance(contributor, dict):
                continue
            contributor_name = str(contributor.get("name") or "").strip()
            if contributor_name and contributor_name not in names:
                names.append(contributor_name)
    return names


def _deezer_candidates(artist: str, title: str, limit: int = 20) -> List[AlbumMatch]:
    if not artist or not title:
        return []

    query = f'artist:"{artist}" track:"{title}"'
    items = search_tracks(query, limit=limit)
    query_title = _normalize_title(title)
    artist_tokens = _split_artist_aliases(artist)
    album_cache: dict[str, Optional[dict]] = {}
    matches: List[AlbumMatch] = []

    for item in items:
        track_name = str(item.get("title") or item.get("title_short") or "")
        normalized_track = _normalize_title(track_name)
        title_score = _ratio(query_title, normalized_track)
        if title_score < 0.55:
            continue
        track_is_live = _has_live_indicator(track_name)

        candidate_artists = _deezer_artist_names(item)
        candidate_tokens = [_normalize_artist_token(name) for name in candidate_artists]
        artist_score_candidates = [
            _ratio(query_artist, candidate_artist)
            for query_artist in artist_tokens
            for candidate_artist in candidate_tokens
            if query_artist and candidate_artist
        ]
        artist_score = max(artist_score_candidates, default=0.0)
        if artist_score < 0.40:
            if not any(
                query_artist in candidate_artist or candidate_artist in query_artist
                for query_artist in artist_tokens
                for candidate_artist in candidate_tokens
            ):
                continue

        album_obj = item.get("album") if isinstance(item.get("album"), dict) else {}
        album_name = str(album_obj.get("title") or album_obj.get("name") or "")
        album_id = str(album_obj.get("id") or "").strip()

        album_detail = album_cache.get(album_id)
        if album_id and album_id not in album_cache:
            album_detail = get_album(album_id)
            album_cache[album_id] = album_detail

        album_type = None
        release_date = None
        album_artists: list[str] = []
        if album_detail:
            album_type = str(album_detail.get("record_type") or "").strip().lower() or None
            release_date = parse_release_date(str(album_detail.get("release_date") or ""))
            album_artists = _deezer_album_artist_names(album_detail)

        album_is_live = _has_live_indicator(album_name)
        is_reissue = _is_reissue(album_name)
        album_rank = _album_type_rank(album_type)

        is_tribute = any(
            keyword in album_name.lower()
            for keyword in ["tribute", "cover", "karaoke", "in the style of"]
        )
        artist_names_lower = " ".join(candidate_artists).lower()
        is_tribute = is_tribute or any(
            keyword in artist_names_lower
            for keyword in ["tribute", "karaoke", "orchestra", "ensemble"]
        )

        album_artist_tokens = [
            _normalize_artist_token(name) for name in album_artists if name
        ]
        album_artist_score_candidates = [
            _ratio(query_artist, album_artist)
            for query_artist in artist_tokens
            for album_artist in album_artist_tokens
            if query_artist and album_artist
        ]
        album_artist_score = max(album_artist_score_candidates, default=0.0)

        popularity = None
        rank_value = item.get("rank")
        if rank_value not in (None, ""):
            try:
                popularity = int(rank_value)
            except (TypeError, ValueError):
                popularity = None

        penalty = 0.08 * album_rank
        if is_reissue:
            penalty += 0.15
        if track_is_live:
            penalty += 0.3
        if album_is_live:
            penalty += 0.2
        if is_tribute:
            penalty += 0.4
        if artist_score < 0.60:
            penalty += 0.2
        if album_artist_tokens and album_artist_score < _ALBUM_ARTIST_MISMATCH_THRESHOLD:
            penalty += 0.25
        exact_title = track_name.strip().lower() == title.strip().lower()
        if not exact_title:
            penalty += 0.05
        bonus = 0.05 if exact_title else 0.0
        if artist_score >= 0.85:
            bonus += 0.05

        confidence = max(
            0.0,
            min(1.0, 0.7 * title_score + 0.3 * artist_score - penalty + bonus),
        )

        flags = []
        if album_type and album_type != "album":
            flags.append(f"type:{album_type}")
        if is_reissue:
            flags.append("reissue")
        if track_is_live:
            flags.append("live_track")
        if album_is_live:
            flags.append("live_album")
        if popularity is not None and popularity < 1000:
            flags.append("low_popularity")
        if album_artist_tokens and album_artist_score < _ALBUM_ARTIST_MISMATCH_THRESHOLD:
            flags.append("album_artist_mismatch")

        raw_album = album_name.strip()
        clean_album = _clean_album_name(raw_album)
        matches.append(
            AlbumMatch(
                album=clean_album,
                source="deezer",
                confidence=confidence,
                album_type=album_type,
                raw_album=raw_album,
                release_date=release_date,
                track_id=str(item.get("id") or "") or None,
                track_name=track_name,
                title_score=title_score,
                artist_score=artist_score,
                album_artist_score=album_artist_score,
                popularity=popularity,
                flags=tuple(flags),
            )
        )

    matches.sort(
        key=lambda match: (
            _album_type_rank(match.album_type),
            -(match.popularity or 0),
            match.release_date or datetime(3000, 1, 1),
            -match.confidence,
            "reissue" in match.flags,
            "live_album" in match.flags,
            "live_track" in match.flags,
        )
    )
    return matches


def _parse_musicbrainz_date(date_str: Optional[str]) -> Optional[datetime]:
    if not date_str:
        return None
    parts = date_str.split("-")
    try:
        if len(parts) == 3:
            return datetime(int(parts[0]), int(parts[1]), int(parts[2]))
        if len(parts) == 2:
            return datetime(int(parts[0]), int(parts[1]), 1)
        if len(parts) == 1:
            return datetime(int(parts[0]), 1, 1)
    except ValueError:
        return None
    return None


def _musicbrainz_candidates(
    artist: str, title: str, limit: int = 5
) -> List[AlbumMatch]:
    if not artist or not title:
        return []

    query = f'recording:"{title}" AND artist:"{artist}"'
    try:
        response = musicbrainzngs.search_recordings(query=query, limit=limit)
    except Exception:
        return []

    recordings = response.get("recording-list", []) or []
    matches: List[AlbumMatch] = []

    query_title = _normalize_title(title)
    query_artists = _split_artist_aliases(artist)

    for recording in recordings:
        rec_title = recording.get("title") or ""
        normalized = _normalize_title(rec_title)
        title_score = _ratio(query_title, normalized)
        if title_score < 0.55:
            continue
        track_is_live = _has_live_indicator(rec_title)

        rel_list = recording.get("release-list", []) or []
        if not rel_list:
            continue

        artist_credits = recording.get("artist-credit", []) or []
        artist_names = [
            credit.get("artist", {}).get("name", "")
            for credit in artist_credits
            if isinstance(credit, dict)
        ]
        candidate_artists = [
            _normalize_artist_token(name) for name in artist_names if name
        ]
        artist_score_candidates = [
            _ratio(query_artist, candidate_artist)
            for query_artist in query_artists
            for candidate_artist in candidate_artists
            if query_artist and candidate_artist
        ]
        artist_score = max(artist_score_candidates, default=0.0)

        for release in rel_list:
            album_name = release.get("title")
            if not album_name:
                continue
            raw_album = album_name.strip()
            release_date = _parse_musicbrainz_date(release.get("date"))
            primary_type = release.get("release-group", {}).get("primary-type")
            album_is_live = _has_live_indicator(album_name)
            flags = []
            if primary_type and primary_type.lower() != "album":
                flags.append(f"type:{primary_type.lower()}")
            if track_is_live:
                flags.append("live_track")
            if album_is_live:
                flags.append("live_album")

            mb_score = recording.get("ext-score")
            try:
                ext_confidence = (
                    float(mb_score) / 100.0 if mb_score is not None else 0.0
                )
            except Exception:
                ext_confidence = 0.0

            base_confidence = max(0.0, min(1.0, 0.6 * title_score + 0.4 * artist_score))
            base_confidence = max(base_confidence, ext_confidence)
            penalty = 0.0
            if track_is_live:
                penalty += 0.25
            if album_is_live:
                penalty += 0.2
            confidence = max(0.0, min(1.0, base_confidence - penalty))

            matches.append(
                AlbumMatch(
                    album=_clean_album_name(raw_album),
                    source="musicbrainz",
                    confidence=confidence,
                    album_type=primary_type.lower()
                    if isinstance(primary_type, str)
                    else None,
                    raw_album=raw_album,
                    release_date=release_date,
                    track_id=None,
                    track_name=rec_title,
                    title_score=title_score,
                    artist_score=artist_score,
                    flags=tuple(flags),
                )
            )

    matches.sort(
        key=lambda match: (
            -match.confidence,
            match.release_date or datetime(3000, 1, 1),
            "live_album" in match.flags,
            "live_track" in match.flags,
        )
    )
    return matches


def _itunes_candidates(artist: str, title: str, limit: int = 10) -> List[AlbumMatch]:
    if not artist or not title:
        return []

    term = f"{artist} {title}"
    results = _itunes_search(term, limit)
    query_title = _normalize_title(title)
    artist_tokens = _split_artist_aliases(artist)
    matches: List[AlbumMatch] = []

    for item in results:
        track_name = str(item.get("trackName") or "")
        normalized_track = _normalize_title(track_name)
        title_score = _ratio(query_title, normalized_track)
        if title_score < 0.55:
            continue
        track_is_live = _has_live_indicator(track_name)

        candidate_artist = str(item.get("artistName") or "")
        candidate_tokens = _split_artist_aliases(candidate_artist)
        artist_score_candidates = [
            _ratio(query_artist, candidate_artist_token)
            for query_artist in artist_tokens
            for candidate_artist_token in candidate_tokens
            if query_artist and candidate_artist_token
        ]
        artist_score = max(artist_score_candidates, default=0.0)
        if artist_score < 0.40:
            if not any(
                query_artist in candidate_artist_token
                or candidate_artist_token in query_artist
                for query_artist in artist_tokens
                for candidate_artist_token in candidate_tokens
            ):
                continue

        album_name = str(item.get("collectionName") or "")
        raw_album = album_name.strip()
        album_type = str(item.get("collectionType") or "").strip().lower() or None
        if album_type == "song":
            album_type = "single"
        release_date = parse_release_date(str(item.get("releaseDate") or "")[:10])
        album_is_live = _has_live_indicator(album_name)
        is_reissue = _is_reissue(album_name)
        popularity = None

        penalty = 0.08 * _album_type_rank(album_type)
        if is_reissue:
            penalty += 0.15
        if track_is_live:
            penalty += 0.3
        if album_is_live:
            penalty += 0.2
        if artist_score < 0.60:
            penalty += 0.2
        exact_title = track_name.strip().lower() == title.strip().lower()
        if not exact_title:
            penalty += 0.05
        bonus = 0.05 if exact_title else 0.0

        confidence = max(
            0.0,
            min(1.0, 0.7 * title_score + 0.3 * artist_score - penalty + bonus),
        )

        flags = []
        if album_type and album_type != "album":
            flags.append(f"type:{album_type}")
        if is_reissue:
            flags.append("reissue")
        if track_is_live:
            flags.append("live_track")
        if album_is_live:
            flags.append("live_album")

        matches.append(
            AlbumMatch(
                album=_clean_album_name(raw_album),
                source="itunes",
                confidence=confidence,
                album_type=album_type,
                raw_album=raw_album,
                release_date=release_date,
                track_id=str(item.get("trackId") or "") or None,
                track_name=track_name,
                title_score=title_score,
                artist_score=artist_score,
                album_artist_score=artist_score,
                popularity=popularity,
                flags=tuple(flags),
            )
        )

    matches.sort(
        key=lambda match: (
            _album_type_rank(match.album_type),
            match.release_date or datetime(3000, 1, 1),
            -match.confidence,
            "reissue" in match.flags,
            "live_album" in match.flags,
            "live_track" in match.flags,
        )
    )
    return matches


def _candidate_provider_order(
    *, prefer_spotify: bool, prefer_deezer: bool
) -> List[str]:
    order: List[str] = []
    if prefer_deezer:
        order.append("deezer")
    elif prefer_spotify:
        order.append("spotify")

    for provider in ("musicbrainz", "itunes"):
        if provider not in order:
            order.append(provider)

    if prefer_deezer:
        if prefer_spotify and "spotify" not in order:
            order.append("spotify")
    else:
        if "deezer" not in order:
            order.append("deezer")
        if prefer_spotify and "spotify" not in order:
            order.insert(0, "spotify")

    return order


def _provider_candidates(
    provider: str, artist: str, title: str, *, limit: int
) -> List[AlbumMatch]:
    if provider == "deezer":
        return _deezer_candidates(artist, title, limit=min(limit, 20))
    if provider == "spotify":
        return _spotify_candidates(artist, title, limit=limit)
    if provider == "musicbrainz":
        return _musicbrainz_candidates(artist, title, limit=min(limit, 10))
    if provider == "itunes":
        return _itunes_candidates(artist, title, limit=min(limit, 10))
    return []


@lru_cache(maxsize=4096)
def album_candidates(
    artist: str,
    title: str,
    *,
    prefer_spotify: bool = True,
    prefer_deezer: bool = False,
    limit: int = 50,
) -> List[AlbumMatch]:
    artist = (artist or "").strip()
    title = (title or "").strip()
    if not artist or not title:
        return []

    for provider in _candidate_provider_order(
        prefer_spotify=prefer_spotify, prefer_deezer=prefer_deezer
    ):
        matches = _provider_candidates(provider, artist, title, limit=limit)
        if matches:
            return matches
    return []


def _prefer_official(matches: Sequence[AlbumMatch]) -> List[AlbumMatch]:
    matches_list = list(matches)
    if not matches_list:
        return []

    def apply_preference(items: Sequence[AlbumMatch], predicate) -> List[AlbumMatch]:
        preferred = [match for match in items if predicate(match)]
        return preferred or list(items)

    def is_official(match: AlbumMatch) -> bool:
        return any(
            flag.lower() == "status:official"
            or flag.lower().startswith("status:official")
            for flag in match.flags
        )

    def is_album(match: AlbumMatch) -> bool:
        return (match.album_type or "").lower() == "album"

    def is_single(match: AlbumMatch) -> bool:
        album_type = (match.album_type or "").lower()
        return album_type == "single" or "type:single" in {
            flag.lower() for flag in match.flags
        }

    non_live = apply_preference(
        matches_list,
        lambda match: "live_track" not in match.flags
        and "live_album" not in match.flags,
    )
    aligned_album_artists = apply_preference(
        non_live,
        lambda match: "album_artist_mismatch" not in match.flags
        or (match.album_type or "").lower() == "single",
    )
    non_live = aligned_album_artists
    official_albums = [
        match for match in non_live if is_official(match) and is_album(match)
    ]
    if official_albums:
        return official_albums

    def has_strong_album_artist(match: AlbumMatch) -> bool:
        score = getattr(match, "album_artist_score", 0.0) or 0.0
        if score == 0.0 and match.source != "spotify":
            return True
        return score >= 0.8 or match.artist_score >= 0.9

    album_matches = [
        match
        for match in non_live
        if is_album(match) and has_strong_album_artist(match)
    ]
    if album_matches:
        return album_matches

    official_matches = [match for match in non_live if is_official(match)]
    if official_matches:
        return official_matches

    non_single_matches = [
        match
        for match in non_live
        if not is_single(match) and has_strong_album_artist(match)
    ]
    if non_single_matches:
        return non_single_matches

    return non_live


def _prefer_earliest_studio_album(matches: Sequence[AlbumMatch]) -> List[AlbumMatch]:
    """Filter matches to prefer the earliest studio album release.

    This prioritizes the original studio album over remasters and deluxe editions
    by selecting albums from the earliest release year.
    """
    if not matches:
        return []

    # Filter for studio albums (non-live)
    studio_albums = [
        m
        for m in matches
        if (m.album_type or "").lower() == "album"
        and "live_album" not in m.flags
        and "live_track" not in m.flags
    ]

    if not studio_albums:
        return list(matches)

    strong_artist = [m for m in studio_albums if m.artist_score >= 0.65]
    candidate_pool = strong_artist or studio_albums

    # Sort by release date (earliest first) then popularity/confidence
    candidate_pool.sort(
        key=lambda m: (
            m.release_date or datetime(3000, 1, 1),
            -(m.popularity or 0),
            -m.confidence,
        )
    )
    earliest_date = candidate_pool[0].release_date

    # Return all albums from the earliest year (handle multiple releases same year)
    if earliest_date:
        same_year = [
            m
            for m in candidate_pool
            if m.release_date and m.release_date.year == earliest_date.year
        ]
        return same_year if same_year else candidate_pool[:1]

    return candidate_pool[:1]


def guess_album(
    artist: str,
    title: str,
    *,
    prefer_spotify: bool = True,
    prefer_deezer: bool = False,
    min_confidence: float = 0.5,
    allow_fallback: bool = True,
) -> Optional[AlbumMatch]:
    def sort_key(match: AlbumMatch) -> tuple:
        popularity = match.popularity if match.popularity is not None else -1
        # For studio albums, prioritize by release date over popularity
        is_studio = (match.album_type or "").lower() == "album"
        release_key = match.release_date or datetime(3000, 1, 1)
        if not (is_studio and not _is_reissue(match.raw_album or match.album)):
            release_key = datetime(4000, 1, 1)
        return (
            _album_type_rank(match.album_type),
            release_key,
            -popularity,
            -match.confidence,
        )

    provider_order = _candidate_provider_order(
        prefer_spotify=prefer_spotify, prefer_deezer=prefer_deezer
    )
    primary_matches = album_candidates(
        artist,
        title,
        prefer_spotify=prefer_spotify,
        prefer_deezer=prefer_deezer,
    )
    if not primary_matches:
        return None
    primary_matches = _prefer_official(primary_matches)
    if not primary_matches:
        return None

    confident = [
        match for match in primary_matches if match.confidence >= min_confidence
    ]
    non_live_confident = [
        match
        for match in confident
        if "live_album" not in match.flags and "live_track" not in match.flags
    ]
    if non_live_confident:
        confident = non_live_confident
    elif confident and all(
        "live_album" in match.flags or "live_track" in match.flags
        for match in confident
    ):
        confident = []
    if confident:
        # Try to get earliest studio album from confident matches
        earliest_studio = _prefer_earliest_studio_album(confident)
        if earliest_studio:
            earliest_studio.sort(key=sort_key)
            return earliest_studio[0]
        # Fallback to original logic
        confident.sort(key=sort_key)
        return confident[0]

    if allow_fallback:
        primary_source = primary_matches[0].source
        for provider in provider_order:
            if provider == primary_source:
                continue
            fallback_matches = _provider_candidates(
                provider, artist, title, limit=50
            )
            fallback_confident = [
                match for match in fallback_matches if match.confidence >= min_confidence
            ]
            if not fallback_confident:
                continue
            fallback_confident = _prefer_official(fallback_confident)
            earliest_fallback = _prefer_earliest_studio_album(fallback_confident)
            if earliest_fallback:
                earliest_fallback.sort(key=sort_key)
                return earliest_fallback[0]
            fallback_confident.sort(key=sort_key)
            return fallback_confident[0]

    return primary_matches[0]


def get_official_album_name(
    artist: str,
    title: str,
    *,
    prefer_spotify: bool = True,
    prefer_deezer: bool = False,
    min_confidence: float = 0.5,
    allow_fallback: bool = True,
) -> Optional[str]:
    match = guess_album(
        artist,
        title,
        prefer_spotify=prefer_spotify,
        prefer_deezer=prefer_deezer,
        min_confidence=min_confidence,
        allow_fallback=allow_fallback,
    )
    if match:
        return match.album
    return None


__all__ = [
    "AlbumMatch",
    "album_candidates",
    "guess_album",
    "get_official_album_name",
]
