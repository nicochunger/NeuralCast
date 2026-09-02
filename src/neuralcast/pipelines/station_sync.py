"""Compatibility facade for station playlist synchronization.

New code should import specific ownership modules or station_sync_service.
"""

from __future__ import annotations

from .station_sync_media import DefaultMediaLibrary
from .station_sync_models import (
    MediaLibrary,
    PlaylistLog,
    PlaylistSyncReport,
    SyncReport,
    SyncRequest,
    TrackResolver,
)
from .station_sync_persistence import remove_new_releases_metadata_entries
from .station_sync_resolver import DefaultTrackResolver
from .station_sync_service import StationSync, list_playlists, main

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
