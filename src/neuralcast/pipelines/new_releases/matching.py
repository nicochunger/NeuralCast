"""Text normalization and comparison helpers for New Releases."""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

from neuralcast.metadata.storage import metadata_key, normalize_metadata_component


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


def _normalize_metadata_component(value: str) -> str:
    return normalize_metadata_component(value)


def _metadata_key(artist: str, title: str, album: str, year: int) -> str:
    return metadata_key(artist, title, album, year)


def _ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, (a or "").casefold(), (b or "").casefold()).ratio()


def _close_enough(a: str, b: str, minimum: float = 0.72) -> bool:
    return _ratio(a, b) >= minimum


__all__ = [
    "_artist_names_match",
    "_close_enough",
    "_metadata_key",
    "_normalize_artist_match_key",
    "_normalize_audio_label",
    "_normalize_metadata_component",
    "_normalize_musicbrainz_label",
    "_normalize_text",
    "_normalize_track_match_key",
    "_ratio",
    "_track_titles_match",
]

