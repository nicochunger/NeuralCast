"""Shared utility helpers for host orchestrator modules."""

from __future__ import annotations

import datetime as dt
import pathlib
import re
import time
from typing import Any, Callable, Sequence, Tuple

from neuralcast.config import PROJECT_ROOT
from neuralcast.pipelines.host_orchestrator_config import (
    GENERATION_RETRIES,
    GENERATION_RETRY_DELAYS,
    LOCK_FILENAME,
    LOGGER,
    STATE_FILENAME,
)


def run_with_retries(
    label: str,
    func: Callable[[], Any],
    retries: int = GENERATION_RETRIES,
    delays: Sequence[int] = GENERATION_RETRY_DELAYS,
) -> Any:
    attempts = retries + 1
    for idx in range(attempts):
        try:
            return func()
        except Exception as exc:  # noqa: BLE001
            if idx >= attempts - 1:
                raise
            delay = delays[idx] if idx < len(delays) else delays[-1]
            LOGGER.warning(
                "[retry] %s failed (%s/%s): %s: %s. Retrying in %ss.",
                label,
                idx + 1,
                attempts,
                type(exc).__name__,
                exc,
                delay,
            )
            time.sleep(delay)


def resolve_station_dir(station: str) -> pathlib.Path:
    direct = PROJECT_ROOT / station
    if direct.exists():
        return direct

    lowered = station.lower()
    for candidate in PROJECT_ROOT.iterdir():
        if not candidate.is_dir():
            continue
        if candidate.name.lower() == lowered:
            return candidate

    # Fallback keeps behavior deterministic for new stations.
    return direct


def station_state_paths(
    station: str,
) -> Tuple[pathlib.Path, pathlib.Path, pathlib.Path]:
    station_dir = resolve_station_dir(station)
    metadata_dir = station_dir / "metadata"
    metadata_dir.mkdir(parents=True, exist_ok=True)
    return station_dir, metadata_dir / STATE_FILENAME, metadata_dir / LOCK_FILENAME


def now_ts() -> float:
    return time.time()


def iso_utc(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts, tz=dt.timezone.utc).isoformat()


def normalize_component(value: str) -> str:
    cleaned = (value or "").strip().lower()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned


def track_key(artist: str, title: str) -> str:
    return f"{normalize_component(artist)}|{normalize_component(title)}"
