"""Core data models used across the NeuralCast pipeline."""
from dataclasses import dataclass
from typing import Optional

from pydantic import BaseModel


class Song(BaseModel):
    artist: str
    title: str
    year: str
    album: Optional[str] = None
    validated: bool = False
    override_url: Optional[str] = None


@dataclass
class ValidationResult:
    song: Optional[Song]
    album: Optional[str] = None
    album_cleared: bool = False


__all__ = ["Song", "ValidationResult"]
