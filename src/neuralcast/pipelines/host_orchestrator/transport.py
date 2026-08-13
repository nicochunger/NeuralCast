"""AzuraCast API client and transport payload helpers."""

from __future__ import annotations

import base64
import pathlib
import warnings
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .config import HOST_ARTIST_NAME
from .models import QueueTrack
from .utils import track_key

try:
    import requests
except ModuleNotFoundError:  # pragma: no cover - dependency guard
    requests = None  # type: ignore[assignment]

if requests is not None:
    from requests import Response
    RequestsHTTPError = requests.HTTPError
else:  # pragma: no cover - dependency guard
    Response = Any  # type: ignore[misc,assignment]

    class RequestsHTTPError(Exception):
        pass

try:
    from urllib3.exceptions import InsecureRequestWarning
except ModuleNotFoundError:  # pragma: no cover - dependency guard
    class InsecureRequestWarning(Warning):
        pass


class AzuraCastClient:
    """Minimal AzuraCast API client used by the orchestrator."""

    def __init__(self, base_url: str, api_key: str, verify_tls: bool = False):
        if requests is None:
            raise RuntimeError(
                "requests package is required for AzuraCast API calls. Install with: pip install requests"
            )
        self.base_url = base_url.rstrip("/")
        self.verify_tls = verify_tls
        self.session = requests.Session()
        self.session.headers.update({"X-API-Key": api_key})

        if not verify_tls:
            warnings.filterwarnings("ignore", category=InsecureRequestWarning)

    def _build_url(self, path: str) -> str:
        return f"{self.base_url}/{path.lstrip('/')}"

    def _request(self, method: str, path: str, **kwargs: Any) -> Response:
        kwargs.setdefault("timeout", 15)
        kwargs.setdefault("verify", self.verify_tls)
        response = self.session.request(
            method=method, url=self._build_url(path), **kwargs
        )
        response.raise_for_status()
        return response

    def get_stations(self) -> List[Dict[str, Any]]:
        return self._request("GET", "/api/stations").json()

    def get_now_playing(self, station: str) -> Dict[str, Any]:
        try:
            return self._request("GET", f"/api/nowplaying/{station}").json()
        except RequestsHTTPError as exc:
            if exc.response is not None and exc.response.status_code == 404:
                payload = self._request("GET", "/api/nowplaying").json()
                for station_payload in payload:
                    shortcode = station_payload.get("station", {}).get("shortcode")
                    if shortcode == station:
                        return station_payload
            raise

    def get_upcoming_queue(self, station: str) -> List[Dict[str, Any]]:
        payload = self._request("GET", f"/api/station/{station}/queue").json()
        if isinstance(payload, dict) and isinstance(payload.get("data"), list):
            return payload["data"]
        if isinstance(payload, list):
            return payload
        return []

    def upload_media(
        self, station: str, file_path: pathlib.Path, remote_path: Optional[str] = None
    ) -> Dict[str, Any]:
        destination = remote_path or file_path.name
        payload = {
            "path": destination,
            "file": base64.b64encode(file_path.read_bytes()).decode("ascii"),
        }
        response = self._request("POST", f"/api/station/{station}/files", json=payload)
        return response.json()

    def send_telnet_command(self, station_id: int, command: str) -> Dict[str, Any]:
        payload = {"command": command}
        response = self._request(
            "PUT", f"/api/admin/debug/station/{station_id}/telnet", json=payload
        )
        return response.json()

    def list_media_files(self, station: str) -> List[Dict[str, Any]]:
        payload = self._request("GET", f"/api/station/{station}/files").json()
        if isinstance(payload, list):
            return payload
        return []

    def delete_media_file(self, station: str, media_id: int) -> Dict[str, Any]:
        return self._request("DELETE", f"/api/station/{station}/file/{media_id}").json()


def parse_queue_tracks(payload: Sequence[Dict[str, Any]]) -> List[QueueTrack]:
    tracks: List[QueueTrack] = []
    for idx, entry in enumerate(payload):
        song = entry.get("song") or {}
        artist = song.get("artist") or entry.get("artist") or ""
        title = song.get("title") or entry.get("title") or ""
        if not str(title).strip():
            continue

        duration_raw = entry.get("duration", entry.get("length"))
        duration: Optional[int] = None
        if duration_raw is not None:
            try:
                duration = int(duration_raw)
            except (TypeError, ValueError):
                duration = None

        queue_id = (
            entry.get("id")
            or entry.get("queue_id")
            or entry.get("unique_id")
            or song.get("id")
            or f"queue-{idx}"
        )

        tracks.append(
            QueueTrack(
                queue_id=str(queue_id),
                song_id=str(song.get("id")) if song.get("id") is not None else None,
                artist=str(artist),
                title=str(title),
                duration=duration,
                raw=dict(entry),
            )
        )
    return tracks


def extract_current_track(
    now_playing_payload: Mapping[str, Any],
) -> Tuple[QueueTrack, Optional[int]]:
    now_block = now_playing_payload.get("now_playing") or {}
    song = now_block.get("song") or {}
    artist = str(song.get("artist") or "").strip()
    title = str(song.get("title") or "").strip()
    if not title:
        raise RuntimeError("Now-playing payload did not include a current song title.")

    duration: Optional[int] = None
    for candidate in (now_block.get("duration"), song.get("length")):
        if candidate is None:
            continue
        try:
            duration = int(candidate)
            break
        except (TypeError, ValueError):
            continue

    remaining: Optional[int] = None
    remaining_raw = now_block.get("remaining")
    if remaining_raw is not None:
        try:
            remaining = int(remaining_raw)
        except (TypeError, ValueError):
            remaining = None

    track = QueueTrack(
        queue_id=str(song.get("id") or "now-playing"),
        song_id=str(song.get("id")) if song.get("id") is not None else None,
        artist=artist,
        title=title,
        duration=duration,
        raw=dict(now_block),
    )
    return track, remaining


def tracks_match(a: QueueTrack, b: QueueTrack) -> bool:
    if a.song_id and b.song_id and a.song_id == b.song_id:
        return True
    return track_key(a.artist, a.title) == track_key(b.artist, b.title)


def extract_current_listeners(now_playing_payload: Mapping[str, Any]) -> Optional[int]:
    listener_candidates: List[Any] = []
    if isinstance(now_playing_payload.get("listeners"), Mapping):
        listener_candidates.append(now_playing_payload["listeners"].get("current"))

    now_block = now_playing_payload.get("now_playing")
    if isinstance(now_block, Mapping) and isinstance(
        now_block.get("listeners"), Mapping
    ):
        listener_candidates.append(now_block["listeners"].get("current"))

    for candidate in listener_candidates:
        try:
            if candidate is not None:
                return int(candidate)
        except (TypeError, ValueError):
            continue
    return None


def choose_next_track(
    current: QueueTrack, queue_tracks: Sequence[QueueTrack]
) -> Optional[QueueTrack]:
    upcoming = choose_upcoming_tracks(current, queue_tracks, limit=1)
    return upcoming[0] if upcoming else None


def choose_upcoming_tracks(
    current: QueueTrack,
    queue_tracks: Sequence[QueueTrack],
    limit: int = 4,
) -> List[QueueTrack]:
    if limit <= 0:
        return []

    upcoming: List[QueueTrack] = []
    for candidate in queue_tracks:
        if tracks_match(candidate, current):
            continue
        upcoming.append(candidate)
        if len(upcoming) >= limit:
            break
    return upcoming


def derive_station_display_name(
    station_payload: Mapping[str, Any], fallback: str
) -> str:
    name = str(station_payload.get("name") or "").strip()
    return name or fallback


def choose_station_payload(
    stations: Sequence[Mapping[str, Any]], station: str
) -> Mapping[str, Any]:
    normalized = station.strip().lower()
    station_entry = next(
        (
            entry
            for entry in stations
            if str(entry.get("shortcode") or entry.get("station_short_name"))
            .strip()
            .lower()
            == normalized
        ),
        None,
    )
    if station_entry is not None:
        return station_entry
    available = ", ".join(str(entry.get("shortcode") or "?") for entry in stations)
    raise RuntimeError(f"Station '{station}' not found. Available: {available}")


def build_request_command(
    media_full_path: str,
    title: str,
    duration: Optional[int],
    *,
    media_id: str,
    song_id: Optional[str] = None,
) -> str:
    artist = HOST_ARTIST_NAME

    def _escape(value: str) -> str:
        return value.replace("\\", "\\\\").replace('"', '\\"')

    annotations = [
        f'title="{_escape(title)}"',
        f'artist="{_escape(artist)}"',
        f'media_id="{_escape(media_id)}"',
    ]
    if song_id:
        annotations.append(f'song_id="{_escape(song_id)}"')
    if duration is not None and duration > 0:
        annotations.append(f'duration="{duration}"')
    return f"requests.push annotate:{','.join(annotations)}:{media_full_path}"


def extract_upload_storage_path(upload_response: Mapping[str, Any]) -> Optional[str]:
    path = upload_response.get("path")
    if isinstance(path, str) and path.strip():
        return path.strip()

    data = upload_response.get("data")
    if isinstance(data, Mapping):
        for key in ("path", "storage_location"):
            candidate = data.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
    return None


def extract_upload_duration(upload_response: Mapping[str, Any]) -> Optional[int]:
    candidates = [upload_response.get("length")]
    if isinstance(upload_response.get("data"), Mapping):
        candidates.append(upload_response["data"].get("length"))
    for candidate in candidates:
        if candidate is None:
            continue
        try:
            return int(float(candidate))
        except (TypeError, ValueError):
            continue
    return None


def _extract_upload_value(
    upload_response: Mapping[str, Any], key: str
) -> Optional[str]:
    candidates = [upload_response.get(key)]
    data = upload_response.get("data")
    if isinstance(data, Mapping):
        candidates.append(data.get(key))

    for candidate in candidates:
        if candidate is None:
            continue
        value = str(candidate).strip()
        if value:
            return value
    return None


def extract_upload_media_id(upload_response: Mapping[str, Any]) -> Optional[str]:
    return _extract_upload_value(upload_response, "id")


def extract_upload_song_id(upload_response: Mapping[str, Any]) -> Optional[str]:
    return _extract_upload_value(upload_response, "song_id")


def extract_telnet_request_id(response_payload: Mapping[str, Any]) -> Optional[str]:
    logs = response_payload.get("logs")
    if not isinstance(logs, list):
        return None
    for entry in reversed(logs):
        context = entry.get("context")
        if not isinstance(context, Mapping):
            continue
        lines = context.get("response")
        if isinstance(lines, list) and lines:
            return str(lines[-1])
    return None
