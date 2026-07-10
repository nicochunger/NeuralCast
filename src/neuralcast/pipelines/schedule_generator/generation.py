"""Code-only weekly plan assembly for fixed-template schedule generation."""

from __future__ import annotations

import datetime as dt
import hashlib
import math
import random
import secrets
from collections import Counter
from dataclasses import dataclass
from typing import Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from .config import (
    DEFAULT_MAX_OPEN_SLOTS,
    DEFAULT_MIN_OPEN_SLOTS,
    DEFAULT_TEMPLATE_TARGET_BLOCK_MINUTES,
    LOGGER,
    NEURALCAST_PLAYLIST_WEIGHT_MULTIPLIERS,
    SCHEDULE_TIME_GRID_MINUTES,
)
from .models import ScheduleValidationError, StationPlaylist, WeeklySchedulePlan
from .template import (
    build_duration_partition,
    build_plan_hash,
    choose_open_block_indices,
    format_hhmm,
    validate_daily_template,
    expand_daily_template_to_week,
)

SCHEDULE_SEED_MODE_STABLE_WEEK = "stable_week"
SCHEDULE_SEED_MODE_FRESH = "fresh"
SCHEDULE_SEED_MODE_CUSTOM = "custom"
SUPPORTED_SCHEDULE_SEED_MODES = (
    SCHEDULE_SEED_MODE_STABLE_WEEK,
    SCHEDULE_SEED_MODE_FRESH,
    SCHEDULE_SEED_MODE_CUSTOM,
)
DEFAULT_SCHEDULE_SEED_MODE = SCHEDULE_SEED_MODE_STABLE_WEEK


@dataclass(frozen=True)
class AssignmentCandidate:
    playlist_ids: Tuple[str, ...]
    playlist_names: Tuple[str, ...]
    section_label: str
    genre_labels: Tuple[str, ...]
    base_weight: float
    kind: str  # "solo" | "combo"


def _name_key(value: str) -> str:
    return " ".join(str(value or "").strip().lower().split())


NEURALFORGE_MELODIC_DEATH_KEY = _name_key("Melodic Death Metal")


def _is_neuralforge(station_slug: str) -> bool:
    return station_slug.strip().lower() == "neuralforge"


def _is_neuralcast(station_slug: str) -> bool:
    return station_slug.strip().lower() == "neuralcast"


def _ceil_to_grid(value: int, grid_minutes: int = SCHEDULE_TIME_GRID_MINUTES) -> int:
    return ((int(value) + grid_minutes - 1) // grid_minutes) * grid_minutes


def _floor_to_grid(value: int, grid_minutes: int = SCHEDULE_TIME_GRID_MINUTES) -> int:
    return (int(value) // grid_minutes) * grid_minutes


def _duration_grid_range(minimum: int, maximum: int) -> List[int]:
    first = _ceil_to_grid(minimum)
    last = _floor_to_grid(maximum)
    if first > last:
        return []
    return list(range(first, last + 1, SCHEDULE_TIME_GRID_MINUTES))


def _stable_seed(
    station_slug: str,
    week_start: dt.date,
    timezone_name: str,
    playlists: Sequence[StationPlaylist],
    open_ratio_min: float,
    open_ratio_max: float,
    min_open_slots: int,
    max_open_slots: int,
    min_block_minutes: int,
    max_block_minutes: int,
) -> int:
    playlist_signature = "|".join(
        sorted(
            f"{playlist.id}:{playlist.name}:{int(playlist.is_enabled)}:{playlist.weight:.3f}"
            for playlist in playlists
        )
    )
    payload = "|".join(
        [
            station_slug.strip().lower(),
            week_start.isoformat(),
            timezone_name,
            f"{open_ratio_min:.4f}",
            f"{open_ratio_max:.4f}",
            str(min_open_slots),
            str(max_open_slots),
            str(min_block_minutes),
            str(max_block_minutes),
            playlist_signature,
        ]
    )
    digest = hashlib.sha256(payload.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")


def _mix_seed(base_seed: int, seed_salt: str) -> int:
    digest = hashlib.sha256(f"{base_seed}|{seed_salt}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")


def resolve_schedule_seed(
    *,
    station_slug: str,
    week_start: dt.date,
    timezone_name: str,
    playlists: Sequence[StationPlaylist],
    open_ratio_min: float,
    open_ratio_max: float,
    min_open_slots: int,
    max_open_slots: int,
    min_block_minutes: int,
    max_block_minutes: int,
    seed_mode: str = DEFAULT_SCHEDULE_SEED_MODE,
    seed_salt: Optional[str] = None,
) -> Tuple[int, str, Optional[str]]:
    normalized_seed_mode = str(seed_mode or DEFAULT_SCHEDULE_SEED_MODE).strip().lower()
    if normalized_seed_mode not in SUPPORTED_SCHEDULE_SEED_MODES:
        raise ValueError(
            "Unsupported seed_mode "
            f"'{seed_mode}'. Allowed values: {SUPPORTED_SCHEDULE_SEED_MODES}."
        )

    normalized_seed_salt = str(seed_salt).strip() if seed_salt is not None else None
    if normalized_seed_salt == "":
        normalized_seed_salt = None

    if (
        normalized_seed_mode == SCHEDULE_SEED_MODE_STABLE_WEEK
        and normalized_seed_salt is not None
    ):
        raise ValueError(
            "seed_salt is only supported for seed_mode 'fresh' or 'custom'."
        )

    if (
        normalized_seed_mode == SCHEDULE_SEED_MODE_CUSTOM
        and normalized_seed_salt is None
    ):
        raise ValueError("seed_salt is required when seed_mode='custom'.")

    base_seed = _stable_seed(
        station_slug=station_slug,
        week_start=week_start,
        timezone_name=timezone_name,
        playlists=playlists,
        open_ratio_min=open_ratio_min,
        open_ratio_max=open_ratio_max,
        min_open_slots=min_open_slots,
        max_open_slots=max_open_slots,
        min_block_minutes=min_block_minutes,
        max_block_minutes=max_block_minutes,
    )

    if normalized_seed_mode == SCHEDULE_SEED_MODE_STABLE_WEEK:
        return base_seed, normalized_seed_mode, None

    resolved_seed_salt = normalized_seed_salt or secrets.token_hex(8)
    return (
        _mix_seed(base_seed, resolved_seed_salt),
        normalized_seed_mode,
        resolved_seed_salt,
    )


def _candidate_durations(
    min_block_minutes: int,
    max_block_minutes: int,
    target_minutes: int,
) -> List[int]:
    preferred = max(min_block_minutes, min(max_block_minutes, target_minutes))
    values = _duration_grid_range(min_block_minutes, max_block_minutes)
    return sorted(
        values,
        key=lambda value: (
            abs(value - preferred),
            value,
        ),
    )


def _build_random_duration_partition(
    *,
    min_block_minutes: int,
    max_block_minutes: int,
    total_minutes: int,
    rng: random.Random,
    target_block_minutes: int = DEFAULT_TEMPLATE_TARGET_BLOCK_MINUTES,
) -> List[int]:
    if total_minutes <= 0:
        raise ScheduleValidationError("total_minutes must be positive.")

    target = max(
        min_block_minutes,
        min(
            max_block_minutes,
            rng.choice([60, 90, 120, 150, 180, target_block_minutes]),
        ),
    )
    candidates = _candidate_durations(min_block_minutes, max_block_minutes, target)
    if not candidates:
        raise ScheduleValidationError("No valid block duration candidates.")

    result: List[int] = []
    dead_remaining: set[int] = set()

    def backtrack(remaining: int) -> bool:
        if remaining == 0:
            return True
        if remaining in dead_remaining:
            return False

        scored: List[Tuple[float, int]] = []
        for duration in candidates:
            if duration > remaining:
                continue
            remainder = remaining - duration
            if remainder != 0 and remainder < min_block_minutes:
                continue

            score = float(abs(duration - target))
            if result:
                if duration == result[-1]:
                    score += 18.0
                if len(result) >= 2 and duration == result[-1] == result[-2]:
                    score += 45.0

            # Light randomness so weekly seed shapes vary while staying structured.
            score += rng.uniform(0.0, 12.0)
            scored.append((score, duration))

        scored.sort(key=lambda item: item[0])
        for _, duration in scored:
            result.append(duration)
            if backtrack(remaining - duration):
                return True
            result.pop()

        dead_remaining.add(remaining)
        return False

    if backtrack(total_minutes):
        return list(result)

    # Fall back to the existing exact-cover deterministic helper if the randomized
    # search fails with unusual duration constraints.
    return build_duration_partition(
        min_block_minutes=min_block_minutes,
        max_block_minutes=max_block_minutes,
        total_minutes=total_minutes,
        target_block_minutes=target_block_minutes,
    )


def _duration_step(values: Sequence[int]) -> int:
    normalized = [abs(int(value)) for value in values if int(value) != 0]
    if not normalized:
        return SCHEDULE_TIME_GRID_MINUTES
    if all(value % SCHEDULE_TIME_GRID_MINUTES == 0 for value in normalized):
        return SCHEDULE_TIME_GRID_MINUTES
    for step in (30, 15, 5, 1):
        if all(value % step == 0 for value in normalized):
            return step
    return 1


def _build_partition_with_specs(
    *,
    total_minutes: int,
    specs: Sequence[Tuple[int, int, int, int]],
    rng: random.Random,
) -> List[int]:
    if total_minutes < 0:
        raise ScheduleValidationError("Partition total_minutes must be non-negative.")
    if total_minutes % SCHEDULE_TIME_GRID_MINUTES != 0:
        raise ScheduleValidationError(
            f"Partition total_minutes must align to {SCHEDULE_TIME_GRID_MINUTES} minutes."
        )
    if not specs:
        if total_minutes == 0:
            return []
        raise ScheduleValidationError("Partition specs are required for non-zero totals.")

    raw_minima = [minimum for minimum, _maximum, _preferred, _jitter in specs]
    raw_maxima = [maximum for _minimum, maximum, _preferred, _jitter in specs]
    if any(minimum > maximum for minimum, maximum in zip(raw_minima, raw_maxima)):
        raise ScheduleValidationError("Partition spec minimum cannot exceed maximum.")

    candidate_lists: List[List[int]] = []
    for minimum, maximum, preferred, jitter in specs:
        candidates = _duration_grid_range(minimum, maximum)
        if not candidates:
            raise ScheduleValidationError(
                "Partition spec has no quarter-hour-aligned duration candidates."
            )

        target = max(minimum, min(maximum, preferred))
        if jitter > 0:
            jitter_units = max(0, jitter // SCHEDULE_TIME_GRID_MINUTES)
            if jitter_units > 0:
                target += (
                    rng.randint(-jitter_units, jitter_units)
                    * SCHEDULE_TIME_GRID_MINUTES
                )
                target = max(minimum, min(maximum, target))

        candidate_lists.append(
            sorted(
                candidates,
                key=lambda value, target_value=target, preferred_value=preferred: (
                    abs(value - target_value),
                    abs(value - preferred_value),
                    value,
                ),
            )
        )

    minima = [min(candidates) for candidates in candidate_lists]
    maxima = [max(candidates) for candidates in candidate_lists]
    minimum_total = sum(minima)
    maximum_total = sum(maxima)
    if total_minutes < minimum_total or total_minutes > maximum_total:
        raise ScheduleValidationError(
            f"Unable to partition {total_minutes} minutes within bounds "
            f"[{minimum_total}, {maximum_total}]."
        )

    suffix_min = [0] * (len(specs) + 1)
    suffix_max = [0] * (len(specs) + 1)
    for index in range(len(specs) - 1, -1, -1):
        suffix_min[index] = suffix_min[index + 1] + minima[index]
        suffix_max[index] = suffix_max[index + 1] + maxima[index]

    chosen: List[int] = []

    def backtrack(index: int, remaining: int) -> bool:
        if index == len(specs):
            return remaining == 0

        min_remaining = suffix_min[index + 1]
        max_remaining = suffix_max[index + 1]
        for value in candidate_lists[index]:
            next_remaining = remaining - value
            if next_remaining < min_remaining or next_remaining > max_remaining:
                continue
            chosen.append(value)
            if backtrack(index + 1, next_remaining):
                return True
            chosen.pop()
        return False

    if not backtrack(0, total_minutes):
        raise ScheduleValidationError(
            f"Unable to build exact partition for {total_minutes} minutes."
        )
    return list(chosen)


def _choose_open_layout(
    *,
    open_ratio_min: float,
    open_ratio_max: float,
    min_open_slots: int,
    max_open_slots: int,
    min_block_minutes: int,
    max_block_minutes: int,
    playlist_capacity: int,
    rng: random.Random,
) -> Tuple[int, int, int]:
    total_minutes = 24 * 60
    min_open_minutes = math.ceil(open_ratio_min * total_minutes)
    max_open_minutes = math.floor(open_ratio_max * total_minutes)
    max_open_slots_feasible = min(int(max_open_slots), max(1, playlist_capacity - 1))
    min_open_slots_feasible = max(1, int(min_open_slots))
    if min_open_slots_feasible > max_open_slots_feasible:
        raise ScheduleValidationError(
            f"No feasible open-slot count in bounds [{min_open_slots}, {max_open_slots}] "
            f"with playlist capacity {playlist_capacity}."
        )

    preferred_ratio = (open_ratio_min + open_ratio_max) / 2.0
    layout_candidates: List[Tuple[float, float, int, int, int]] = []

    for open_count in range(min_open_slots_feasible, max_open_slots_feasible + 1):
        open_total_min = max(min_open_minutes, open_count * min_block_minutes)
        open_total_max = min(max_open_minutes, open_count * max_block_minutes)
        if open_total_min > open_total_max:
            continue

        open_total_min_aligned = _ceil_to_grid(open_total_min)
        open_total_max_aligned = _floor_to_grid(open_total_max)
        if open_total_min_aligned > open_total_max_aligned:
            continue

        step = SCHEDULE_TIME_GRID_MINUTES
        open_total_candidates = list(
            range(open_total_max_aligned, open_total_min_aligned - 1, -step)
        )

        for open_total in open_total_candidates:
            remaining_minutes = total_minutes - open_total
            gap_count = open_count + 1
            open_average = open_total / open_count
            playlist_min_blocks = max(
                gap_count,
                math.ceil(remaining_minutes / max_block_minutes),
                math.floor(remaining_minutes / open_average) + 1,
            )
            playlist_max_blocks = min(
                playlist_capacity,
                remaining_minutes // min_block_minutes,
            )
            if playlist_min_blocks > playlist_max_blocks:
                continue

            playlist_step_target = max(
                min_block_minutes,
                min(max_block_minutes, int(open_average) - step),
            )
            playlist_block_count = max(
                playlist_min_blocks,
                math.ceil(remaining_minutes / playlist_step_target),
            )
            playlist_block_count = min(playlist_block_count, playlist_max_blocks)
            if playlist_block_count < playlist_min_blocks:
                continue

            playlist_average = remaining_minutes / playlist_block_count
            duration_advantage = open_average - playlist_average
            ratio_distance = abs((open_total / total_minutes) - preferred_ratio)
            layout_candidates.append(
                (
                    -duration_advantage,
                    ratio_distance,
                    open_count,
                    open_total,
                    playlist_block_count,
                )
            )

    if not layout_candidates:
        raise ScheduleValidationError(
            "Unable to choose an open-layout configuration that keeps open blocks "
            "longer on average than playlist blocks."
        )

    layout_candidates.sort(key=lambda item: (item[0], item[1], -item[2], -item[3], item[4]))
    shortlist = layout_candidates[: min(4, len(layout_candidates))]
    _advantage_score, _ratio_score, open_count, open_total, playlist_block_count = rng.choice(
        shortlist
    )
    return open_count, open_total, playlist_block_count


def _distribute_playlist_blocks_across_gaps(
    *,
    gap_count: int,
    playlist_block_count: int,
    rng: random.Random,
) -> List[int]:
    if gap_count <= 0:
        raise ScheduleValidationError("gap_count must be positive.")
    if playlist_block_count < gap_count:
        raise ScheduleValidationError(
            "playlist_block_count must allow at least one playlist block per gap."
        )

    distribution = [1] * gap_count
    remaining = playlist_block_count - gap_count
    indices = list(range(gap_count))
    rng.shuffle(indices)
    while remaining > 0:
        for index in indices:
            if remaining <= 0:
                break
            distribution[index] += 1
            remaining -= 1
        rng.shuffle(indices)
    return distribution


def _build_playlist_gap_durations(
    *,
    total_minutes: int,
    blocks_per_gap: Sequence[int],
    min_block_minutes: int,
    max_block_minutes: int,
    rng: random.Random,
) -> List[int]:
    gap_count = len(blocks_per_gap)
    if gap_count == 0:
        return []

    preferred_gap = total_minutes // gap_count
    jitter = min(45, max(0, preferred_gap // 6))
    specs = [
        (
            block_count * min_block_minutes,
            block_count * max_block_minutes,
            max(
                block_count * min_block_minutes,
                min(block_count * max_block_minutes, preferred_gap),
            ),
            jitter,
        )
        for block_count in blocks_per_gap
    ]
    return _build_partition_with_specs(
        total_minutes=total_minutes,
        specs=specs,
        rng=rng,
    )


def _build_gap_playlist_partitions(
    *,
    gap_minutes: int,
    block_count: int,
    min_block_minutes: int,
    max_block_minutes: int,
    open_average_minutes: float,
    rng: random.Random,
) -> List[int]:
    if block_count <= 0:
        if gap_minutes == 0:
            return []
        raise ScheduleValidationError("Gap playlist partition requires block_count > 0.")

    duration_step = _duration_step(
        [gap_minutes, min_block_minutes, max_block_minutes, int(open_average_minutes)]
    )
    preferred_playlist = gap_minutes // block_count
    preferred_playlist = min(
        preferred_playlist,
        max(min_block_minutes, int(open_average_minutes) - duration_step),
    )
    preferred_playlist = max(min_block_minutes, min(max_block_minutes, preferred_playlist))
    jitter = min(30, max(0, preferred_playlist // 6))
    specs = [
        (min_block_minutes, max_block_minutes, preferred_playlist, jitter)
        for _ in range(block_count)
    ]
    return _build_partition_with_specs(
        total_minutes=gap_minutes,
        specs=specs,
        rng=rng,
    )


def _build_randomized_scaffold(
    *,
    open_ratio_min: float,
    open_ratio_max: float,
    min_open_slots: int,
    max_open_slots: int,
    min_block_minutes: int,
    max_block_minutes: int,
    playlist_capacity: int,
    rng: random.Random,
) -> List[Dict[str, object]]:
    open_count, open_total_minutes, playlist_block_count = _choose_open_layout(
        open_ratio_min=open_ratio_min,
        open_ratio_max=open_ratio_max,
        min_open_slots=min_open_slots,
        max_open_slots=max_open_slots,
        min_block_minutes=min_block_minutes,
        max_block_minutes=max_block_minutes,
        playlist_capacity=playlist_capacity,
        rng=rng,
    )
    gap_count = open_count + 1
    blocks_per_gap = _distribute_playlist_blocks_across_gaps(
        gap_count=gap_count,
        playlist_block_count=playlist_block_count,
        rng=rng,
    )
    playlist_total_minutes = (24 * 60) - open_total_minutes
    gap_durations = _build_playlist_gap_durations(
        total_minutes=playlist_total_minutes,
        blocks_per_gap=blocks_per_gap,
        min_block_minutes=min_block_minutes,
        max_block_minutes=max_block_minutes,
        rng=rng,
    )

    open_preferred = max(
        min_block_minutes,
        min(max_block_minutes, open_total_minutes // open_count),
    )
    open_jitter = min(30, max(0, open_preferred // 6))
    open_specs = [
        (min_block_minutes, max_block_minutes, open_preferred, open_jitter)
        for _ in range(open_count)
    ]
    open_durations = _build_partition_with_specs(
        total_minutes=open_total_minutes,
        specs=open_specs,
        rng=rng,
    )
    open_average_minutes = open_total_minutes / open_count

    open_labels = [
        ("Bloque libre", ["sin tematica"]),
        ("Sin tematica", ["mix variado"]),
        ("Cruce libre", ["sin tematica"]),
        ("Mezcla libre", ["catalogo completo"]),
    ]

    raw_blocks: List[Dict[str, object]] = []
    start_minute = 0
    for gap_index, gap_duration in enumerate(gap_durations):
        playlist_durations = _build_gap_playlist_partitions(
            gap_minutes=gap_duration,
            block_count=blocks_per_gap[gap_index],
            min_block_minutes=min_block_minutes,
            max_block_minutes=max_block_minutes,
            open_average_minutes=open_average_minutes,
            rng=rng,
        )
        for duration in playlist_durations:
            end_minute = start_minute + duration
            raw_blocks.append(
                {
                    "start_time_local": format_hhmm(start_minute),
                    "end_time_local": format_hhmm(end_minute),
                    "mode": "playlist",
                    "section_label": "",
                    "genre_labels": [],
                    "_duration_minutes": duration,
                }
            )
            start_minute = end_minute

        if gap_index < open_count:
            duration = open_durations[gap_index]
            end_minute = start_minute + duration
            start_time = format_hhmm(start_minute)
            end_time = format_hhmm(end_minute)
            section_label, genre_labels = rng.choice(open_labels)
            raw_blocks.append(
                {
                    "start_time_local": start_time,
                    "end_time_local": end_time,
                    "mode": "open",
                    "section_label": section_label,
                    "genre_labels": list(genre_labels),
                    "_duration_minutes": duration,
                }
            )
            start_minute = end_minute

    return raw_blocks


def _playlist_search_text(playlist: StationPlaylist) -> str:
    return _name_key(playlist.name).replace("-", " ")


def _find_neuralcast_reserved_playlists(
    playlists: Sequence[StationPlaylist],
    predicate: Callable[[StationPlaylist], bool],
    description: str,
) -> List[StationPlaylist]:
    matches = [
        playlist for playlist in playlists if playlist.is_enabled and predicate(playlist)
    ]
    if not matches:
        raise ScheduleValidationError(
            f"NeuralCast reserved schedule requires an enabled {description} playlist."
        )
    return matches


def _neuralcast_reggae_playlists(
    playlists: Sequence[StationPlaylist],
) -> List[StationPlaylist]:
    return _find_neuralcast_reserved_playlists(
        playlists,
        lambda playlist: "reggae" in _playlist_search_text(playlist),
        "reggae",
    )


def _neuralcast_evening_playlists(
    playlists: Sequence[StationPlaylist],
) -> List[StationPlaylist]:
    return _find_neuralcast_reserved_playlists(
        playlists,
        lambda playlist: _name_key(playlist.name) == "deep house",
        "Deep House",
    )


def _reserved_playlist_blocks(
    *,
    start_minute: int,
    end_minute: int,
    playlists: Sequence[StationPlaylist],
) -> List[Dict[str, object]]:
    if not playlists:
        raise ScheduleValidationError("Reserved windows require at least one playlist.")

    playlist_ids = [playlist.id for playlist in playlists]
    playlist_names = [playlist.name for playlist in playlists]
    return [
        {
            "start_time_local": format_hhmm(start_minute),
            "end_time_local": format_hhmm(end_minute),
            "mode": "playlist",
            "playlist_ids": playlist_ids,
            "playlist_names": playlist_names,
            "playlist_id": playlist_ids[0],
            "playlist_name": playlist_names[0],
            "section_label": " + ".join(playlist_names),
            "genre_labels": playlist_names,
            "_duration_minutes": end_minute - start_minute,
            "_reserved_playlist": True,
        }
    ]


def _build_neuralcast_reserved_scaffold(
    *,
    playlists: Sequence[StationPlaylist],
    open_ratio_min: float,
    open_ratio_max: float,
    min_block_minutes: int,
    max_block_minutes: int,
    rng: random.Random,
) -> List[Dict[str, object]]:
    reserved_windows = [
        (7 * 60, 9 * 60, _neuralcast_reggae_playlists(playlists)),
        ((19 * 60) + 30, 22 * 60, _neuralcast_evening_playlists(playlists)),
    ]

    raw_blocks: List[Dict[str, object]] = []
    cursor = 0
    for start_minute, end_minute, reserved_playlists in reserved_windows:
        if cursor < start_minute:
            for duration in _build_random_duration_partition(
                min_block_minutes=min_block_minutes,
                max_block_minutes=max_block_minutes,
                total_minutes=start_minute - cursor,
                rng=rng,
            ):
                block_end = cursor + duration
                raw_blocks.append(
                    {
                        "start_time_local": format_hhmm(cursor),
                        "end_time_local": format_hhmm(block_end),
                        "mode": "playlist",
                        "section_label": "",
                        "genre_labels": [],
                        "_duration_minutes": duration,
                    }
                )
                cursor = block_end

        raw_blocks.extend(
            _reserved_playlist_blocks(
                start_minute=start_minute,
                end_minute=end_minute,
                playlists=reserved_playlists,
            )
        )
        cursor = end_minute

    if cursor < 24 * 60:
        for duration in _build_random_duration_partition(
            min_block_minutes=min_block_minutes,
            max_block_minutes=max_block_minutes,
            total_minutes=(24 * 60) - cursor,
            rng=rng,
        ):
            block_end = cursor + duration
            raw_blocks.append(
                {
                    "start_time_local": format_hhmm(cursor),
                    "end_time_local": format_hhmm(block_end),
                    "mode": "playlist",
                    "section_label": "",
                    "genre_labels": [],
                    "_duration_minutes": duration,
                }
            )
            cursor = block_end

    open_labels = [
        ("Bloque libre", ["sin tematica"]),
        ("Sin tematica", ["mix variado"]),
        ("Cruce libre", ["sin tematica"]),
        ("Mezcla libre", ["catalogo completo"]),
    ]
    open_candidate_indices = [
        index
        for index, block in enumerate(raw_blocks)
        if not bool(block.get("_reserved_playlist"))
    ]
    outside_minutes = sum(
        int(raw_blocks[index]["_duration_minutes"]) for index in open_candidate_indices
    )
    min_open_minutes = math.ceil(open_ratio_min * (24 * 60))
    max_open_minutes = math.floor(open_ratio_max * (24 * 60))
    if outside_minutes <= 0 or min_open_minutes > outside_minutes:
        raise ScheduleValidationError(
            "NeuralCast reserved windows leave insufficient time for open rotation."
        )

    open_indices = choose_open_block_indices(
        block_minutes=[
            int(raw_blocks[index]["_duration_minutes"]) for index in open_candidate_indices
        ],
        open_ratio_min=min_open_minutes / outside_minutes,
        open_ratio_max=min(1.0, max_open_minutes / outside_minutes),
    )
    for relative_index in open_indices:
        block = raw_blocks[open_candidate_indices[relative_index]]
        section_label, genre_labels = rng.choice(open_labels)
        block["mode"] = "open"
        block["section_label"] = section_label
        block["genre_labels"] = list(genre_labels)
        block.pop("playlist_id", None)
        block.pop("playlist_name", None)

    return raw_blocks


def _build_station_scaffold(
    *,
    station_slug: str,
    playlists: Sequence[StationPlaylist],
    open_ratio_min: float,
    open_ratio_max: float,
    min_open_slots: int,
    max_open_slots: int,
    min_block_minutes: int,
    max_block_minutes: int,
    playlist_capacity: int,
    rng: random.Random,
) -> List[Dict[str, object]]:
    if _is_neuralcast(station_slug):
        return _build_neuralcast_reserved_scaffold(
            playlists=playlists,
            open_ratio_min=open_ratio_min,
            open_ratio_max=open_ratio_max,
            min_block_minutes=min_block_minutes,
            max_block_minutes=max_block_minutes,
            rng=rng,
        )

    return _build_randomized_scaffold(
        open_ratio_min=open_ratio_min,
        open_ratio_max=open_ratio_max,
        min_open_slots=min_open_slots,
        max_open_slots=max_open_slots,
        min_block_minutes=min_block_minutes,
        max_block_minutes=max_block_minutes,
        playlist_capacity=playlist_capacity,
        rng=rng,
    )


def _station_label_map(station_slug: str) -> Mapping[str, Tuple[str, Tuple[str, ...]]]:
    if not _is_neuralforge(station_slug):
        return {}

    return {
        _name_key("Classic Metal"): ("Metal clasico", ("metal clasico",)),
        _name_key("Celtic Metal"): ("Metal celta", ("metal celta", "folk metal")),
        _name_key("Epic Cinematic Orchestral"): (
            "Cinematico epico",
            ("orquestal", "epico"),
        ),
        _name_key("Fantasy Metal"): ("Fantasy Metal", ("fantasy metal", "power metal")),
        _name_key("Folk Metal"): ("Folk metal", ("folk metal",)),
        _name_key("Folk Rock"): ("Folk rock", ("folk rock",)),
        _name_key("Hard Rock"): ("Hard rock", ("hard rock",)),
        _name_key("Instrumental Prog Metal"): (
            "Prog instrumental",
            ("prog instrumental", "metal progresivo"),
        ),
        _name_key("Melodic Death Metal"): (
            "Death melodico",
            ("death melodico", "metal extremo"),
        ),
        _name_key("Neo Classical Metal"): (
            "Metal neo clasico",
            ("metal neo clasico", "virtuosismo"),
        ),
        _name_key("New Releases"): ("Novedades", ("novedades",)),
        _name_key("NWOBHM"): (
            "Heavy britanico clasico",
            ("nwobhm", "heavy metal clasico"),
        ),
        _name_key("Power Metal"): ("Power metal", ("power metal",)),
        _name_key("Prog Metal"): ("Metal progresivo", ("metal progresivo",)),
        _name_key("Symphonic Metal"): ("Metal sinfonico", ("metal sinfonico",)),
    }


def _neuralforge_combo_presets(
    playlist_by_name_key: Mapping[str, StationPlaylist],
) -> List[AssignmentCandidate]:
    preset_specs = [
        (
            ("Hard Rock", "Classic Metal"),
            "Hard y heavy",
            ("hard rock", "metal clasico"),
            1.08,
        ),
        (
            ("Prog Metal", "Instrumental Prog Metal"),
            "Progresivo e instrumental",
            ("metal progresivo", "prog instrumental"),
            1.10,
        ),
        (
            ("Power Metal", "Symphonic Metal"),
            "Power y sinfonico",
            ("power metal", "metal sinfonico"),
            1.15,
        ),
        (
            ("Folk Rock", "Folk Metal"),
            "Cruce folk",
            ("folk rock", "folk metal"),
            1.10,
        ),
        (
            ("Prog Metal", "Neo Classical Metal"),
            "Progresivo y neo clasico",
            ("metal progresivo", "metal neo clasico"),
            1.08,
        ),
        (
            ("Symphonic Metal", "Fantasy Metal"),
            "Fantasia sinfonica",
            ("metal sinfonico", "fantasy metal"),
            1.12,
        ),
        (
            ("Folk Metal", "Celtic Metal"),
            "Folk y celta",
            ("folk metal", "metal celta"),
            1.10,
        ),
        (
            ("Classic Metal", "NWOBHM"),
            "Raices del heavy",
            ("metal clasico", "nwobhm"),
            1.05,
        ),
    ]

    candidates: List[AssignmentCandidate] = []
    for names, section_label, genre_labels, bonus in preset_specs:
        resolved: List[StationPlaylist] = []
        missing = False
        for name in names:
            playlist = playlist_by_name_key.get(_name_key(name))
            if playlist is None or not playlist.is_enabled:
                missing = True
                break
            resolved.append(playlist)
        if missing or not resolved:
            continue

        base_weight = max(
            0.1, (sum(max(0.1, item.weight) for item in resolved) / len(resolved)) * bonus
        )
        candidates.append(
            AssignmentCandidate(
                playlist_ids=tuple(item.id for item in resolved),
                playlist_names=tuple(item.name for item in resolved),
                section_label=section_label,
                genre_labels=tuple(genre_labels),
                base_weight=base_weight,
                kind="combo",
            )
        )

    return candidates


def _solo_candidate(
    playlist: StationPlaylist,
    station_slug: str,
    label_map: Mapping[str, Tuple[str, Tuple[str, ...]]],
) -> AssignmentCandidate:
    alias, genres = label_map.get(
        _name_key(playlist.name), (playlist.name, (playlist.name,))
    )
    return AssignmentCandidate(
        playlist_ids=(playlist.id,),
        playlist_names=(playlist.name,),
        section_label=alias,
        genre_labels=tuple(genres),
        base_weight=max(0.1, playlist.weight),
        kind="solo",
    )


def _combo_probability_for_duration(duration_minutes: int) -> float:
    if duration_minutes < 60:
        return 0.0
    if duration_minutes < 90:
        return 0.12
    if duration_minutes < 120:
        return 0.25
    if duration_minutes < 150:
        return 0.40
    if duration_minutes < 180:
        return 0.50
    return 0.62


def _playlist_daily_repeat_limit(playlist: StationPlaylist, station_slug: str) -> int:
    if (
        _is_neuralforge(station_slug)
        and _name_key(playlist.name) == NEURALFORGE_MELODIC_DEATH_KEY
    ):
        return 2
    return 1


def _candidate_long_block_multiplier(
    candidate: AssignmentCandidate,
    station_slug: str,
    duration_minutes: int,
) -> float:
    if not _is_neuralforge(station_slug):
        return 1.0
    if not any(
        _name_key(playlist_name) == NEURALFORGE_MELODIC_DEATH_KEY
        for playlist_name in candidate.playlist_names
    ):
        return 1.0

    if duration_minutes < 45:
        return 0.80
    if duration_minutes < 60:
        return 1.05
    if duration_minutes < 75:
        return 1.45
    if duration_minutes < 90:
        return 1.85
    return 2.30


def _candidate_station_preference_multiplier(
    candidate: AssignmentCandidate,
    station_slug: str,
) -> float:
    if not _is_neuralcast(station_slug):
        return 1.0

    multiplier = 1.0
    for playlist_name in candidate.playlist_names:
        multiplier *= NEURALCAST_PLAYLIST_WEIGHT_MULTIPLIERS.get(
            _name_key(playlist_name),
            1.0,
        )
    return multiplier


def _weighted_choice(
    rng: random.Random,
    candidates: Sequence[AssignmentCandidate],
    weights: Sequence[float],
) -> AssignmentCandidate:
    total = sum(weights)
    if total <= 0:
        return rng.choice(list(candidates))
    threshold = rng.random() * total
    cumulative = 0.0
    for candidate, weight in zip(candidates, weights):
        cumulative += max(0.0, weight)
        if cumulative >= threshold:
            return candidate
    return candidates[-1]


def _candidate_selection_weight(
    *,
    candidate: AssignmentCandidate,
    station_slug: str,
    duration_minutes: int,
    usage_counts: Counter[str],
    usage_minutes: Counter[str],
    previous_playlist_ids: set[str],
    previous_signatures: Sequence[Tuple[str, ...]],
) -> float:
    weight = max(0.05, candidate.base_weight)

    if candidate.kind == "combo":
        if duration_minutes < 60:
            return 0.0
        if duration_minutes < 90:
            weight *= 0.30
        elif duration_minutes < 120:
            weight *= 0.70
        elif duration_minutes < 180:
            weight *= 1.15
        else:
            weight *= 1.35
    else:
        if duration_minutes >= 180:
            weight *= 1.10

    weight *= _candidate_long_block_multiplier(
        candidate=candidate,
        station_slug=station_slug,
        duration_minutes=duration_minutes,
    )
    weight *= _candidate_station_preference_multiplier(
        candidate=candidate,
        station_slug=station_slug,
    )

    candidate_id_set = set(candidate.playlist_ids)
    if previous_playlist_ids and (candidate_id_set & previous_playlist_ids):
        weight *= 0.10

    signature = tuple(candidate.playlist_ids)
    if previous_signatures and signature == previous_signatures[-1]:
        weight *= 0.05

    for lookback, prior in enumerate(reversed(previous_signatures[-3:]), start=1):
        overlap = candidate_id_set & set(prior)
        if overlap:
            weight *= 1.0 / (1.0 + (0.45 / lookback) * len(overlap))

    for playlist_id in candidate.playlist_ids:
        weight *= 1.0 / (1.0 + (0.35 * usage_counts[playlist_id]))
        weight *= 1.0 / (1.0 + (usage_minutes[playlist_id] / 600.0))

    return max(0.0, weight)


def _assign_playlists_to_scaffold(
    *,
    station_slug: str,
    playlists: Sequence[StationPlaylist],
    raw_blocks: List[Dict[str, object]],
    rng: random.Random,
    allow_combo_presets: bool = True,
) -> None:
    enabled_playlists = [playlist for playlist in playlists if playlist.is_enabled]
    if not enabled_playlists:
        raise ScheduleValidationError("No enabled playlists available for schedule generation.")

    label_map = _station_label_map(station_slug)
    playlist_by_name_key = {_name_key(item.name): item for item in enabled_playlists}
    repeat_limits = {
        playlist.id: _playlist_daily_repeat_limit(playlist, station_slug)
        for playlist in enabled_playlists
    }
    total_assignment_capacity = sum(repeat_limits.values())
    solo_candidates = [
        _solo_candidate(item, station_slug, label_map) for item in enabled_playlists
    ]
    combo_candidates = (
        _neuralforge_combo_presets(playlist_by_name_key)
        if allow_combo_presets and _is_neuralforge(station_slug)
        else []
    )

    playlist_blocks_with_durations: List[Tuple[int, int]] = [
        (index, int(block.get("_duration_minutes") or 0))
        for index, block in enumerate(raw_blocks)
        if str(block.get("mode")) == "playlist"
    ]
    playlist_block_count = len(playlist_blocks_with_durations)
    if playlist_block_count <= 0:
        for block in raw_blocks:
            block.pop("_duration_minutes", None)
        raise ScheduleValidationError(
            "Template cannot be fully open; at least one playlist block is required."
        )
    if playlist_block_count > total_assignment_capacity:
        raise ScheduleValidationError(
            f"Template contains {playlist_block_count} playlist blocks but only "
            f"{total_assignment_capacity} playlist assignment slots are available."
        )

    forced_combo_block_indices: set[int] = set()
    combo_slack = total_assignment_capacity - playlist_block_count
    if combo_candidates and combo_slack > 0:
        long_enough = [item for item in playlist_blocks_with_durations if item[1] >= 90]
        if long_enough:
            long_enough.sort(key=lambda item: (item[1], -item[0]), reverse=True)
            forced_combo_block_indices.add(long_enough[0][0])
            if combo_slack > 1 and len(long_enough) >= 4 and rng.random() < 0.35:
                for index, _duration in long_enough[1:]:
                    if abs(index - next(iter(forced_combo_block_indices))) > 1:
                        forced_combo_block_indices.add(index)
                        break

    usage_counts: Counter[str] = Counter()
    usage_minutes: Counter[str] = Counter()
    previous_playlist_ids: set[str] = set()
    previous_signatures: List[Tuple[str, ...]] = []
    assigned_playlist_blocks = 0

    for block_index, block in enumerate(raw_blocks):
        if str(block.get("mode")) != "playlist":
            continue

        duration_minutes = int(block.get("_duration_minutes") or 0)
        if block.get("playlist_id") and block.get("playlist_name"):
            playlist_ids = [str(block["playlist_id"])]
            if isinstance(block.get("playlist_ids"), list):
                playlist_ids = [str(value) for value in block["playlist_ids"]]
            previous_playlist_ids = set(playlist_ids)
            previous_signatures.append(tuple(playlist_ids))
            assigned_playlist_blocks += 1
            for playlist_id in playlist_ids:
                usage_counts[playlist_id] += 1
                usage_minutes[playlist_id] += duration_minutes
            continue

        remaining_blocks_including_current = playlist_block_count - assigned_playlist_blocks
        remaining_assignment_capacity = sum(
            max(0, repeat_limits[playlist.id] - usage_counts[playlist.id])
            for playlist in enabled_playlists
        )
        if remaining_blocks_including_current > remaining_assignment_capacity:
            raise ScheduleValidationError(
                "Insufficient playlist assignment capacity remaining for playlist blocks."
            )

        extra_playlist_budget = remaining_assignment_capacity - remaining_blocks_including_current
        force_combo = block_index in forced_combo_block_indices and extra_playlist_budget > 0
        allow_combos = bool(combo_candidates) and extra_playlist_budget > 0 and (
            force_combo or (rng.random() < _combo_probability_for_duration(duration_minutes))
        )
        candidate_pool = list(solo_candidates)
        if allow_combos and combo_candidates:
            if force_combo:
                candidate_pool = list(combo_candidates) + candidate_pool
            else:
                candidate_pool.extend(combo_candidates)

        if not candidate_pool:
            raise ScheduleValidationError("No playlist assignment candidates available.")

        candidate_pool = [
            candidate
            for candidate in candidate_pool
            if all(
                usage_counts[playlist_id] < repeat_limits.get(playlist_id, 1)
                for playlist_id in candidate.playlist_ids
            )
        ]
        if not candidate_pool:
            raise ScheduleValidationError(
                "No valid playlist candidates remain within daily repeat limits."
            )

        no_adjacent_overlap_pool = [
            candidate
            for candidate in candidate_pool
            if not (set(candidate.playlist_ids) & previous_playlist_ids)
        ]
        if no_adjacent_overlap_pool:
            candidate_pool = no_adjacent_overlap_pool

        if force_combo:
            combo_only_pool = [
                candidate for candidate in candidate_pool if candidate.kind == "combo"
            ]
            if combo_only_pool:
                candidate_pool = combo_only_pool

        # Choosing a combo spends more assignment capacity. Keep enough capacity
        # available so every remaining playlist block can still be filled.
        remaining_future_blocks = remaining_blocks_including_current - 1
        feasible_pool = [
            candidate
            for candidate in candidate_pool
            if (remaining_assignment_capacity - len(candidate.playlist_ids))
            >= remaining_future_blocks
        ]
        if feasible_pool:
            candidate_pool = feasible_pool
        else:
            raise ScheduleValidationError(
                "No feasible candidates remain while preserving repeat limits "
                "across remaining blocks."
            )

        weights = [
            _candidate_selection_weight(
                candidate=candidate,
                station_slug=station_slug,
                duration_minutes=duration_minutes,
                usage_counts=usage_counts,
                usage_minutes=usage_minutes,
                previous_playlist_ids=previous_playlist_ids,
                previous_signatures=previous_signatures,
            )
            for candidate in candidate_pool
        ]
        chosen = _weighted_choice(rng, candidate_pool, weights)

        block["section_label"] = chosen.section_label
        block["genre_labels"] = list(chosen.genre_labels)
        if len(chosen.playlist_ids) == 1:
            block["playlist_id"] = chosen.playlist_ids[0]
            block["playlist_name"] = chosen.playlist_names[0]
            block.pop("playlist_ids", None)
            block.pop("playlist_names", None)
        else:
            block["playlist_ids"] = list(chosen.playlist_ids)
            block["playlist_names"] = list(chosen.playlist_names)
            block["playlist_id"] = chosen.playlist_ids[0]
            block["playlist_name"] = chosen.playlist_names[0]

        previous_playlist_ids = set(chosen.playlist_ids)
        previous_signatures.append(tuple(chosen.playlist_ids))
        assigned_playlist_blocks += 1
        for playlist_id in chosen.playlist_ids:
            usage_counts[playlist_id] += 1
            usage_minutes[playlist_id] += duration_minutes

    for block in raw_blocks:
        block.pop("_duration_minutes", None)


def build_weekly_plan_with_code(
    station_slug: str,
    station_name: str,
    timezone_name: str,
    week_start: dt.date,
    week_end: dt.date,
    playlists: Sequence[StationPlaylist],
    open_ratio_min: float,
    open_ratio_max: float,
    min_open_slots: int,
    max_open_slots: int,
    min_block_minutes: int,
    max_block_minutes: int,
    model: Optional[str] = None,  # Ignored; kept for import compatibility.
    seed_mode: str = DEFAULT_SCHEDULE_SEED_MODE,
    seed_salt: Optional[str] = None,
) -> WeeklySchedulePlan:
    _ = model

    enabled_playlists = [playlist for playlist in playlists if playlist.is_enabled]
    playlist_by_id = {playlist.id: playlist for playlist in enabled_playlists}
    if not playlist_by_id:
        raise RuntimeError("No enabled playlists available for schedule generation.")

    seed, normalized_seed_mode, resolved_seed_salt = resolve_schedule_seed(
        station_slug=station_slug,
        week_start=week_start,
        timezone_name=timezone_name,
        playlists=playlists,
        open_ratio_min=open_ratio_min,
        open_ratio_max=open_ratio_max,
        min_open_slots=min_open_slots,
        max_open_slots=max_open_slots,
        min_block_minutes=min_block_minutes,
        max_block_minutes=max_block_minutes,
        seed_mode=seed_mode,
        seed_salt=seed_salt,
    )

    last_error: Optional[Exception] = None
    for attempt in range(1, 9):
        rng = random.Random(seed + (attempt * 1009))
        try:
            raw_blocks = _build_station_scaffold(
                station_slug=station_slug,
                playlists=enabled_playlists,
                open_ratio_min=open_ratio_min,
                open_ratio_max=open_ratio_max,
                min_open_slots=min_open_slots,
                max_open_slots=max_open_slots,
                min_block_minutes=min_block_minutes,
                max_block_minutes=max_block_minutes,
                playlist_capacity=len(enabled_playlists),
                rng=rng,
            )
            _assign_playlists_to_scaffold(
                station_slug=station_slug,
                playlists=enabled_playlists,
                raw_blocks=raw_blocks,
                rng=rng,
            )
            daily_template = validate_daily_template(
                raw_blocks=raw_blocks,
                playlist_by_id=playlist_by_id,
                open_ratio_min=open_ratio_min,
                open_ratio_max=open_ratio_max,
                min_block_minutes=min_block_minutes,
                max_block_minutes=max_block_minutes,
                enforce_unscheduled_window=False,
            )
            expanded = expand_daily_template_to_week(daily_template, week_start)
            plan_hash = build_plan_hash(
                station=station_slug,
                timezone_name=timezone_name,
                week_start=week_start,
                daily_template=daily_template,
            )
            combo_note = ""
            if station_slug.strip().lower() == "neuralforge":
                combo_note = (
                    " NeuralForge usa combinaciones curadas (hard rock+classic, "
                    "prog+instrumental, power+sinfonico, folk rock+folk metal, "
                    "prog+neo clasico, sinfonico+fantasy, folk+celtic, classic+nwobhm)."
                )
            if normalized_seed_mode == SCHEDULE_SEED_MODE_STABLE_WEEK:
                seed_note = "estable por semana"
            elif normalized_seed_mode == SCHEDULE_SEED_MODE_CUSTOM:
                seed_note = f"reproducible con semilla personalizada '{resolved_seed_salt}'"
            else:
                seed_note = f"rerolleada con clave '{resolved_seed_salt}'"
            rationale = (
                "Plan semanal generado por codigo (sin LLM), con bloques variables, "
                "ventanas abiertas largas repartidas durante el dia y seleccion pseudoaleatoria "
                f"{seed_note}, respetando limites diarios de repeticion por playlist."
                f"{combo_note}"
            )
            return WeeklySchedulePlan(
                station=station_slug,
                station_name=station_name,
                timezone=timezone_name,
                week_start_local_date=week_start.isoformat(),
                week_end_local_date=week_end.isoformat(),
                generated_at_utc=dt.datetime.now(dt.timezone.utc).isoformat(),
                seed_mode=normalized_seed_mode,
                seed_salt=resolved_seed_salt,
                resolved_seed=seed,
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
                "[code] Schedule generation attempt failed (%s/8): %s",
                attempt,
                exc,
            )

    LOGGER.warning(
        "[code] Retrying with fallback mode (combos disabled) after repeated failures: %s",
        last_error,
    )
    fallback_error: Optional[Exception] = last_error
    for attempt in range(1, 25):
        rng = random.Random(seed + 100_000 + (attempt * 3137))
        try:
            raw_blocks = _build_station_scaffold(
                station_slug=station_slug,
                playlists=enabled_playlists,
                open_ratio_min=open_ratio_min,
                open_ratio_max=open_ratio_max,
                min_open_slots=min_open_slots,
                max_open_slots=max_open_slots,
                min_block_minutes=min_block_minutes,
                max_block_minutes=max_block_minutes,
                playlist_capacity=len(enabled_playlists),
                rng=rng,
            )
            _assign_playlists_to_scaffold(
                station_slug=station_slug,
                playlists=enabled_playlists,
                raw_blocks=raw_blocks,
                rng=rng,
                allow_combo_presets=False,
            )
            daily_template = validate_daily_template(
                raw_blocks=raw_blocks,
                playlist_by_id=playlist_by_id,
                open_ratio_min=open_ratio_min,
                open_ratio_max=open_ratio_max,
                min_block_minutes=min_block_minutes,
                max_block_minutes=max_block_minutes,
                enforce_unscheduled_window=False,
            )
            expanded = expand_daily_template_to_week(daily_template, week_start)
            plan_hash = build_plan_hash(
                station=station_slug,
                timezone_name=timezone_name,
                week_start=week_start,
                daily_template=daily_template,
            )
            rationale = (
                "Plan semanal generado por codigo (sin LLM) en modo de respaldo, "
                "sin combinaciones curadas, con ventanas abiertas largas repartidas durante el dia "
                "y respetando limites diarios de repeticion por playlist. "
                f"Seed mode={normalized_seed_mode}"
            )
            if fallback_error is not None:
                rationale = f"{rationale} Error previo: {fallback_error}"

            return WeeklySchedulePlan(
                station=station_slug,
                station_name=station_name,
                timezone=timezone_name,
                week_start_local_date=week_start.isoformat(),
                week_end_local_date=week_end.isoformat(),
                generated_at_utc=dt.datetime.now(dt.timezone.utc).isoformat(),
                seed_mode=normalized_seed_mode,
                seed_salt=resolved_seed_salt,
                resolved_seed=seed,
                open_ratio_min=open_ratio_min,
                open_ratio_max=open_ratio_max,
                daily_template=daily_template,
                expanded_blocks=expanded,
                rationale=rationale,
                plan_hash=plan_hash,
            )
        except Exception as exc:  # noqa: BLE001
            fallback_error = exc
            LOGGER.warning(
                "[code] Fallback generation attempt failed (%s/24): %s",
                attempt,
                exc,
            )

    raise RuntimeError(
        "Unable to generate weekly schedule with current constraints "
        f"(repeat limits, open slot bounds, duration bounds): {fallback_error}"
    )


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
    model: Optional[str] = None,
    *,
    min_open_slots: int = DEFAULT_MIN_OPEN_SLOTS,
    max_open_slots: int = DEFAULT_MAX_OPEN_SLOTS,
    seed_mode: str = DEFAULT_SCHEDULE_SEED_MODE,
    seed_salt: Optional[str] = None,
) -> WeeklySchedulePlan:
    """Compatibility wrapper retained for existing imports."""
    return build_weekly_plan_with_code(
        station_slug=station_slug,
        station_name=station_name,
        timezone_name=timezone_name,
        week_start=week_start,
        week_end=week_end,
        playlists=playlists,
        open_ratio_min=open_ratio_min,
        open_ratio_max=open_ratio_max,
        min_open_slots=min_open_slots,
        max_open_slots=max_open_slots,
        min_block_minutes=min_block_minutes,
        max_block_minutes=max_block_minutes,
        model=model,
        seed_mode=seed_mode,
        seed_salt=seed_salt,
    )
