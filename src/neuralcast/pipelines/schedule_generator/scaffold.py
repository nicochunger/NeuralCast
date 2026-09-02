"""Daily schedule scaffold construction and reserved station layouts."""

from __future__ import annotations

import math
import random
from typing import Dict, List, Sequence, Tuple

from .config import SCHEDULE_TIME_GRID_MINUTES
from .models import ScheduleValidationError, StationPlaylist
from .policy import (
    _build_partition_with_specs,
    _build_random_duration_partition,
    _ceil_to_grid,
    _duration_step,
    _floor_to_grid,
    _is_neuralcast,
    _name_key,
)
from .template import choose_open_block_indices, format_hhmm


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
    evening_playlist_names = {
        "aspen vibes",
        "acoustic singer-songwriter",
    }
    return _find_neuralcast_reserved_playlists(
        playlists,
        lambda playlist: _name_key(playlist.name) in evening_playlist_names,
        "Aspen Vibes or Acoustic Singer-Songwriter",
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


__all__ = ["_build_station_scaffold", "_build_randomized_scaffold"]
