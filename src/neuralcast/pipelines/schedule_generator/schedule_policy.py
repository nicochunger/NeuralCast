"""Seed, station identity, and duration-partition policy for schedules."""

from __future__ import annotations

import datetime as dt
import hashlib
import random
import secrets
from typing import List, Optional, Sequence, Tuple

from .config import DEFAULT_TEMPLATE_TARGET_BLOCK_MINUTES, SCHEDULE_TIME_GRID_MINUTES
from .models import ScheduleValidationError, StationPlaylist
from .template import build_duration_partition


SCHEDULE_SEED_MODE_STABLE_WEEK = "stable_week"
SCHEDULE_SEED_MODE_FRESH = "fresh"
SCHEDULE_SEED_MODE_CUSTOM = "custom"
SUPPORTED_SCHEDULE_SEED_MODES = (
    SCHEDULE_SEED_MODE_STABLE_WEEK,
    SCHEDULE_SEED_MODE_FRESH,
    SCHEDULE_SEED_MODE_CUSTOM,
)
DEFAULT_SCHEDULE_SEED_MODE = SCHEDULE_SEED_MODE_STABLE_WEEK


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


__all__ = [
    "DEFAULT_SCHEDULE_SEED_MODE",
    "NEURALFORGE_MELODIC_DEATH_KEY",
    "SCHEDULE_SEED_MODE_CUSTOM",
    "SCHEDULE_SEED_MODE_FRESH",
    "SCHEDULE_SEED_MODE_STABLE_WEEK",
    "SUPPORTED_SCHEDULE_SEED_MODES",
    "_ceil_to_grid",
    "_build_partition_with_specs",
    "_build_random_duration_partition",
    "_duration_step",
    "_floor_to_grid",
    "_is_neuralcast",
    "_is_neuralforge",
    "_name_key",
    "resolve_schedule_seed",
]
