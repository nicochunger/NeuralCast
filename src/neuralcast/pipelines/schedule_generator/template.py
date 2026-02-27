"""Template parsing, validation, deterministic generation, and plan summaries."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import re
from functools import lru_cache
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .config import (
    DEFAULT_TEMPLATE_TARGET_BLOCK_MINUTES,
    LOGGER,
    UNSCHEDULED_WINDOW_END_MINUTE,
    UNSCHEDULED_WINDOW_START_MINUTE,
    UNSCHEDULED_WINDOW_TOTAL_MINUTES,
)
from .models import (
    DailyTemplateBlock,
    ExpandedScheduleBlock,
    ScheduleValidationError,
    StationPlaylist,
    WeeklySchedulePlan,
)

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


def normalize_string_list(value: Any) -> List[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value or "").strip()
    if not text:
        return []
    return [text]


def validate_daily_template(
    raw_blocks: Sequence[Mapping[str, Any]],
    playlist_by_id: Mapping[str, StationPlaylist],
    open_ratio_min: float,
    open_ratio_max: float,
    min_block_minutes: int,
    max_block_minutes: int,
    required_time_ranges: Optional[Sequence[Tuple[int, int]]] = None,
    enforce_unscheduled_window: bool = True,
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
        overlaps_quiet_hours = (
            enforce_unscheduled_window
            and overlaps_unscheduled_window(start_minute, end_minute)
        )

        playlist_ids: List[str] = []
        playlist_names: List[str] = []
        playlist_id: Optional[str] = None
        playlist_name: Optional[str] = None
        if mode == "playlist":
            if overlaps_quiet_hours:
                raise ScheduleValidationError(
                    f"Playlist blocks are not allowed between 22:00 and 06:00 "
                    f"({start_time}-{end_time}). Use mode='open' or disable quiet-hour enforcement."
                )
            raw_playlist_ids = normalize_string_list(entry.get("playlist_ids"))
            raw_playlist_names = normalize_string_list(entry.get("playlist_names"))

            legacy_playlist_id = str(entry.get("playlist_id") or "").strip()
            legacy_playlist_name = str(entry.get("playlist_name") or "").strip()
            if not raw_playlist_ids and legacy_playlist_id:
                raw_playlist_ids = [legacy_playlist_id]
            if not raw_playlist_names and legacy_playlist_name:
                raw_playlist_names = [legacy_playlist_name]

            if raw_playlist_ids and raw_playlist_names and (
                len(raw_playlist_ids) != len(raw_playlist_names)
            ):
                raise ScheduleValidationError(
                    "playlist_ids and playlist_names must have the same length when both are provided."
                )

            resolved_playlists: List[StationPlaylist] = []
            if raw_playlist_ids:
                for raw_id in raw_playlist_ids:
                    if raw_id not in playlist_by_id:
                        raise ScheduleValidationError(
                            f"Unknown playlist_id '{raw_id}' in block {start_time}-{end_time}."
                        )
                    resolved_playlists.append(playlist_by_id[raw_id])
            elif raw_playlist_names:
                for raw_name in raw_playlist_names:
                    resolved = playlist_name_map.get(raw_name.lower())
                    if resolved is None:
                        raise ScheduleValidationError(
                            f"Unknown playlist_name '{raw_name}' in block {start_time}-{end_time}."
                        )
                    resolved_playlists.append(resolved)
            else:
                raise ScheduleValidationError(
                    "Playlist blocks require playlist_ids/playlist_names (or legacy playlist_id/playlist_name)."
                )

            deduped_playlists: List[StationPlaylist] = []
            seen_playlist_ids: set[str] = set()
            for playlist in resolved_playlists:
                if playlist.id in seen_playlist_ids:
                    continue
                seen_playlist_ids.add(playlist.id)
                deduped_playlists.append(playlist)

            if not deduped_playlists:
                raise ScheduleValidationError(
                    f"Playlist block {start_time}-{end_time} resolved to zero playlists."
                )

            for playlist in deduped_playlists:
                if not playlist.is_enabled:
                    raise ScheduleValidationError(
                        f"Playlist '{playlist.name}' is disabled and cannot be scheduled explicitly."
                    )

            playlist_ids = [playlist.id for playlist in deduped_playlists]
            playlist_names = [playlist.name for playlist in deduped_playlists]
            playlist_id = playlist_ids[0]
            playlist_name = playlist_names[0]
            if not section_label:
                section_label = (
                    playlist_name
                    if len(playlist_names) == 1
                    else " + ".join(playlist_names[:2])
                )
            if not genres:
                genres = playlist_names[:]
        else:
            playlist_ids = []
            playlist_names = []
            playlist_id = None
            playlist_name = None
            if not section_label:
                section_label = "Bloque libre"
            if not genres:
                genres = ["sin tematica"]

        parsed_blocks.append(
            DailyTemplateBlock(
                start_time_local=start_time,
                end_time_local=end_time,
                start_minute=start_minute,
                end_minute=end_minute,
                mode=mode,
                section_label=section_label,
                genre_labels=genres,
                playlist_ids=playlist_ids,
                playlist_names=playlist_names,
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
                    ",".join(block.playlist_ids) if block.playlist_ids else "open",
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
                    playlist_ids=list(block.playlist_ids),
                    playlist_names=list(block.playlist_names),
                    playlist_id=block.playlist_id,
                    playlist_name=block.playlist_name,
                )
            )
    return expanded



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
            row["playlist_ids"] = list(block.playlist_ids)
            row["playlist_names"] = list(block.playlist_names)
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
                    "section_label": "Bloque libre",
                    "genre_labels": ["sin tematica"],
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
            if len(block.playlist_ids) > 1:
                joined = ", ".join(
                    f"{name} ({pid})"
                    for pid, name in zip(block.playlist_ids, block.playlist_names)
                )
                descriptor = f"playlists=[{joined}]"
            else:
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
