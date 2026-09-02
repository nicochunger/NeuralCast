"""Selection and ranking of discovered New Releases tracks."""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from typing import Iterable, Optional, Set

from tqdm import tqdm

from .models import ArtistIDCache, ArtistRelease
from .discovery import fetch_recent_releases
from .matching import _normalize_audio_label, _normalize_text


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
    excluded_keys: Optional[Set[str]] = None,
) -> list[ArtistRelease]:
    cutoff = cutoff or datetime.now(UTC) - timedelta(days=days)
    releases: list[ArtistRelease] = []
    seen_track_ids: Set[str] = set(seen_tracks or set())
    seen_title_keys: Set[str] = set(seen_keys or set())
    normalized_excluded_keys = set(excluded_keys or set())
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
        filtered = [
            candidate
            for candidate in candidates
            if (candidate.rank or 0) >= min_rank
            and _normalize_audio_label(candidate.artist, candidate.title)
            not in normalized_excluded_keys
        ]
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


__all__ = ["build_new_releases"]

