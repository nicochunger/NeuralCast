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
import spotipy
from spotipy import Spotify
from spotipy.exceptions import SpotifyException
from spotipy.oauth2 import SpotifyClientCredentials

from openai_utils import openai_text_completion

# Ensure environment variables (e.g., Spotify credentials) are available.
dotenv.load_dotenv()

# Configure MusicBrainz client once. The email can be customized by the caller.
musicbrainzngs.set_useragent("NeuralCast", "0.1", "neuralcast@example.com")

_LOGGER = logging.getLogger(__name__)


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
)

_FEATURE_RE = re.compile(r"\s+(feat|featuring|ft|with)\.? .*$", re.IGNORECASE)
_PARENS_RE = re.compile(r"\s*[\(\[].*?[\)\]]")
_SUFFIX_RE = re.compile(
    r"\s*-\s*(live.*|acoustic.*|remaster.*|version.*|radio edit.*|mono|stereo)$",
    re.IGNORECASE,
)
_NON_ALNUM_RE = re.compile(r"[^0-9a-z]+")
_MULTISPACE_RE = re.compile(r"\s+")

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
    return any(term in lowered for term in _BAD_ALBUM_TERMS)


def _parse_spotify_release_date(date_str: Optional[str], precision: Optional[str]) -> Optional[datetime]:
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
        if title_score < 0.6:
            continue
        track_is_live = _has_live_indicator(track_name)

        candidate_artists = [entry.get("name", "") for entry in item.get("artists") or []]
        candidate_tokens = [_normalize_artist_token(a) for a in candidate_artists if a]

        artist_score_candidates = [
            _ratio(query_artist, candidate_artist)
            for query_artist in artist_tokens
            for candidate_artist in candidate_tokens
            if query_artist and candidate_artist
        ]

        artist_score = max(artist_score_candidates, default=0.0)
        if artist_score < 0.45:
            if not any(
                query_artist in candidate_artist or candidate_artist in query_artist
                for query_artist in artist_tokens
                for candidate_artist in candidate_tokens
            ):
                continue

        album_obj = item.get("album") or {}
        album_name = album_obj.get("name") or ""
        album_type = album_obj.get("album_type") or album_obj.get("type")
        album_is_live = _has_live_indicator(album_name)
        release_date = _parse_spotify_release_date(
            album_obj.get("release_date"),
            album_obj.get("release_date_precision"),
        )
        is_reissue = _is_reissue(album_name)
        album_rank = _album_type_rank(album_type)

        popularity = int(item.get("popularity") or 0)
        penalty = 0.08 * album_rank
        if is_reissue:
            penalty += 0.1
        if track_is_live:
            penalty += 0.3
        if album_is_live:
            penalty += 0.2
        exact_title = track_name.strip().lower() == title.strip().lower()
        if not exact_title:
            penalty += 0.05
        bonus = 0.05 if exact_title else 0.0

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


def _musicbrainz_candidates(artist: str, title: str, limit: int = 5) -> List[AlbumMatch]:
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
        candidate_artists = [_normalize_artist_token(name) for name in artist_names if name]
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
                ext_confidence = float(mb_score) / 100.0 if mb_score is not None else 0.0
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
                    album_type=primary_type.lower() if isinstance(primary_type, str) else None,
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


@lru_cache(maxsize=4096)
def album_candidates(
    artist: str,
    title: str,
    *,
    prefer_spotify: bool = True,
    limit: int = 50,
) -> List[AlbumMatch]:
    artist = (artist or "").strip()
    title = (title or "").strip()
    if not artist or not title:
        return []

    spotify_matches: List[AlbumMatch] = []
    musicbrainz_matches: List[AlbumMatch] = []

    if prefer_spotify:
        spotify_matches = _spotify_candidates(artist, title, limit=limit)
        if spotify_matches:
            return spotify_matches
        musicbrainz_matches = _musicbrainz_candidates(artist, title, limit=limit)
        return musicbrainz_matches

    musicbrainz_matches = _musicbrainz_candidates(artist, title, limit=limit)
    if musicbrainz_matches:
        return musicbrainz_matches
    return _spotify_candidates(artist, title, limit=limit)


def _prefer_official(matches: Sequence[AlbumMatch]) -> List[AlbumMatch]:
    matches_list = list(matches)
    if not matches_list:
        return []

    def apply_preference(items: Sequence[AlbumMatch], predicate) -> List[AlbumMatch]:
        preferred = [match for match in items if predicate(match)]
        return preferred or list(items)

    def is_official(match: AlbumMatch) -> bool:
        return any(
            flag.lower() == "status:official" or flag.lower().startswith("status:official")
            for flag in match.flags
        )

    def is_album(match: AlbumMatch) -> bool:
        return (match.album_type or "").lower() == "album"

    def is_single(match: AlbumMatch) -> bool:
        album_type = (match.album_type or "").lower()
        return album_type == "single" or "type:single" in {flag.lower() for flag in match.flags}

    non_live = apply_preference(
        matches_list,
        lambda match: "live_track" not in match.flags and "live_album" not in match.flags,
    )
    official_albums = [match for match in non_live if is_official(match) and is_album(match)]
    if official_albums:
        return official_albums

    album_matches = [match for match in non_live if is_album(match)]
    if album_matches:
        return album_matches

    official_matches = [match for match in non_live if is_official(match)]
    if official_matches:
        return official_matches

    non_single_matches = [match for match in non_live if not is_single(match)]
    if non_single_matches:
        return non_single_matches

    return non_live


def guess_album(
    artist: str,
    title: str,
    *,
    prefer_spotify: bool = True,
    min_confidence: float = 0.5,
    allow_fallback: bool = True,
) -> Optional[AlbumMatch]:
    def sort_key(match: AlbumMatch) -> tuple:
        popularity = match.popularity if match.popularity is not None else -1
        return (_album_type_rank(match.album_type), -popularity, -match.confidence)

    primary_matches = album_candidates(artist, title, prefer_spotify=prefer_spotify)
    if not primary_matches:
        return None
    primary_matches = _prefer_official(primary_matches)
    if not primary_matches:
        return None

    confident = [match for match in primary_matches if match.confidence >= min_confidence]
    if confident:
        confident.sort(key=sort_key)
        return confident[0]

    if allow_fallback:
        primary_source = primary_matches[0].source
        if primary_source == "spotify":
            fallback_matches = _musicbrainz_candidates(artist, title)
        else:
            fallback_matches = _spotify_candidates(artist, title)
        fallback_confident = [
            match for match in fallback_matches if match.confidence >= min_confidence
        ]
        if fallback_confident:
            fallback_confident = _prefer_official(fallback_confident)
            fallback_confident.sort(key=sort_key)
            return fallback_confident[0]

    return primary_matches[0]


def get_official_album_name(
    artist: str,
    title: str,
    *,
    prefer_spotify: bool = True,
    min_confidence: float = 0.5,
    allow_fallback: bool = True,
) -> Optional[str]:
    match = guess_album(
        artist,
        title,
        prefer_spotify=prefer_spotify,
        min_confidence=min_confidence,
        allow_fallback=allow_fallback,
    )
    if match:
        return match.album
    return None


def lookup_album_via_openai(artist: str, title: str) -> Optional[str]:
    """Ask the OpenAI search model for the canonical album of the specified song.

    Requires ``OPENAI_API_KEY`` to be configured via ``openai_utils``. Returns the
    album name provided by the model or ``None`` when no confident answer is found.
    The helper requests ``gpt-4o-mini-search-preview`` so the model can research the
    song online and is instructed to reply with a single album title.
    """
    # The web browsing tool is too expensive to call. Disabled for now.
    return
    cleaned_artist = (artist or "").strip()
    cleaned_title = (title or "").strip()
    if not cleaned_artist or not cleaned_title:
        return None

    system_prompt = (
        "You are a meticulous music metadata researcher. When identifying an album "
        "for a song you must use your browsing tools to verify the canonical studio "
        "album or primary commercial release. Answer with the album title only."
    )
    user_prompt = (
        "Identify the official album that first included the given song. If there are "
        "multiple versions, prefer the primary studio album release over compilations "
        "or reissues. Respond with only the album name, nothing else.\n"
        f"Artist: {cleaned_artist}\n"
        f"Song title: {cleaned_title}"
    )

    try:
        response = openai_text_completion(
            prompt=user_prompt,
            system_prompt=system_prompt,
            model="gpt-4o-mini-search-preview",
        )
    except Exception as exc:
        _styled_warning(
            f"OpenAI album lookup failed for {cleaned_artist} - {cleaned_title}: {exc}"
        )
        return None

    if not response:
        return None

    first_line = response.strip().splitlines()[0].strip()
    normalized = first_line.strip('"“”')
    if not normalized:
        return None
    if normalized.lower() in {"unknown", "not sure", "n/a"}:
        return None
    return normalized


__all__ = [
    "AlbumMatch",
    "album_candidates",
    "guess_album",
    "get_official_album_name",
    "lookup_album_via_openai",
]
