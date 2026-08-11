"""Unit tests for playlist sync CLI entrypoint."""

from __future__ import annotations

import types

from neuralcast.cli import sync_playlists


def test_sync_playlists_run_dispatches_to_pipeline(monkeypatch) -> None:
    calls: list[tuple[str, bool]] = []

    def fake_main(station: str, dry_run: bool) -> None:
        calls.append((station, dry_run))

    fake_module = types.SimpleNamespace(main=fake_main)
    monkeypatch.setattr(
        "sys.argv",
        ["main.py", "-s", "neuralcast", "--dry-run"],
    )
    monkeypatch.setitem(__import__("sys").modules, "neuralcast.pipelines.playlist_sync", fake_module)

    sync_playlists.run()

    assert calls
    assert calls[0][0] == "neuralcast"
    assert calls[0][1] is True
