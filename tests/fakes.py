"""Reusable fakes for offline boundary tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from neuralcast.models import Song, ValidationResult
from neuralcast.pipelines.station_sync import MediaLibrary, PlaylistLog, TrackResolver


class FakeResponse:
    def __init__(
        self,
        payload: dict[str, Any] | list[Any] | None = None,
        *,
        status_code: int = 200,
        text: str = "",
    ) -> None:
        self._payload = payload if payload is not None else {}
        self.status_code = status_code
        self.text = text
        self.headers: dict[str, str] = {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> dict[str, Any] | list[Any]:
        return self._payload


class FakeAzuraCastClient:
    def __init__(
        self,
        *,
        now_playing_payload: dict[str, Any] | None = None,
        queue_payload: list[dict[str, Any]] | None = None,
    ) -> None:
        self.now_playing_payload = now_playing_payload or {
            "listeners": {"current": 7},
            "now_playing": {
                "remaining": 142,
                "song": {
                    "id": 11,
                    "artist": "Amorphis",
                    "title": "Black Winter Day",
                    "length": 244,
                },
            },
        }
        self.queue_payload = queue_payload or [
            {
                "id": "queue-1",
                "duration": 244,
                "song": {"id": 11, "artist": "Amorphis", "title": "Black Winter Day"},
            },
            {
                "id": "queue-2",
                "duration": 215,
                "song": {"id": 12, "artist": "Sentenced", "title": "Noose"},
            },
        ]

    def get_now_playing(self, _station: str) -> dict[str, Any]:
        return self.now_playing_payload

    def get_upcoming_queue(self, _station: str) -> list[dict[str, Any]]:
        return self.queue_payload


class FakeResolver(TrackResolver):
    def __init__(
        self,
        *,
        available: dict[tuple[str, str], bool] | None = None,
        validations: dict[tuple[str, str], ValidationResult] | None = None,
    ) -> None:
        self.available = available or {}
        self.validations = validations or {}

    def is_available(self, song: Song) -> bool:
        return self.available.get((song.artist, song.title), True)

    def validate_song(self, song: Song) -> ValidationResult:
        return self.validations.get(
            (song.artist, song.title),
            ValidationResult(song=song.model_copy(update={"validated": True})),
        )

    def backfill_album(
        self,
        song: Song,
        *,
        log=print,
    ) -> tuple[Song, bool]:
        return song, False


class FakeMediaLibrary(MediaLibrary):
    def __init__(self) -> None:
        self.downloads: list[Path] = []
        self.deleted: list[Path] = []

    def apply_override(
        self,
        song: Song,
        song_path: Path | None,
        playlist_name: str,
        *,
        dry_run: bool,
        log: PlaylistLog,
    ) -> bool:
        return False

    def audit_existing_tags(
        self,
        existing_songs: list[tuple[Song, Path]],
        playlist_name: str,
        *,
        log: PlaylistLog,
    ) -> int:
        return 0

    def download_song(
        self,
        song: Song,
        song_path: Path,
        playlist_name: str,
        *,
        log: PlaylistLog,
    ) -> None:
        song_path.parent.mkdir(parents=True, exist_ok=True)
        song_path.write_bytes(b"fake mp3")
        self.downloads.append(song_path)

    def delete_file(
        self,
        song_path: Path,
        *,
        log: PlaylistLog,
    ) -> None:
        self.deleted.append(song_path)
        if song_path.exists():
            song_path.unlink()
