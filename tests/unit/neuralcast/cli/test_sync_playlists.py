"""Unit tests for playlist sync CLI entrypoint."""

from __future__ import annotations

from neuralcast.cli import sync_playlists


def test_sync_playlists_main_dispatches_explicit_args(monkeypatch) -> None:
    calls: list[tuple[str, bool]] = []

    def fake_main(station: str, dry_run: bool) -> None:
        calls.append((station, dry_run))

    monkeypatch.setattr("neuralcast.pipelines.playlist_sync.main", fake_main)

    exit_code = sync_playlists.main(["-s", "neuralcast", "--dry-run"])

    assert exit_code == 0
    assert calls == [("neuralcast", True)]


def test_sync_playlists_run_remains_a_compatibility_alias(monkeypatch) -> None:
    monkeypatch.setattr(sync_playlists, "main", lambda: 7)

    assert sync_playlists.run() == 7
