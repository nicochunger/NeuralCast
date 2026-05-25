"""Small object factories shared by unit and boundary tests."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from neuralcast.models import Song
from neuralcast.pipelines.host_orchestrator.models import QueueTrack, TrackMetadata
from neuralcast.pipelines.schedule_generator.models import StationPlaylist


def song_factory(
    *,
    artist: str = "Ghost",
    title: str = "Rats",
    album: str | None = "Prequelle",
    year: str | None = "2018",
    validated: bool = False,
) -> Song:
    return Song(
        artist=artist,
        title=title,
        album=album,
        year=year,
        validated=validated,
    )


def queue_track_factory(
    *,
    queue_id: str = "queue-1",
    song_id: str | int | None = "song-1",
    artist: str = "Ghost",
    title: str = "Rats",
    duration: int | None = 240,
) -> QueueTrack:
    return QueueTrack(
        queue_id=str(queue_id),
        song_id=str(song_id) if song_id is not None else None,
        artist=artist,
        title=title,
        duration=duration,
    )


def track_metadata_factory(
    *,
    album: str | None = "Prequelle",
    year: str | None = "2018",
    genre: str | None = "metal",
) -> TrackMetadata:
    return TrackMetadata(album=album, year=year, genre=genre)


def station_playlist_factory(
    *,
    playlist_id: str = "10",
    name: str = "Prog Metal",
    is_enabled: bool = True,
    weight: float = 1.0,
    schedule_items: list[dict] | None = None,
) -> StationPlaylist:
    return StationPlaylist(
        id=playlist_id,
        name=name,
        is_enabled=is_enabled,
        weight=weight,
        schedule_items=schedule_items or [],
        raw={},
    )


def station_tree_factory(base_dir: Path, *, station_name: str = "Station") -> Path:
    station_dir = base_dir / station_name
    for child in ("playlists", "songs", "metadata"):
        (station_dir / child).mkdir(parents=True, exist_ok=True)
    return station_dir


def write_playlist(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False)
