"""Compatibility facade for station playlist synchronization.

New code should import the owning module within this package.
"""

from __future__ import annotations

from .media import DefaultMediaLibrary
from .models import (
    MediaLibrary,
    PlaylistLog,
    PlaylistSyncReport,
    SyncReport,
    SyncRequest,
    TrackResolver,
)
from .persistence import remove_new_releases_metadata_entries
from .resolver import DefaultTrackResolver
from .service import StationSync, list_playlists, main

__all__ = [
    "DefaultMediaLibrary",
    "DefaultTrackResolver",
    "MediaLibrary",
    "PlaylistLog",
    "PlaylistSyncReport",
    "StationSync",
    "SyncReport",
    "SyncRequest",
    "TrackResolver",
    "list_playlists",
    "main",
    "remove_new_releases_metadata_entries",
]
