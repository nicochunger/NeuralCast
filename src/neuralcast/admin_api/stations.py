"""Read-only station helpers for the NeuralCast admin API."""

from __future__ import annotations

import os
from typing import Any, Callable

from neuralcast.pipelines.host_orchestrator.transport import (
    AzuraCastClient,
    choose_upcoming_tracks,
    extract_current_listeners,
    extract_current_track,
    parse_queue_tracks,
)
from neuralcast.pipelines.host_orchestrator.channels import resolve_host_channel

from .jobs import SUPPORTED_HOST_CHANNELS, SUPPORTED_STATIONS

ClientFactory = Callable[[], AzuraCastClient]


class StationServiceConfigError(RuntimeError):
    """Raised when the admin API cannot build its AzuraCast client."""


def _serialize_track(track: Any) -> dict[str, Any]:
    return {
        "queue_id": track.queue_id,
        "song_id": track.song_id,
        "artist": track.artist,
        "title": track.title,
        "duration_seconds": track.duration,
    }


class AdminStationService:
    """Read live station state from AzuraCast for admin HTTP endpoints."""

    def __init__(self, client_factory: ClientFactory | None = None) -> None:
        self._client_factory = client_factory or self._build_client_from_env

    def now_playing(self, station: str) -> dict[str, Any]:
        target_station = self._target_station(station)

        client = self._client_factory()
        payload = client.get_now_playing(target_station)
        current_track, remaining_seconds = extract_current_track(payload)
        listener_count = extract_current_listeners(payload)

        return {
            "station": station,
            "current_track": _serialize_track(current_track),
            "remaining_seconds": remaining_seconds,
            "listener_count": listener_count,
        }

    def queue(self, station: str, *, limit: int = 4) -> dict[str, Any]:
        target_station = self._target_station(station)

        client = self._client_factory()
        queue_payload = client.get_upcoming_queue(target_station)
        queue_tracks = parse_queue_tracks(queue_payload)

        try:
            now_playing_payload = client.get_now_playing(target_station)
            current_track, _remaining_seconds = extract_current_track(now_playing_payload)
            items = choose_upcoming_tracks(
                current=current_track,
                queue_tracks=queue_tracks,
                limit=limit,
            )
        except Exception:  # noqa: BLE001
            items = list(queue_tracks[:limit])

        next_track = items[0] if items else None
        return {
            "station": station,
            "items": [_serialize_track(track) for track in items],
            "next_track": (_serialize_track(next_track) if next_track is not None else None),
        }

    def _target_station(self, station: str) -> str:
        if station in SUPPORTED_HOST_CHANNELS:
            return resolve_host_channel(channel_key=station).azuracast_station
        if station not in SUPPORTED_STATIONS:
            raise ValueError(
                "Unsupported station or host channel "
                f"'{station}'. Allowed values: {SUPPORTED_STATIONS + SUPPORTED_HOST_CHANNELS}."
            )
        return station

    @staticmethod
    def _build_client_from_env() -> AzuraCastClient:
        base_url = str(os.getenv("AZURACAST_BASE_URL") or "").strip()
        if not base_url:
            raise StationServiceConfigError("AZURACAST_BASE_URL is not configured.")

        api_key = os.getenv("AZURACAST_API_KEY")
        if not api_key:
            raise StationServiceConfigError("AZURACAST_API_KEY is not configured.")

        return AzuraCastClient(
            base_url=base_url.rstrip("/"),
            api_key=api_key,
            verify_tls=False,
        )
