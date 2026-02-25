"""Retry and local state persistence helpers for schedule generation."""

from __future__ import annotations

import json
import pathlib
import time
from typing import Any, Mapping, Optional, Sequence

from neuralcast.config import PROJECT_ROOT

from .config import LOGGER, STATE_FILENAME

def run_with_retries(
    label: str,
    func,
    retries: int = 2,
    delays: Sequence[int] = (2, 5),
):
    attempts = retries + 1
    for index in range(attempts):
        try:
            return func()
        except Exception as exc:  # noqa: BLE001
            if index >= attempts - 1:
                raise
            delay = delays[index] if index < len(delays) else delays[-1]
            LOGGER.warning(
                "[retry] %s failed (%s/%s): %s. Retrying in %ss.",
                label,
                index + 1,
                attempts,
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

    return direct


def schedule_state_path(station: str) -> pathlib.Path:
    station_dir = resolve_station_dir(station)
    metadata_dir = station_dir / "metadata"
    metadata_dir.mkdir(parents=True, exist_ok=True)
    return metadata_dir / STATE_FILENAME



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


