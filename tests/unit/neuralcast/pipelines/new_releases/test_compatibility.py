"""Compatibility tests for the former New Releases entrypoint module."""

from __future__ import annotations

from neuralcast.pipelines.new_releases import main
from neuralcast.pipelines.new_releases.models import ArtistIDCache, ArtistRelease


def test_main_reexports_public_models() -> None:
    assert main.ArtistIDCache is ArtistIDCache
    assert main.ArtistRelease is ArtistRelease


def test_main_delegates_cli_execution(monkeypatch) -> None:
    calls: list[list[str] | None] = []
    monkeypatch.setattr(
        "neuralcast.cli.update_new_releases.main",
        lambda argv=None: calls.append(argv) or 4,
    )

    assert main.main(["--dry-run"]) == 4
    assert calls == [["--dry-run"]]
