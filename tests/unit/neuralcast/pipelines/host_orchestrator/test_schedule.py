"""Unit tests for host orchestrator schedule helpers."""

from __future__ import annotations

from neuralcast.pipelines.host_orchestrator import schedule


def test_resolve_station_metadata_file_prefers_metadata_then_legacy(tmp_path) -> None:
    station_dir = tmp_path / "Station"
    metadata_dir = station_dir / "metadata"
    playlists_dir = station_dir / "playlists"
    metadata_dir.mkdir(parents=True)
    playlists_dir.mkdir()
    legacy = playlists_dir / "state.json"
    legacy.write_text("{}", encoding="utf-8")

    assert schedule.resolve_station_metadata_file(station_dir, "state.json") == legacy

    current = metadata_dir / "state.json"
    current.write_text("{}", encoding="utf-8")
    assert schedule.resolve_station_metadata_file(station_dir, "state.json") == current
