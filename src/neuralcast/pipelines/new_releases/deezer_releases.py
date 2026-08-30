"""Provider adapter for Deezer-backed New Releases discovery.

The adapter gives runtime callers one provider seam while the operational
implementation remains independently testable.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Protocol

from . import operations
from .models import ArtistIDCache, ArtistRelease


class ReleaseDiscoveryProvider(Protocol):
    def fetch_recent_releases(
        self,
        artist_name: str,
        cutoff: datetime,
        known_titles: Optional[set[str]] = None,
        artist_cache: Optional[ArtistIDCache] = None,
    ) -> list[ArtistRelease]:
        ...


@dataclass(frozen=True)
class DeezerReleaseDiscoveryProvider:
    """Concrete adapter for Deezer plus MusicBrainz old-catalog filtering."""

    def fetch_recent_releases(
        self,
        artist_name: str,
        cutoff: datetime,
        known_titles: Optional[set[str]] = None,
        artist_cache: Optional[ArtistIDCache] = None,
    ) -> list[ArtistRelease]:
        return operations.fetch_recent_releases(
            artist_name,
            cutoff,
            known_titles,
            artist_cache=artist_cache,
        )


__all__ = [
    "DeezerReleaseDiscoveryProvider",
    "ReleaseDiscoveryProvider",
]
