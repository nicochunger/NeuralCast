"""Data models shared by New Releases discovery, storage, and runtime."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime

from neuralcast.metadata.storage import normalize_metadata_component


def _artist_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value or "")
    collapsed = re.sub(r"\s+", " ", normalized).strip()
    return normalize_metadata_component(collapsed)


@dataclass
class ArtistIDCache:
    entries: dict[str, str]
    dirty: bool = False

    def get(self, artist_name: str) -> str | None:
        return self.entries.get(_artist_key(artist_name))

    def set(self, artist_name: str, artist_id: str) -> None:
        artist_id = str(artist_id or "").strip()
        if not artist_id:
            return
        key = _artist_key(artist_name)
        if self.entries.get(key) == artist_id:
            return
        self.entries[key] = artist_id
        self.dirty = True

    def remove(self, artist_name: str) -> None:
        key = _artist_key(artist_name)
        if key in self.entries:
            del self.entries[key]
            self.dirty = True


@dataclass
class ArtistRelease:
    artist: str
    title: str
    year: int
    album: str
    release_date: datetime
    track_id: str
    rank: int | None = None
    is_single: bool = False
    album_type: str | None = None
    validated: bool = False


__all__ = ["ArtistIDCache", "ArtistRelease"]
