"""Tests for the concrete Deezer discovery-provider seam."""

from __future__ import annotations

from datetime import UTC, datetime

from neuralcast.pipelines.new_releases.deezer_releases import (
    DeezerReleaseDiscoveryProvider,
)
from neuralcast.pipelines.new_releases.models import ArtistIDCache, ArtistRelease


def test_deezer_provider_delegates_all_discovery_arguments(monkeypatch) -> None:
    cutoff = datetime(2026, 9, 1, tzinfo=UTC)
    known_titles = {"Rats"}
    cache = ArtistIDCache({"Ghost": "123"})
    expected = [
        ArtistRelease(
            artist="Ghost",
            title="Lachryma",
            album="Skeleta",
            year=2025,
            release_date=datetime(2025, 4, 25, tzinfo=UTC),
            track_id="release-1",
        )
    ]
    received: dict[str, object] = {}

    def fake_fetch(*args, **kwargs):
        received["args"] = args
        received["kwargs"] = kwargs
        return expected

    monkeypatch.setattr(
        "neuralcast.pipelines.new_releases.deezer_releases.fetch_recent_releases",
        fake_fetch,
    )

    result = DeezerReleaseDiscoveryProvider().fetch_recent_releases(
        "Ghost", cutoff, known_titles, cache
    )

    assert result == expected
    assert received == {
        "args": ("Ghost", cutoff, known_titles),
        "kwargs": {"artist_cache": cache},
    }
