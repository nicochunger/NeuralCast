"""Read exact existing recordings and embedded artwork without catalog mutations."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mutagen.id3 import ID3, ID3NoHeaderError
from PIL import Image
from io import BytesIO

from neuralcast.playlists.catalog import StationPlaylistCatalog
from neuralcast.playlists.library import sanitize_filename_component
from .models import Music
from .storage import file_hash


def key(value: str) -> str:
    return value.strip().casefold()


def resolve_recording(station: Path, segment: Music) -> dict[str, Any]:
    catalog = StationPlaylistCatalog(station / "playlists", log=lambda _: None)
    snapshot = catalog.load(segment.playlist)
    matches = [
        song
        for song in snapshot.songs
        if (key(song.artist), key(song.title))
        == (key(segment.artist), key(segment.title))
    ]
    if len(matches) != 1:
        raise ValueError(
            f"Missing or ambiguous catalog recording: {segment.artist} - {segment.title}"
        )
    song = matches[0]
    filename = f"{sanitize_filename_component(song.artist)} - {sanitize_filename_component(song.title)}.mp3"
    # Reject two different identities which sanitize to the same file.
    collisions = [
        other
        for other in snapshot.songs
        if f"{sanitize_filename_component(other.artist)} - {sanitize_filename_component(other.title)}.mp3".casefold()
        == filename.casefold()
    ]
    if len(collisions) != 1:
        raise ValueError(f"Ambiguous sanitized filename: {filename}")
    path = (station / "songs" / segment.playlist / filename).resolve(strict=True)
    path.relative_to((station / "songs").resolve(strict=True))
    if not path.is_file():
        raise ValueError("Recording is not a regular file")
    try:
        tags = ID3(path)
    except ID3NoHeaderError:
        tags = None
    if tags is not None:
        for tag, expected in (("TPE1", song.artist), ("TIT2", song.title)):
            values = tags.getall(tag)
            if values and key(str(values[0])) != key(expected):
                raise ValueError(f"Recording tags do not match catalog: {filename}")
    return {
        "path": str(path),
        "sha256": file_hash(path),
        "artist": song.artist,
        "title": song.title,
        "album": song.album or "",
        "year": song.year,
        "rationale": segment.rationale,
    }


def extract_artwork(source: Path, destination: Path) -> bool:
    try:
        for tag in ID3(source).getall("APIC"):
            with Image.open(BytesIO(tag.data)) as image:
                image.convert("RGB").resize((256, 256), Image.Resampling.LANCZOS).save(
                    destination, "JPEG"
                )
            return True
    except (OSError, ValueError, ID3NoHeaderError):
        pass
    return False
