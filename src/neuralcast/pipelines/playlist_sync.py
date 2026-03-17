"""Compatibility wrapper for the station sync service."""

from __future__ import annotations

from neuralcast.pipelines.station_sync import (
    DefaultMediaLibrary,
    DefaultTrackResolver,
    MediaLibrary,
    PlaylistLog,
    PlaylistSyncReport,
    StationSync,
    SyncReport,
    SyncRequest,
    TrackResolver,
    _backfill_album_for_missing_song,
    _save_playlist_state,
    list_playlists,
    main,
    remove_new_releases_metadata_entries,
)

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
    "_backfill_album_for_missing_song",
    "_save_playlist_state",
    "list_playlists",
    "main",
    "remove_new_releases_metadata_entries",
]
