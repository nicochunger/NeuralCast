"""Unit tests for playlist_sync compatibility exports."""

from __future__ import annotations

from neuralcast.pipelines import playlist_sync
from neuralcast.pipelines import station_sync


def test_playlist_sync_reexports_station_sync_service_types() -> None:
    assert playlist_sync.StationSync is station_sync.StationSync
    assert playlist_sync.SyncRequest is station_sync.SyncRequest
    assert playlist_sync.main is station_sync.main
