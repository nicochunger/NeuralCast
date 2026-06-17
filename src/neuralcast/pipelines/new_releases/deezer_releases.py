"""Provider adapter for Deezer-backed New Releases discovery.

The implementation currently delegates to the legacy provider functions in
``main`` so existing private compatibility imports keep working while callers
can depend on one provider seam.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Protocol

legacy = importlib.import_module("neuralcast.pipelines.new_releases.main")


class ReleaseDiscoveryProvider(Protocol):
    def fetch_recent_releases(
        self,
        artist_name: str,
        cutoff: datetime,
        known_titles: Optional[set[str]] = None,
        artist_cache: Optional[legacy.ArtistIDCache] = None,
    ) -> list[legacy.ArtistRelease]:
        ...


@dataclass(frozen=True)
class DeezerReleaseDiscoveryProvider:
    """Concrete adapter for Deezer plus MusicBrainz old-catalog filtering."""

    def fetch_recent_releases(
        self,
        artist_name: str,
        cutoff: datetime,
        known_titles: Optional[set[str]] = None,
        artist_cache: Optional[legacy.ArtistIDCache] = None,
    ) -> list[legacy.ArtistRelease]:
        return legacy.fetch_recent_releases(
            artist_name,
            cutoff,
            known_titles,
            artist_cache=artist_cache,
        )


__all__ = [
    "DeezerReleaseDiscoveryProvider",
    "ReleaseDiscoveryProvider",
]
