"""Weekly AI schedule generation and AzuraCast schedule application."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import logging
import math
import os
import pathlib
import random
import re
import time
import warnings
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

try:
    import requests
except ModuleNotFoundError:  # pragma: no cover - dependency guard
    requests = None  # type: ignore[assignment]

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:  # pragma: no cover - dependency guard
    def load_dotenv(*_args: Any, **_kwargs: Any) -> bool:
        return False

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

from neuralcast.config import ASSETS_ROOT, PROJECT_ROOT
from neuralcast.services.openai_client import get_gemini_client

LOGGER = logging.getLogger(pathlib.Path(__file__).stem)

PROMPTS_DIR = ASSETS_ROOT / "stories" / "prompts"
SCHEDULE_SYSTEM_PROMPT_PATH = PROMPTS_DIR / "schedule_system.md"
SCHEDULE_USER_PROMPT_PATH = PROMPTS_DIR / "schedule_user.md"

STATE_FILENAME = "ai_schedule_state.json"
STATE_VERSION = 1

DEFAULT_OPEN_RATIO_MIN = 0.20
DEFAULT_OPEN_RATIO_MAX = 0.40
DEFAULT_MIN_BLOCK_MINUTES = 30
DEFAULT_MAX_BLOCK_MINUTES = 240
DEFAULT_TEMPLATE_TARGET_BLOCK_MINUTES = 180
UNSCHEDULED_WINDOW_START_MINUTE = 22 * 60
UNSCHEDULED_WINDOW_END_MINUTE = 6 * 60
UNSCHEDULED_WINDOW_TOTAL_MINUTES = (
    (24 * 60 - UNSCHEDULED_WINDOW_START_MINUTE) + UNSCHEDULED_WINDOW_END_MINUTE
)

GENERATION_MAX_ATTEMPTS = 2
DEFAULT_GEMINI_MODEL = os.getenv("GEMINI_TEXT_MODEL", "gemini-3-flash-preview")

FALLBACK_TIMEZONE = "Europe/Zurich"


class ScheduleValidationError(ValueError):
    """Raised when generated schedule data does not satisfy hard rules."""


@dataclass
class StationPlaylist:
    id: str
    name: str
    is_enabled: bool
    weight: float
    schedule_items: List[Dict[str, Any]]
    raw: Dict[str, Any]


@dataclass
class DailyTemplateBlock:
    start_time_local: str
    end_time_local: str
    start_minute: int
    end_minute: int
    mode: str
    section_label: str
    genre_labels: List[str]
    playlist_id: Optional[str] = None
    playlist_name: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "start_time_local": self.start_time_local,
            "end_time_local": self.end_time_local,
            "start_minute": self.start_minute,
            "end_minute": self.end_minute,
            "mode": self.mode,
            "section_label": self.section_label,
            "genre_labels": list(self.genre_labels),
            "playlist_id": self.playlist_id,
            "playlist_name": self.playlist_name,
        }


@dataclass
class ExpandedScheduleBlock:
    block_key: str
    day_of_week: int
    date_local: str
    start_time_local: str
    end_time_local: str
    mode: str
    section_label: str
    genre_labels: List[str]
    playlist_id: Optional[str]
    playlist_name: Optional[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "block_key": self.block_key,
            "day_of_week": self.day_of_week,
            "date_local": self.date_local,
            "start_time_local": self.start_time_local,
            "end_time_local": self.end_time_local,
            "mode": self.mode,
            "section_label": self.section_label,
            "genre_labels": list(self.genre_labels),
            "playlist_id": self.playlist_id,
            "playlist_name": self.playlist_name,
        }


@dataclass
class WeeklySchedulePlan:
    station: str
    station_name: str
    timezone: str
    week_start_local_date: str
    week_end_local_date: str
    generated_at_utc: str
    open_ratio_min: float
    open_ratio_max: float
    daily_template: List[DailyTemplateBlock]
    expanded_blocks: List[ExpandedScheduleBlock]
    rationale: str
    plan_hash: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "state_version": STATE_VERSION,
            "station": self.station,
            "station_name": self.station_name,
            "timezone": self.timezone,
            "week_start_local_date": self.week_start_local_date,
            "week_end_local_date": self.week_end_local_date,
            "generated_at_utc": self.generated_at_utc,
            "open_ratio_min": self.open_ratio_min,
            "open_ratio_max": self.open_ratio_max,
            "daily_template": [block.to_dict() for block in self.daily_template],
            "expanded_blocks": [block.to_dict() for block in self.expanded_blocks],
            "rationale": self.rationale,
            "plan_hash": self.plan_hash,
        }


def configure_logging(level: int = logging.INFO) -> None:
    if LOGGER.handlers:
        return

    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s | %(name)s | %(levelname)-7s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    LOGGER.addHandler(handler)
    LOGGER.setLevel(level)
    LOGGER.propagate = False


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


def strip_code_fences(text: str) -> str:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?", "", cleaned, flags=re.IGNORECASE).strip()
    cleaned = re.sub(r"```$", "", cleaned).strip()
    return cleaned


def extract_json_object(text: str) -> Mapping[str, Any]:
    cleaned = strip_code_fences(text)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end < 0 or end <= start:
        raise ScheduleValidationError("No JSON object found in model output.")

    candidate = cleaned[start : end + 1]
    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise ScheduleValidationError(f"Model output contains invalid JSON: {exc}") from exc

    if not isinstance(payload, Mapping):
        raise ScheduleValidationError("Top-level model output must be a JSON object.")
    return payload


def parse_hhmm(value: str, *, allow_24: bool = False) -> int:
    text = (value or "").strip()
    match = re.fullmatch(r"([01]?\d|2[0-3]):([0-5]\d)", text)
    if match:
        hour = int(match.group(1))
        minute = int(match.group(2))
        return hour * 60 + minute

    if allow_24 and text == "24:00":
        return 24 * 60

    raise ScheduleValidationError(f"Invalid HH:MM time value: '{value}'")


def format_hhmm(minutes: int) -> str:
    if minutes == 24 * 60:
        return "24:00"
    hours = minutes // 60
    mins = minutes % 60
    return f"{hours:02d}:{mins:02d}"


def overlaps_unscheduled_window(start_minute: int, end_minute: int) -> bool:
    if start_minute < 0 or end_minute > 24 * 60 or end_minute <= start_minute:
        return False

    if start_minute < UNSCHEDULED_WINDOW_END_MINUTE:
        return True
    if end_minute > UNSCHEDULED_WINDOW_START_MINUTE:
        return True
    return False


def normalize_mode(value: Any) -> str:
    normalized = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if normalized in {"playlist", "assigned"}:
        return "playlist"
    if normalized in {"open", "unassigned", "weighted", "rotation"}:
        return "open"
    raise ScheduleValidationError(f"Unsupported block mode '{value}'.")


def normalize_genre_labels(value: Any) -> List[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value or "").strip()
    if not text:
        return []
    return [chunk.strip() for chunk in text.split(",") if chunk.strip()]


def validate_daily_template(
    raw_blocks: Sequence[Mapping[str, Any]],
    playlist_by_id: Mapping[str, StationPlaylist],
    open_ratio_min: float,
    open_ratio_max: float,
    min_block_minutes: int,
    max_block_minutes: int,
    required_time_ranges: Optional[Sequence[Tuple[int, int]]] = None,
) -> List[DailyTemplateBlock]:
    if not isinstance(raw_blocks, Sequence) or not raw_blocks:
        raise ScheduleValidationError("daily_template must be a non-empty array.")

    playlist_name_map = {
        playlist.name.strip().lower(): playlist
        for playlist in playlist_by_id.values()
        if playlist.name.strip()
    }

    parsed_blocks: List[DailyTemplateBlock] = []
    for entry in raw_blocks:
        if not isinstance(entry, Mapping):
            raise ScheduleValidationError("Each daily_template item must be an object.")

        start_time = str(entry.get("start_time_local") or "").strip()
        end_time = str(entry.get("end_time_local") or "").strip()
        start_minute = parse_hhmm(start_time)
        end_minute = parse_hhmm(end_time, allow_24=True)
        if end_minute <= start_minute:
            raise ScheduleValidationError(
                f"Block end must be after start ({start_time} -> {end_time})."
            )

        duration_minutes = end_minute - start_minute
        if duration_minutes < min_block_minutes or duration_minutes > max_block_minutes:
            raise ScheduleValidationError(
                f"Block duration {duration_minutes} is outside allowed range "
                f"[{min_block_minutes}, {max_block_minutes}] for {start_time}-{end_time}."
            )

        mode = normalize_mode(entry.get("mode"))
        section_label = str(entry.get("section_label") or "").strip()
        genres = normalize_genre_labels(entry.get("genre_labels"))
        overlaps_quiet_hours = overlaps_unscheduled_window(start_minute, end_minute)

        playlist_id: Optional[str] = None
        playlist_name: Optional[str] = None
        if mode == "playlist":
            if overlaps_quiet_hours:
                raise ScheduleValidationError(
                    f"Playlist blocks are not allowed between 22:00 and 06:00 "
                    f"({start_time}-{end_time}). Use mode='open'."
                )
            raw_playlist_id = entry.get("playlist_id")
            if raw_playlist_id not in (None, ""):
                playlist_id = str(raw_playlist_id).strip()

            raw_playlist_name = str(entry.get("playlist_name") or "").strip()
            if playlist_id is None and raw_playlist_name:
                resolved = playlist_name_map.get(raw_playlist_name.lower())
                if resolved is not None:
                    playlist_id = resolved.id

            if not playlist_id:
                raise ScheduleValidationError(
                    "Playlist blocks require playlist_id (or a resolvable playlist_name)."
                )
            if playlist_id not in playlist_by_id:
                raise ScheduleValidationError(
                    f"Unknown playlist_id '{playlist_id}' in block {start_time}-{end_time}."
                )

            playlist = playlist_by_id[playlist_id]
            playlist_name = playlist.name
            if not playlist.is_enabled:
                raise ScheduleValidationError(
                    f"Playlist '{playlist.name}' is disabled and cannot be scheduled explicitly."
                )
            # Enforce canonical block naming for scheduled playlist blocks.
            # We still allow custom labels for open blocks.
            section_label = playlist.name
            if not genres:
                genres = [playlist.name]
        else:
            playlist_id = None
            playlist_name = None
            if not section_label:
                section_label = "Rotacion abierta"
            if not genres:
                genres = ["mix variado"]

        parsed_blocks.append(
            DailyTemplateBlock(
                start_time_local=start_time,
                end_time_local=end_time,
                start_minute=start_minute,
                end_minute=end_minute,
                mode=mode,
                section_label=section_label,
                genre_labels=genres,
                playlist_id=playlist_id,
                playlist_name=playlist_name,
            )
        )

    parsed_blocks.sort(key=lambda block: block.start_minute)

    cursor = 0
    for block in parsed_blocks:
        if block.start_minute != cursor:
            raise ScheduleValidationError(
                f"Template must cover all day without gaps/overlaps. "
                f"Expected next block to start at {format_hhmm(cursor)}, got {block.start_time_local}."
            )
        cursor = block.end_minute
    if cursor != 24 * 60:
        raise ScheduleValidationError(
            f"Template must end at 24:00, got {format_hhmm(cursor)}."
        )

    if required_time_ranges is not None:
        actual_ranges = [
            (block.start_minute, block.end_minute) for block in parsed_blocks
        ]
        expected_ranges = list(required_time_ranges)
        if actual_ranges != expected_ranges:
            raise ScheduleValidationError(
                "Template block boundaries must match deterministic scaffold."
            )

    open_minutes = sum(
        block.end_minute - block.start_minute
        for block in parsed_blocks
        if block.mode == "open"
    )
    open_ratio = open_minutes / (24 * 60)
    if open_ratio < open_ratio_min or open_ratio > open_ratio_max:
        raise ScheduleValidationError(
            f"Open-slot ratio {open_ratio:.3f} outside bounds [{open_ratio_min:.2f}, {open_ratio_max:.2f}]."
        )

    if all(block.mode == "open" for block in parsed_blocks):
        raise ScheduleValidationError(
            "Template cannot be fully open; at least one playlist block is required."
        )

    return parsed_blocks


def compute_week_start(today_local: dt.date) -> dt.date:
    return today_local - dt.timedelta(days=today_local.weekday())


def build_plan_hash(
    station: str,
    timezone_name: str,
    week_start: dt.date,
    daily_template: Sequence[DailyTemplateBlock],
) -> str:
    canonical = {
        "station": station.strip().lower(),
        "timezone": timezone_name,
        "week_start_local_date": week_start.isoformat(),
        "daily_template": [block.to_dict() for block in daily_template],
    }
    raw = json.dumps(canonical, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def expand_daily_template_to_week(
    daily_template: Sequence[DailyTemplateBlock],
    week_start: dt.date,
) -> List[ExpandedScheduleBlock]:
    expanded: List[ExpandedScheduleBlock] = []
    for day_offset in range(7):
        date_local = week_start + dt.timedelta(days=day_offset)
        for block_index, block in enumerate(daily_template):
            block_key = "|".join(
                [
                    date_local.isoformat(),
                    str(block_index),
                    block.start_time_local,
                    block.end_time_local,
                    block.mode,
                    block.playlist_id or "open",
                ]
            )
            expanded.append(
                ExpandedScheduleBlock(
                    block_key=block_key,
                    day_of_week=day_offset,
                    date_local=date_local.isoformat(),
                    start_time_local=block.start_time_local,
                    end_time_local=block.end_time_local,
                    mode=block.mode,
                    section_label=block.section_label,
                    genre_labels=list(block.genre_labels),
                    playlist_id=block.playlist_id,
                    playlist_name=block.playlist_name,
                )
            )
    return expanded


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
        return sorted(set(inferred))

    # Some stations store "all days" as an explicit empty list; preserve that shape.
    if saw_empty_days_list:
        return []

    # Default to seven-day coverage when there is no prior schedule shape to infer.
    return [0, 1, 2, 3, 4, 5, 6]


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

    for block in daily_template:
        if block.mode != "playlist" or not block.playlist_id:
            continue

        if block.playlist_id not in items_by_playlist:
            continue

        item = {
            "start_time": azuracast_time_for_api(block.start_time_local),
            "end_time": azuracast_time_for_api(block.end_time_local),
            "days": list(day_values),
        }
        items_by_playlist[block.playlist_id].append(item)

    return items_by_playlist


def load_prompt(path: pathlib.Path) -> str:
    if not path.is_file():
        raise FileNotFoundError(f"Missing schedule prompt template: {path}")
    return path.read_text(encoding="utf-8").strip()


def build_playlist_catalog(playlists: Sequence[StationPlaylist]) -> str:
    lines: List[str] = []
    for playlist in sorted(playlists, key=lambda item: item.name.lower()):
        lines.append(
            f"- id={playlist.id}; name={playlist.name}; weight={playlist.weight:.2f}; enabled={playlist.is_enabled}"
        )
    return "\n".join(lines)


def build_duration_partition(
    min_block_minutes: int,
    max_block_minutes: int,
    *,
    total_minutes: int = 24 * 60,
    target_block_minutes: int = DEFAULT_TEMPLATE_TARGET_BLOCK_MINUTES,
) -> List[int]:
    if min_block_minutes <= 0 or max_block_minutes <= 0:
        raise ScheduleValidationError("Block duration bounds must be positive.")
    if total_minutes <= 0:
        raise ScheduleValidationError("total_minutes must be positive.")
    if min_block_minutes > max_block_minutes:
        raise ScheduleValidationError(
            "min_block_minutes cannot exceed max_block_minutes."
        )

    preferred = max(min_block_minutes, min(max_block_minutes, target_block_minutes))
    candidate_set = {
        value
        for value in range(min_block_minutes, max_block_minutes + 1)
        if value % 30 == 0
    }
    candidate_set.update({min_block_minutes, max_block_minutes, preferred})
    candidates = sorted(
        [value for value in candidate_set if min_block_minutes <= value <= max_block_minutes],
        key=lambda value: (abs(value - preferred), -value),
    )
    if not candidates:
        raise ScheduleValidationError(
            "Unable to build deterministic block candidates from duration bounds."
        )

    @lru_cache(maxsize=None)
    def solve(remaining_minutes: int) -> Optional[Tuple[int, ...]]:
        if remaining_minutes == 0:
            return ()

        for duration in candidates:
            if duration > remaining_minutes:
                continue
            remainder = remaining_minutes - duration
            if remainder != 0 and remainder < min_block_minutes:
                continue
            tail = solve(remainder)
            if tail is not None:
                return (duration, *tail)
        return None

    result = solve(total_minutes)
    if result is None:
        raise ScheduleValidationError(
            f"Unable to build deterministic template for {total_minutes} minutes with current bounds."
        )
    return list(result)


def block_open_preference(index: int, total_blocks: int) -> int:
    if total_blocks <= 0:
        return 0
    ratio = (index + 0.5) / total_blocks
    score = 0
    if ratio < 0.20:
        score += 4
    if 0.45 <= ratio <= 0.60:
        score += 5
    if 0.80 <= ratio <= 0.95:
        score += 4
    return score


def choose_open_block_indices(
    block_minutes: Sequence[int],
    open_ratio_min: float,
    open_ratio_max: float,
) -> List[int]:
    if not block_minutes:
        return []

    total_minutes = sum(block_minutes)
    min_open = math.ceil(open_ratio_min * total_minutes)
    max_open = math.floor(open_ratio_max * total_minutes)
    target_open = ((open_ratio_min + open_ratio_max) / 2.0) * total_minutes
    count = len(block_minutes)

    if max_open <= 0:
        return []

    if count <= 16:
        best: Optional[Tuple[Tuple[float, int, int, int], int]] = None
        for mask in range(1 << count):
            open_minutes = 0
            open_count = 0
            preference = 0
            transitions = 0
            prev_open = False
            for idx, duration in enumerate(block_minutes):
                current_open = bool(mask & (1 << idx))
                if current_open:
                    open_minutes += duration
                    open_count += 1
                    preference += block_open_preference(idx, count)
                if idx > 0 and current_open != prev_open:
                    transitions += 1
                prev_open = current_open

            if open_count == count:
                continue
            if open_minutes < min_open or open_minutes > max_open:
                continue

            score = (
                abs(open_minutes - target_open),
                -preference,
                -transitions,
                open_count,
            )
            if best is None or score < best[0]:
                best = (score, mask)

        if best is None:
            raise ScheduleValidationError(
                "Unable to choose deterministic open blocks within requested open-slot ratio."
            )
        mask = best[1]
        return [index for index in range(count) if mask & (1 << index)]

    ranked_indices = sorted(
        range(count),
        key=lambda idx: (block_open_preference(idx, count), -block_minutes[idx]),
        reverse=True,
    )
    selected: List[int] = []
    open_minutes = 0

    for index in ranked_indices:
        if open_minutes >= min_open:
            break
        candidate_open = open_minutes + block_minutes[index]
        if candidate_open > max_open:
            continue
        selected.append(index)
        open_minutes = candidate_open

    if open_minutes < min_open:
        remaining = sorted(
            [idx for idx in range(count) if idx not in selected],
            key=lambda idx: block_minutes[idx],
        )
        for index in remaining:
            candidate_open = open_minutes + block_minutes[index]
            if candidate_open > max_open:
                continue
            selected.append(index)
            open_minutes = candidate_open
            if open_minutes >= min_open:
                break

    if open_minutes < min_open:
        raise ScheduleValidationError(
            "Unable to choose deterministic open blocks within requested open-slot ratio."
        )

    if len(selected) >= count:
        selected = selected[:-1]

    return sorted(set(selected))


def build_weighted_playlist_cycle(
    playlists: Sequence[StationPlaylist],
) -> List[StationPlaylist]:
    ordered = sorted(playlists, key=lambda item: (-item.weight, item.name.lower(), item.id))
    if not ordered:
        return []

    positive = [item for item in ordered if item.weight > 0]
    source = positive or ordered
    min_weight = min((item.weight for item in positive), default=1.0)

    cycle: List[StationPlaylist] = []
    for item in source:
        if positive:
            repeats = max(1, min(6, int(round(item.weight / min_weight))))
        else:
            repeats = 1
        cycle.extend([item] * repeats)
    return cycle


def format_seed_template_for_prompt(
    daily_template: Sequence[DailyTemplateBlock],
) -> str:
    rows: List[Dict[str, Any]] = []
    for block in daily_template:
        row: Dict[str, Any] = {
            "start_time_local": block.start_time_local,
            "end_time_local": block.end_time_local,
            "mode": block.mode,
            "section_label": block.section_label,
            "genre_labels": list(block.genre_labels),
        }
        if block.mode == "playlist":
            row["playlist_id"] = block.playlist_id
            row["playlist_name"] = block.playlist_name
        rows.append(row)
    return json.dumps(rows, indent=2, ensure_ascii=False)


def build_deterministic_daily_template(
    playlist_by_id: Mapping[str, StationPlaylist],
    open_ratio_min: float,
    open_ratio_max: float,
    min_block_minutes: int,
    max_block_minutes: int,
) -> List[DailyTemplateBlock]:
    enabled_playlists = [item for item in playlist_by_id.values() if item.is_enabled]
    if not enabled_playlists:
        raise ScheduleValidationError("No enabled playlists available for deterministic template.")

    day_total_minutes = 24 * 60
    min_open_minutes = math.ceil(open_ratio_min * day_total_minutes)
    max_open_minutes = math.floor(open_ratio_max * day_total_minutes)
    forced_open_minutes = UNSCHEDULED_WINDOW_TOTAL_MINUTES

    if max_open_minutes < forced_open_minutes:
        raise ScheduleValidationError(
            "open_ratio_max is too low for fixed unscheduled window 22:00-06:00 "
            f"(requires at least {forced_open_minutes / day_total_minutes:.3f})."
        )

    block_durations: List[int] = []
    block_durations.extend(
        build_duration_partition(
            min_block_minutes=min_block_minutes,
            max_block_minutes=max_block_minutes,
            total_minutes=UNSCHEDULED_WINDOW_END_MINUTE,
        )
    )
    daytime_minutes = UNSCHEDULED_WINDOW_START_MINUTE - UNSCHEDULED_WINDOW_END_MINUTE
    block_durations.extend(
        build_duration_partition(
            min_block_minutes=min_block_minutes,
            max_block_minutes=max_block_minutes,
            total_minutes=daytime_minutes,
        )
    )
    block_durations.extend(
        build_duration_partition(
            min_block_minutes=min_block_minutes,
            max_block_minutes=max_block_minutes,
            total_minutes=24 * 60 - UNSCHEDULED_WINDOW_START_MINUTE,
        )
    )

    forced_open_indices: set[int] = set()
    daytime_indices: List[int] = []
    probe_start = 0
    for index, duration in enumerate(block_durations):
        probe_end = probe_start + duration
        if overlaps_unscheduled_window(probe_start, probe_end):
            forced_open_indices.add(index)
        else:
            daytime_indices.append(index)
        probe_start = probe_end

    additional_open_min = max(0, min_open_minutes - forced_open_minutes)
    additional_open_max = max_open_minutes - forced_open_minutes
    if additional_open_max < 0:
        raise ScheduleValidationError(
            "open_ratio_max is too low for fixed unscheduled window 22:00-06:00."
        )

    daytime_block_minutes = [block_durations[index] for index in daytime_indices]
    total_daytime_minutes = sum(daytime_block_minutes)
    if additional_open_min > total_daytime_minutes:
        raise ScheduleValidationError(
            "open_ratio_min is too high after applying fixed unscheduled window 22:00-06:00."
        )

    optional_open_indices: set[int] = set()
    if total_daytime_minutes > 0 and additional_open_max > 0:
        day_ratio_min = additional_open_min / total_daytime_minutes
        day_ratio_max = min(1.0, additional_open_max / total_daytime_minutes)
        if day_ratio_min > day_ratio_max:
            raise ScheduleValidationError(
                "No feasible daytime open-slot ratio after applying 22:00-06:00 unscheduled window."
            )
        chosen_daytime = choose_open_block_indices(
            block_minutes=daytime_block_minutes,
            open_ratio_min=day_ratio_min,
            open_ratio_max=day_ratio_max,
        )
        optional_open_indices = {
            daytime_indices[relative_index] for relative_index in chosen_daytime
        }

    open_indices = forced_open_indices | optional_open_indices

    cycle = build_weighted_playlist_cycle(enabled_playlists)
    if not cycle:
        raise ScheduleValidationError("Unable to build deterministic playlist cycle.")

    unique_playlist_ids = {item.id for item in cycle}
    cycle_index = 0
    last_playlist_id: Optional[str] = None
    start_minute = 0
    raw_blocks: List[Dict[str, Any]] = []

    for block_index, duration in enumerate(block_durations):
        end_minute = start_minute + duration
        start_time = format_hhmm(start_minute)
        end_time = format_hhmm(end_minute)

        if block_index in open_indices:
            raw_blocks.append(
                {
                    "start_time_local": start_time,
                    "end_time_local": end_time,
                    "mode": "open",
                    "section_label": "Open Rotation",
                    "genre_labels": ["mixed"],
                }
            )
            last_playlist_id = None
            start_minute = end_minute
            continue

        choice = cycle[cycle_index % len(cycle)]
        cycle_index += 1
        if (
            last_playlist_id is not None
            and choice.id == last_playlist_id
            and len(unique_playlist_ids) > 1
        ):
            for _ in range(len(cycle)):
                alternative = cycle[cycle_index % len(cycle)]
                cycle_index += 1
                if alternative.id != last_playlist_id:
                    choice = alternative
                    break

        raw_blocks.append(
            {
                "start_time_local": start_time,
                "end_time_local": end_time,
                "mode": "playlist",
                "playlist_id": choice.id,
                "playlist_name": choice.name,
                "section_label": choice.name,
                "genre_labels": [choice.name],
            }
        )
        last_playlist_id = choice.id
        start_minute = end_minute

    return validate_daily_template(
        raw_blocks=raw_blocks,
        playlist_by_id=playlist_by_id,
        open_ratio_min=open_ratio_min,
        open_ratio_max=open_ratio_max,
        min_block_minutes=min_block_minutes,
        max_block_minutes=max_block_minutes,
    )


def gemini_generate_schedule_text(
    prompt: str,
    system_prompt: str,
    model: str,
    temperature: float = 0.4,
    top_p: float = 0.85,
) -> str:
    client = get_gemini_client()
    try:
        from google.genai import types
    except ImportError as exc:
        raise RuntimeError(
            "Gemini client is not installed. Install with: pip install google-genai"
        ) from exc

    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=temperature,
            top_p=top_p,
        ),
    )
    text = (response.text or "").strip()
    if not text:
        raise RuntimeError("Gemini returned an empty response while generating schedule.")
    return text


def build_weekly_plan_with_llm(
    station_slug: str,
    station_name: str,
    timezone_name: str,
    week_start: dt.date,
    week_end: dt.date,
    playlists: Sequence[StationPlaylist],
    open_ratio_min: float,
    open_ratio_max: float,
    min_block_minutes: int,
    max_block_minutes: int,
    model: str,
) -> WeeklySchedulePlan:
    system_prompt = load_prompt(SCHEDULE_SYSTEM_PROMPT_PATH)
    user_template = load_prompt(SCHEDULE_USER_PROMPT_PATH)

    enabled_playlists = [playlist for playlist in playlists if playlist.is_enabled]
    playlist_by_id = {playlist.id: playlist for playlist in enabled_playlists}
    if not playlist_by_id:
        raise RuntimeError("No enabled playlists available for schedule generation.")

    deterministic_template = build_deterministic_daily_template(
        playlist_by_id=playlist_by_id,
        open_ratio_min=open_ratio_min,
        open_ratio_max=open_ratio_max,
        min_block_minutes=min_block_minutes,
        max_block_minutes=max_block_minutes,
    )
    required_time_ranges = [
        (block.start_minute, block.end_minute) for block in deterministic_template
    ]

    prompt = user_template.format(
        station_slug=station_slug,
        station_name=station_name,
        timezone=timezone_name,
        week_start=week_start.isoformat(),
        week_end=week_end.isoformat(),
        open_ratio_min=f"{open_ratio_min:.2f}",
        open_ratio_max=f"{open_ratio_max:.2f}",
        min_block_minutes=min_block_minutes,
        max_block_minutes=max_block_minutes,
        playlist_catalog=build_playlist_catalog(enabled_playlists),
        deterministic_seed_template=format_seed_template_for_prompt(
            deterministic_template
        ),
    )

    # Use slight randomization in retries to avoid repeating invalid shape.
    rng = random.Random()
    last_error: Optional[Exception] = None
    output = ""
    for attempt in range(1, GENERATION_MAX_ATTEMPTS + 1):
        if attempt == 1:
            output = run_with_retries(
                "Generate weekly schedule",
                lambda: gemini_generate_schedule_text(
                    prompt=prompt,
                    system_prompt=system_prompt,
                    model=model,
                    temperature=0.35,
                    top_p=0.82,
                ),
            )
        else:
            repair_prompt = (
                "Tu salida anterior no cumplio el contrato JSON o las reglas de validacion. "
                "Reescribi la salida usando exactamente el mismo esquema requerido. "
                "No agregues texto fuera del JSON. "
                f"Error de validacion: {last_error}\n"
                "Salida anterior:\n"
                f"{output}"
            )
            output = run_with_retries(
                "Repair weekly schedule JSON",
                lambda: gemini_generate_schedule_text(
                    prompt=repair_prompt,
                    system_prompt=system_prompt,
                    model=model,
                    temperature=0.20 + rng.uniform(0.0, 0.1),
                    top_p=0.7,
                ),
            )

        try:
            payload = extract_json_object(output)
            raw_blocks = payload.get("daily_template")
            if not isinstance(raw_blocks, list):
                raise ScheduleValidationError(
                    "JSON must include a 'daily_template' array."
                )

            daily_template = validate_daily_template(
                raw_blocks=raw_blocks,
                playlist_by_id=playlist_by_id,
                open_ratio_min=open_ratio_min,
                open_ratio_max=open_ratio_max,
                min_block_minutes=min_block_minutes,
                max_block_minutes=max_block_minutes,
                required_time_ranges=required_time_ranges,
            )
            expanded = expand_daily_template_to_week(daily_template, week_start)
            plan_hash = build_plan_hash(
                station=station_slug,
                timezone_name=timezone_name,
                week_start=week_start,
                daily_template=daily_template,
            )
            rationale = str(payload.get("rationale") or "").strip()
            if not rationale:
                rationale = "LLM-generated weekly fixed daily template."

            return WeeklySchedulePlan(
                station=station_slug,
                station_name=station_name,
                timezone=timezone_name,
                week_start_local_date=week_start.isoformat(),
                week_end_local_date=week_end.isoformat(),
                generated_at_utc=dt.datetime.now(dt.timezone.utc).isoformat(),
                open_ratio_min=open_ratio_min,
                open_ratio_max=open_ratio_max,
                daily_template=daily_template,
                expanded_blocks=expanded,
                rationale=rationale,
                plan_hash=plan_hash,
            )
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            LOGGER.warning(
                "[llm] Schedule validation failed (%s/%s): %s",
                attempt,
                GENERATION_MAX_ATTEMPTS,
                exc,
            )

    LOGGER.warning(
        "[llm] Falling back to deterministic weekly template after %s failed attempt(s): %s",
        GENERATION_MAX_ATTEMPTS,
        last_error,
    )
    expanded = expand_daily_template_to_week(deterministic_template, week_start)
    plan_hash = build_plan_hash(
        station=station_slug,
        timezone_name=timezone_name,
        week_start=week_start,
        daily_template=deterministic_template,
    )
    rationale = "Deterministic weekly template fallback."
    if last_error is not None:
        rationale = f"{rationale} LLM error: {last_error}"

    return WeeklySchedulePlan(
        station=station_slug,
        station_name=station_name,
        timezone=timezone_name,
        week_start_local_date=week_start.isoformat(),
        week_end_local_date=week_end.isoformat(),
        generated_at_utc=dt.datetime.now(dt.timezone.utc).isoformat(),
        open_ratio_min=open_ratio_min,
        open_ratio_max=open_ratio_max,
        daily_template=deterministic_template,
        expanded_blocks=expanded,
        rationale=rationale,
        plan_hash=plan_hash,
    )


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


def summarize_plan(plan: WeeklySchedulePlan) -> None:
    total_open = sum(
        block.end_minute - block.start_minute
        for block in plan.daily_template
        if block.mode == "open"
    )
    open_ratio = total_open / (24 * 60)

    LOGGER.info(
        "[plan] Week %s -> %s | blocks/day=%s | open_ratio=%.2f%% | hash=%s",
        plan.week_start_local_date,
        plan.week_end_local_date,
        len(plan.daily_template),
        open_ratio * 100.0,
        plan.plan_hash,
    )

    for block in plan.daily_template:
        if block.mode == "playlist":
            descriptor = f"playlist={block.playlist_name} ({block.playlist_id})"
        else:
            descriptor = "open-slot (AzuraCast weighted random)"
        LOGGER.info(
            "[plan] %s-%s | %s | section=%s | genres=%s",
            block.start_time_local,
            block.end_time_local,
            descriptor,
            block.section_label,
            ", ".join(block.genre_labels),
        )


def run(args: argparse.Namespace) -> None:
    configure_logging()

    load_dotenv()
    api_key = os.getenv("AZURACAST_API_KEY")
    if not api_key:
        raise RuntimeError("AZURACAST_API_KEY is not set in the environment.")

    open_ratio_min = float(args.open_ratio_min)
    open_ratio_max = float(args.open_ratio_max)
    if not (0.0 <= open_ratio_min <= open_ratio_max <= 1.0):
        raise ValueError("Open-slot ratio bounds must satisfy 0 <= min <= max <= 1.")

    if args.min_block_minutes > args.max_block_minutes:
        raise ValueError("min-block-minutes cannot exceed max-block-minutes.")

    client = AzuraCastClient(
        base_url=args.base_url.rstrip("/"),
        api_key=api_key,
        verify_tls=args.verify_tls,
    )

    stations = run_with_retries("Fetch stations", client.get_stations)
    station_payload = choose_station_payload(stations, args.station)
    station_name = derive_station_name(station_payload, args.station)
    timezone_name = derive_station_timezone(station_payload)
    station_tz = ZoneInfo(timezone_name)

    now_local = dt.datetime.now(station_tz)
    if args.week_start_date:
        week_start = dt.date.fromisoformat(args.week_start_date)
    else:
        week_start = compute_week_start(now_local.date())
    week_end = week_start + dt.timedelta(days=6)

    raw_playlists = run_with_retries(
        "Fetch station playlists",
        lambda: client.get_station_playlists(args.station),
    )
    playlists = extract_station_playlists(raw_playlists)
    if not playlists:
        raise RuntimeError(
            f"No playlists returned by AzuraCast for station '{args.station}'."
        )

    LOGGER.info(
        "[station] %s (%s) | timezone=%s | playlists=%s",
        station_name,
        args.station,
        timezone_name,
        len(playlists),
    )

    plan = build_weekly_plan_with_llm(
        station_slug=args.station,
        station_name=station_name,
        timezone_name=timezone_name,
        week_start=week_start,
        week_end=week_end,
        playlists=playlists,
        open_ratio_min=open_ratio_min,
        open_ratio_max=open_ratio_max,
        min_block_minutes=args.min_block_minutes,
        max_block_minutes=args.max_block_minutes,
        model=args.model,
    )

    summarize_plan(plan)

    state_path = schedule_state_path(args.station)
    existing_state = load_schedule_state(state_path)
    previous_hash = (
        str(existing_state.get("plan_hash"))
        if isinstance(existing_state, Mapping)
        else None
    )

    if args.dry_run:
        LOGGER.info("[dry-run] Skipping AzuraCast mutations.")
        print(json.dumps(plan.to_dict(), indent=2, ensure_ascii=False))
        return

    if previous_hash and previous_hash == plan.plan_hash and not args.force_apply:
        LOGGER.info(
            "[apply] Plan hash unchanged (%s); skipping remote apply (use --force-apply to override).",
            plan.plan_hash,
        )
        save_schedule_state_atomic(state_path, plan.to_dict())
        return

    updated_playlists, updated_items = apply_weekly_schedule(
        client=client,
        station_slug=args.station,
        playlists=playlists,
        daily_template=plan.daily_template,
    )

    save_schedule_state_atomic(state_path, plan.to_dict())

    LOGGER.info(
        "[apply] Updated %s playlists with %s scheduled blocks total.",
        updated_playlists,
        updated_items,
    )
    LOGGER.info("[state] Saved schedule state to %s", state_path)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Generate a fixed weekly schedule (same daily template for all 7 days) "
            "from AzuraCast playlists using Gemini."
        )
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv("AZURACAST_BASE_URL", "https://192.168.1.226"),
        help="Base URL for AzuraCast instance (default: %(default)s).",
    )
    parser.add_argument(
        "-s",
        "--station",
        default=os.getenv("AZURACAST_STATION", "neuralforge"),
        help="AzuraCast station shortcode (default: %(default)s).",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_GEMINI_MODEL,
        help="Gemini text model for schedule generation (default: %(default)s).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Generate and validate plan without writing to AzuraCast.",
    )
    parser.add_argument(
        "--force-apply",
        action="store_true",
        help="Apply even when plan hash matches saved state.",
    )
    parser.add_argument(
        "--verify-tls",
        action="store_true",
        help="Verify TLS certificates for AzuraCast requests.",
    )
    parser.add_argument(
        "--week-start-date",
        help=(
            "Optional ISO date (YYYY-MM-DD) for deterministic generation. "
            "Defaults to current local week's Monday in station timezone."
        ),
    )
    parser.add_argument(
        "--open-ratio-min",
        type=float,
        default=DEFAULT_OPEN_RATIO_MIN,
        help="Minimum open-slot ratio per day (0-1).",
    )
    parser.add_argument(
        "--open-ratio-max",
        type=float,
        default=DEFAULT_OPEN_RATIO_MAX,
        help="Maximum open-slot ratio per day (0-1).",
    )
    parser.add_argument(
        "--min-block-minutes",
        type=int,
        default=DEFAULT_MIN_BLOCK_MINUTES,
        help="Minimum allowed block duration in minutes.",
    )
    parser.add_argument(
        "--max-block-minutes",
        type=int,
        default=DEFAULT_MAX_BLOCK_MINUTES,
        help="Maximum allowed block duration in minutes.",
    )
    return parser


if __name__ == "__main__":
    run(build_arg_parser().parse_args())
