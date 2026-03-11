"""Shared Deezer API helpers for anonymous metadata lookups."""

from __future__ import annotations

import time
from datetime import datetime
from typing import Optional

import requests
from requests import Session

SESSION: Session = requests.Session()
SESSION.headers.update({"User-Agent": "NeuralCast/1.0"})

_API_BASE_URL = "https://api.deezer.com"
_REQUEST_TIMEOUT = 15
_MAX_API_RETRIES = 3
_MIN_REQUEST_INTERVAL_SECONDS = 0.25
_QUOTA_BACKOFF_SECONDS = (5, 15, 30)

_LAST_REQUEST_TS = 0.0
_QUOTA_PAUSE_UNTIL = 0.0


def parse_release_date(date_str: str | None) -> Optional[datetime]:
    value = (date_str or "").strip()
    if not value:
        return None
    try:
        if len(value) == 10:
            return datetime.strptime(value, "%Y-%m-%d")
        if len(value) == 7:
            return datetime.strptime(value, "%Y-%m")
        if len(value) == 4:
            return datetime.strptime(value, "%Y")
    except ValueError:
        return None
    return None


def deezer_get(
    resource: str, *, params: Optional[dict[str, object]] = None
) -> Optional[dict]:
    global _LAST_REQUEST_TS
    global _QUOTA_PAUSE_UNTIL

    url = resource if resource.startswith("http") else f"{_API_BASE_URL}{resource}"
    for attempt in range(1, _MAX_API_RETRIES + 1):
        now = time.monotonic()
        if _QUOTA_PAUSE_UNTIL > now:
            time.sleep(_QUOTA_PAUSE_UNTIL - now)

        now = time.monotonic()
        elapsed = now - _LAST_REQUEST_TS
        if elapsed < _MIN_REQUEST_INTERVAL_SECONDS:
            time.sleep(_MIN_REQUEST_INTERVAL_SECONDS - elapsed)

        try:
            response = SESSION.get(url, params=params, timeout=_REQUEST_TIMEOUT)
            _LAST_REQUEST_TS = time.monotonic()
            if response.status_code == 429:
                retry_after = int(response.headers.get("Retry-After", "3"))
                time.sleep(retry_after)
                continue
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException:
            if attempt >= _MAX_API_RETRIES:
                return None
            time.sleep(attempt)
            continue
        except ValueError:
            return None

        if isinstance(payload, dict) and payload.get("error"):
            error = payload["error"]
            error_code = str(error.get("code", "")).strip()
            if error_code == "4":
                backoff = _QUOTA_BACKOFF_SECONDS[
                    min(attempt - 1, len(_QUOTA_BACKOFF_SECONDS) - 1)
                ]
                _QUOTA_PAUSE_UNTIL = max(
                    _QUOTA_PAUSE_UNTIL, time.monotonic() + backoff
                )
                if attempt < _MAX_API_RETRIES:
                    continue
            return None

        if not isinstance(payload, dict):
            return None
        return payload

    return None


def _payload_items(payload: Optional[dict]) -> list[dict]:
    if not payload:
        return []
    data = payload.get("data")
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


def search_tracks(query: str, *, limit: int = 10) -> list[dict]:
    return _payload_items(
        deezer_get("/search/track", params={"q": query, "limit": limit})
    )


def search_albums(query: str, *, limit: int = 10) -> list[dict]:
    return _payload_items(
        deezer_get("/search/album", params={"q": query, "limit": limit})
    )


def get_album(album_id: str) -> Optional[dict]:
    album_id = str(album_id or "").strip()
    if not album_id:
        return None
    payload = deezer_get(f"/album/{album_id}")
    if not isinstance(payload, dict):
        return None
    return payload


def get_track(track_id: str) -> Optional[dict]:
    track_id = str(track_id or "").strip()
    if not track_id:
        return None
    payload = deezer_get(f"/track/{track_id}")
    if not isinstance(payload, dict):
        return None
    return payload


__all__ = [
    "deezer_get",
    "search_tracks",
    "search_albums",
    "get_album",
    "get_track",
    "parse_release_date",
]
