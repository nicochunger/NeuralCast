"""Unit tests for playlist sync CLI entrypoint."""

from __future__ import annotations

from neuralcast.cli import sync_playlists


def test_sync_playlists_main_dispatches_explicit_args(monkeypatch) -> None:
    calls: list[tuple[str, bool]] = []

    def fake_main(station: str, dry_run: bool) -> None:
        calls.append((station, dry_run))

    monkeypatch.setattr("neuralcast.pipelines.station_sync.main", fake_main)

    exit_code = sync_playlists.main(["-s", "neuralcast", "--dry-run"])

    assert exit_code == 0
    assert calls == [("neuralcast", True)]
