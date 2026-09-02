"""Compatibility exports for New Releases operational behavior.

New code should import the matching, storage, discovery, migration, or selection
module that owns the behavior it uses.
"""

from __future__ import annotations

from .models import ArtistIDCache, ArtistRelease
from .discovery import fetch_recent_releases, parse_release_date
from .logging import (
    log_debug,
    log_error,
    log_info,
    log_success,
    log_warning,
    set_debug_mode,
)
from .matching import (
    _artist_names_match,
    _close_enough,
    _metadata_key,
    _normalize_artist_match_key,
    _normalize_audio_label,
    _normalize_metadata_component,
    _normalize_musicbrainz_label,
    _normalize_text,
    _normalize_track_match_key,
    _ratio,
    _track_titles_match,
)
from .migration import move_outdated_releases
from .selection import build_new_releases
from .storage import (
    load_artist_id_cache,
    load_existing_new_releases,
    load_release_exclusions,
    load_station_artists,
    partition_releases_by_cutoff,
    save_artist_id_cache,
    save_new_releases,
)


def __getattr__(name: str):
    """Resolve legacy private imports without restoring a monolithic module."""
    from . import discovery, migration, storage

    for module in (discovery, migration, storage):
        try:
            return getattr(module, name)
        except AttributeError:
            continue
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "ArtistIDCache",
    "ArtistRelease",
    "build_new_releases",
    "fetch_recent_releases",
    "load_artist_id_cache",
    "load_existing_new_releases",
    "load_release_exclusions",
    "load_station_artists",
    "move_outdated_releases",
    "parse_release_date",
    "partition_releases_by_cutoff",
    "save_artist_id_cache",
    "save_new_releases",
    "set_debug_mode",
]
