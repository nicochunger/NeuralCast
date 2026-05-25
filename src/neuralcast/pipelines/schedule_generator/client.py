"""AzuraCast transport and schedule application helpers."""

from __future__ import annotations

import json
import pathlib
import warnings
from typing import Any, Dict, List, Mapping, Sequence, Tuple
from zoneinfo import ZoneInfo

from .config import (
    FALLBACK_TIMEZONE,
    InsecureRequestWarning,
    RequestsHTTPError,
    Response,
    requests,
)
from .models import DailyTemplateBlock, StationPlaylist
from .state import run_with_retries
from .template import parse_hhmm

class AzuraCastClient:
    """Minimal AzuraCast API client for weekly scheduling operations."""

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
        kwargs.setdefault("timeout", 20)
        kwargs.setdefault("verify", self.verify_tls)
        response = self.session.request(
            method=method,
            url=self._build_url(path),
            **kwargs,
        )
        response.raise_for_status()
        return response

    def get_stations(self) -> List[Dict[str, Any]]:
        payload = self._request("GET", "/api/stations").json()
        return payload if isinstance(payload, list) else []

    def get_station_playlists(self, station: str) -> List[Dict[str, Any]]:
        payload = self._request("GET", f"/api/station/{station}/playlists").json()
        if isinstance(payload, list):
            return payload
        if isinstance(payload, Mapping) and isinstance(payload.get("data"), list):
            return payload["data"]
        return []

    def update_playlist_schedule_items(
        self,
        station: str,
        playlist_id: str,
        schedule_items: Sequence[Mapping[str, Any]],
    ) -> Dict[str, Any]:
        path = f"/api/station/{station}/playlist/{playlist_id}"
        payload = {"schedule_items": list(schedule_items)}

        errors: List[str] = []
        for method in ("PUT", "PATCH"):
            try:
                response = self._request(method, path, json=payload)
                raw = response.json()
                return raw if isinstance(raw, dict) else {"data": raw}
            except RequestsHTTPError as exc:
                detail = ""
                if exc.response is not None:
                    try:
                        detail = exc.response.text.strip()
                    except Exception:  # noqa: BLE001
                        detail = ""
                errors.append(f"{method} failed: {exc} {detail}".strip())

        joined = " | ".join(errors) if errors else "unknown error"
        raise RuntimeError(
            f"Unable to update schedule_items for playlist {playlist_id}: {joined}"
        )


def infer_azuracast_days(playlists: Sequence[StationPlaylist]) -> List[int]:
    inferred: List[int] = []
    saw_empty_days_list = False
    for playlist in playlists:
        for item in playlist.schedule_items:
            days = item.get("days")
            if not isinstance(days, list):
                continue
            if not days:
                saw_empty_days_list = True
            for day in days:
                try:
                    inferred.append(int(day))
                except (TypeError, ValueError):
                    continue

    if inferred:
        day_values = sorted(set(inferred))
        # Older/generated schedules may use 0..6. AzuraCast expects ISO-8601 1..7.
        if 0 in day_values and 7 not in day_values and all(0 <= day <= 6 for day in day_values):
            return sorted({day + 1 for day in day_values})
        return day_values

    # Some stations store "all days" as an explicit empty list; preserve that shape.
    if saw_empty_days_list:
        return []

    # Default to seven-day coverage when there is no prior schedule shape to infer.
    return [1, 2, 3, 4, 5, 6, 7]


def azuracast_time_for_api(value: str) -> int:
    minutes = parse_hhmm(value, allow_24=True)
    if minutes == 24 * 60:
        # AzuraCast accepts HHMM integer values; use 23:59 for day-end boundaries.
        return 2359
    hour = minutes // 60
    minute = minutes % 60
    return (hour * 100) + minute


def build_schedule_items_by_playlist(
    playlists: Sequence[StationPlaylist],
    daily_template: Sequence[DailyTemplateBlock],
    day_values: Sequence[int],
) -> Dict[str, List[Dict[str, Any]]]:
    items_by_playlist: Dict[str, List[Dict[str, Any]]] = {
        playlist.id: [] for playlist in playlists
    }
    enabled_playlist_ids = {
        playlist.id for playlist in playlists if playlist.is_enabled
    }

    for block in daily_template:
        item = {
            "start_time": azuracast_time_for_api(block.start_time_local),
            "end_time": azuracast_time_for_api(block.end_time_local),
            "days": list(day_values),
        }

        target_playlist_ids: Sequence[str]
        if block.mode == "playlist":
            target_playlist_ids = [
                playlist_id
                for playlist_id in (block.playlist_ids or ([block.playlist_id] if block.playlist_id else []))
                if playlist_id and playlist_id in items_by_playlist
            ]
            if not target_playlist_ids:
                continue
        elif block.mode == "open":
            target_playlist_ids = [
                playlist_id
                for playlist_id in items_by_playlist.keys()
                if playlist_id in enabled_playlist_ids
            ]
        else:
            continue

        for playlist_id in target_playlist_ids:
            items_by_playlist[playlist_id].append(dict(item))

    return items_by_playlist



def extract_station_playlists(payload: Sequence[Mapping[str, Any]]) -> List[StationPlaylist]:
    playlists: List[StationPlaylist] = []
    for entry in payload:
        playlist_id = entry.get("id")
        name = str(entry.get("name") or "").strip()
        if playlist_id in (None, "") or not name:
            continue

        is_enabled = bool(entry.get("is_enabled", True))
        weight_raw = entry.get("weight", 1)
        try:
            weight = float(weight_raw)
        except (TypeError, ValueError):
            weight = 1.0

        schedule_items = entry.get("schedule_items")
        if not isinstance(schedule_items, list):
            schedule_items = []

        playlists.append(
            StationPlaylist(
                id=str(playlist_id),
                name=name,
                is_enabled=is_enabled,
                weight=weight,
                schedule_items=[
                    dict(item) for item in schedule_items if isinstance(item, Mapping)
                ],
                raw=dict(entry),
            )
        )
    return playlists


def choose_station_payload(
    stations: Sequence[Mapping[str, Any]], station_slug: str
) -> Mapping[str, Any]:
    normalized = station_slug.strip().lower()
    for entry in stations:
        shortcode = str(entry.get("shortcode") or entry.get("station_short_name") or "").strip().lower()
        if shortcode == normalized:
            return entry
    available = ", ".join(
        str(entry.get("shortcode") or entry.get("station_short_name") or "?")
        for entry in stations
    )
    raise RuntimeError(f"Station '{station_slug}' not found. Available: {available}")


def derive_station_name(station_payload: Mapping[str, Any], fallback: str) -> str:
    name = str(station_payload.get("name") or "").strip()
    return name or fallback


def derive_station_timezone(station_payload: Mapping[str, Any]) -> str:
    candidates: List[str] = []
    for key in ("timezone", "tz", "time_zone"):
        value = station_payload.get(key)
        if isinstance(value, str) and value.strip():
            candidates.append(value.strip())

    nested_station = station_payload.get("station")
    if isinstance(nested_station, Mapping):
        for key in ("timezone", "tz", "time_zone"):
            value = nested_station.get(key)
            if isinstance(value, str) and value.strip():
                candidates.append(value.strip())

    for candidate in candidates:
        try:
            ZoneInfo(candidate)
            return candidate
        except Exception:  # noqa: BLE001
            continue

    return FALLBACK_TIMEZONE


def load_schedule_state(path: pathlib.Path) -> Optional[Mapping[str, Any]]:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    return payload if isinstance(payload, Mapping) else None


def save_schedule_state_atomic(path: pathlib.Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    tmp_path.replace(path)


def apply_weekly_schedule(
    client: AzuraCastClient,
    station_slug: str,
    playlists: Sequence[StationPlaylist],
    daily_template: Sequence[DailyTemplateBlock],
) -> Tuple[int, int]:
    day_values = infer_azuracast_days(playlists)
    items_by_playlist = build_schedule_items_by_playlist(
        playlists=playlists,
        daily_template=daily_template,
        day_values=day_values,
    )

    updated_playlists = 0
    updated_items = 0
    for playlist in playlists:
        schedule_items = items_by_playlist.get(playlist.id, [])
        run_with_retries(
            f"Update schedule for playlist {playlist.name}",
            lambda playlist_id=playlist.id, items=schedule_items: client.update_playlist_schedule_items(
                station=station_slug,
                playlist_id=playlist_id,
                schedule_items=items,
            ),
        )
        updated_playlists += 1
        updated_items += len(schedule_items)

    return updated_playlists, updated_items

