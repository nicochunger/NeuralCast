"""Core data models used across the NeuralCast pipeline."""
from dataclasses import dataclass
from typing import List, Optional

from pydantic import BaseModel


class Song(BaseModel):
    artist: str
    title: str
    year: str
    album: Optional[str] = None
    validated: bool = False


class Playlist(BaseModel):
    songs: List[Song]


@dataclass
class ValidationResult:
    status: str
    song: Optional[Song]
    album: Optional[str] = None
    album_validated: Optional[bool] = None
    album_reason: Optional[str] = None


__all__ = ["Song", "Playlist", "ValidationResult"]
