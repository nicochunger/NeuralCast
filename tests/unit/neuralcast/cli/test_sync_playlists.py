"""Unit tests for playlist sync CLI entrypoint."""

from __future__ import annotations

import types

from neuralcast.cli import sync_playlists
from neuralcast.pipelines.media_sync import RemoteSyncRequest


def test_sync_playlists_run_dispatches_to_pipeline(monkeypatch) -> None:
    calls: list[tuple[str, bool, RemoteSyncRequest]] = []

    def fake_main(station: str, dry_run: bool, *, remote_sync: RemoteSyncRequest) -> None:
        calls.append((station, dry_run, remote_sync))

    fake_module = types.SimpleNamespace(main=fake_main)
    monkeypatch.setattr(
        "sys.argv",
        ["main.py", "-s", "neuralcast", "--dry-run", "--no-sync-remote"],
    )
    monkeypatch.setitem(__import__("sys").modules, "neuralcast.pipelines.playlist_sync", fake_module)

    sync_playlists.run()

    assert calls
    assert calls[0][0] == "neuralcast"
    assert calls[0][1] is True
    assert calls[0][2].enabled is False
