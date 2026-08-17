"""Shared utility helpers for host orchestrator modules."""

from __future__ import annotations

import datetime as dt
import pathlib
import re
import time
from typing import Any, Callable, Sequence, Tuple

from neuralcast.config import station_dir_from_slug
from .channels import resolve_host_channel
from .config import (
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


def resolve_station_dir(station_or_channel: str) -> pathlib.Path:
    try:
        channel = resolve_host_channel(channel_key=station_or_channel)
    except ValueError:
        return station_dir_from_slug(station_or_channel)
    return channel.content_station_dir


def station_state_paths(
    station_or_channel: str,
) -> Tuple[pathlib.Path, pathlib.Path, pathlib.Path]:
    try:
        channel = resolve_host_channel(channel_key=station_or_channel)
    except ValueError:
        channel = resolve_host_channel(station_slug=station_or_channel)

    station_dir = channel.content_station_dir
    metadata_dir = station_dir / "metadata"
    if channel.legacy_station is None:
        metadata_dir = metadata_dir / "host_channels" / channel.key
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
