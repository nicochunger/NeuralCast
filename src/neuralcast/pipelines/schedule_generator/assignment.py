"""Playlist candidate scoring and allocation for daily schedule scaffolds."""

from __future__ import annotations

import random
from collections import Counter
from dataclasses import dataclass
from typing import Dict, List, Mapping, Sequence, Tuple

from .config import NEURALCAST_PLAYLIST_WEIGHT_MULTIPLIERS
from .models import ScheduleValidationError, StationPlaylist
from .policy import (
    NEURALFORGE_MELODIC_DEATH_KEY,
    _is_neuralcast,
    _is_neuralforge,
    _name_key,
)


@dataclass(frozen=True)
class AssignmentCandidate:
    playlist_ids: Tuple[str, ...]
    playlist_names: Tuple[str, ...]
    section_label: str
    genre_labels: Tuple[str, ...]
    base_weight: float
    kind: str  # "solo" | "combo"


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


__all__ = ["AssignmentCandidate", "_assign_playlists_to_scaffold"]
