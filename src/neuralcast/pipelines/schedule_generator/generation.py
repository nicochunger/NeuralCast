"""Code-only weekly plan assembly for fixed-template schedule generation."""

from __future__ import annotations

import datetime as dt
import hashlib
import math
import random
from collections import Counter
from dataclasses import dataclass
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from .config import (
    DEFAULT_MAX_OPEN_SLOTS,
    DEFAULT_MIN_OPEN_SLOTS,
    DEFAULT_TEMPLATE_TARGET_BLOCK_MINUTES,
    LOGGER,
    UNSCHEDULED_WINDOW_END_MINUTE,
    UNSCHEDULED_WINDOW_START_MINUTE,
    UNSCHEDULED_WINDOW_TOTAL_MINUTES,
)
from .models import ScheduleValidationError, StationPlaylist, WeeklySchedulePlan
from .template import (
    build_deterministic_daily_template,
    build_duration_partition,
    build_plan_hash,
    choose_open_block_indices,
    format_hhmm,
    overlaps_unscheduled_window,
    validate_daily_template,
    expand_daily_template_to_week,
)


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


def _candidate_durations(
    min_block_minutes: int,
    max_block_minutes: int,
    target_minutes: int,
) -> List[int]:
    preferred = max(min_block_minutes, min(max_block_minutes, target_minutes))
    values = {
        value
        for value in range(min_block_minutes, max_block_minutes + 1)
        if value % 30 == 0
    }
    values.update(
        {
            min_block_minutes,
            max_block_minutes,
            preferred,
            max(min_block_minutes, min(max_block_minutes, preferred - 30)),
            max(min_block_minutes, min(max_block_minutes, preferred + 30)),
        }
    )
    return sorted(value for value in values if min_block_minutes <= value <= max_block_minutes)


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


def _build_randomized_scaffold(
    *,
    open_ratio_min: float,
    open_ratio_max: float,
    min_open_slots: int,
    max_open_slots: int,
    min_block_minutes: int,
    max_block_minutes: int,
    rng: random.Random,
) -> List[Dict[str, object]]:
    day_total_minutes = 24 * 60
    min_open_minutes = math.ceil(open_ratio_min * day_total_minutes)
    max_open_minutes = math.floor(open_ratio_max * day_total_minutes)

    if max_open_minutes < UNSCHEDULED_WINDOW_TOTAL_MINUTES:
        raise ScheduleValidationError(
            "open_ratio_max is too low for fixed unscheduled window 22:00-06:00 "
            f"(requires at least {UNSCHEDULED_WINDOW_TOTAL_MINUTES / day_total_minutes:.3f})."
        )

    block_durations: List[int] = []
    segment_totals = (
        UNSCHEDULED_WINDOW_END_MINUTE,  # 00:00-06:00
        UNSCHEDULED_WINDOW_START_MINUTE - UNSCHEDULED_WINDOW_END_MINUTE,  # 06:00-22:00
        (24 * 60) - UNSCHEDULED_WINDOW_START_MINUTE,  # 22:00-24:00
    )
    for segment_total in segment_totals:
        block_durations.extend(
            _build_random_duration_partition(
                min_block_minutes=min_block_minutes,
                max_block_minutes=max_block_minutes,
                total_minutes=segment_total,
                rng=rng,
            )
        )

    forced_open_indices: set[int] = set()
    daytime_indices: List[int] = []
    cursor = 0
    for index, duration in enumerate(block_durations):
        block_end = cursor + duration
        if overlaps_unscheduled_window(cursor, block_end):
            forced_open_indices.add(index)
        else:
            daytime_indices.append(index)
        cursor = block_end

    forced_open_minutes = sum(block_durations[index] for index in forced_open_indices)
    additional_open_min = max(0, min_open_minutes - forced_open_minutes)
    additional_open_max = max_open_minutes - forced_open_minutes
    if additional_open_max < 0:
        raise ScheduleValidationError(
            "open_ratio_max is too low after applying fixed unscheduled window."
        )

    optional_open_indices: set[int] = set()
    daytime_block_minutes = [block_durations[index] for index in daytime_indices]
    total_daytime_minutes = sum(daytime_block_minutes)
    if additional_open_min > total_daytime_minutes:
        raise ScheduleValidationError(
            "open_ratio_min is too high after applying fixed unscheduled window 22:00-06:00."
        )

    if total_daytime_minutes > 0 and additional_open_max > 0:
        day_ratio_min = additional_open_min / total_daytime_minutes
        day_ratio_max = min(1.0, additional_open_max / total_daytime_minutes)
        if day_ratio_min > day_ratio_max:
            raise ScheduleValidationError(
                "No feasible daytime open-slot ratio after applying 22:00-06:00 unscheduled window."
            )
        chosen_daytime_indices = choose_open_block_indices(
            block_minutes=daytime_block_minutes,
            open_ratio_min=day_ratio_min,
            open_ratio_max=day_ratio_max,
        )
        optional_open_indices = {
            daytime_indices[relative_index] for relative_index in chosen_daytime_indices
        }

    open_indices = forced_open_indices | optional_open_indices
    open_slot_count = len(open_indices)
    if open_slot_count < min_open_slots or open_slot_count > max_open_slots:
        raise ScheduleValidationError(
            f"Open-slot count {open_slot_count} outside bounds "
            f"[{min_open_slots}, {max_open_slots}]."
        )

    open_labels_day = [
        ("Rotacion abierta", ["mix variado"]),
        ("Ventana abierta", ["rotacion libre"]),
        ("Cruce libre", ["mix variado"]),
    ]
    open_labels_night = [
        ("Rotacion nocturna", ["mix variado"]),
        ("Noche abierta", ["rotacion nocturna"]),
        ("Tramo nocturno libre", ["mix variado"]),
    ]

    raw_blocks: List[Dict[str, object]] = []
    start_minute = 0
    for block_index, duration in enumerate(block_durations):
        end_minute = start_minute + duration
        start_time = format_hhmm(start_minute)
        end_time = format_hhmm(end_minute)

        if block_index in open_indices:
            label_pool = (
                open_labels_night
                if end_minute <= UNSCHEDULED_WINDOW_END_MINUTE
                or start_minute >= UNSCHEDULED_WINDOW_START_MINUTE
                else open_labels_day
            )
            section_label, genre_labels = rng.choice(label_pool)
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
        else:
            raw_blocks.append(
                {
                    "start_time_local": start_time,
                    "end_time_local": end_time,
                    "mode": "playlist",
                    "section_label": "",
                    "genre_labels": [],
                    "_duration_minutes": duration,
                }
            )

        start_minute = end_minute

    return raw_blocks


def _station_label_map(station_slug: str) -> Mapping[str, Tuple[str, Tuple[str, ...]]]:
    if station_slug.strip().lower() != "neuralforge":
        return {}

    return {
        _name_key("Classic Metal"): ("Metal clasico", ("metal clasico",)),
        _name_key("Epic Cinematic Orchestral"): (
            "Cinematico epico",
            ("orquestal", "epico"),
        ),
        _name_key("Folk Metal"): ("Folk metal", ("folk metal",)),
        _name_key("Folk Rock"): ("Folk rock", ("folk rock",)),
        _name_key("Instrumental Prog Metal"): (
            "Prog instrumental",
            ("prog instrumental", "metal progresivo"),
        ),
        _name_key("Melodic Death Metal"): (
            "Death melodico",
            ("death melodico", "metal extremo"),
        ),
        _name_key("New Releases"): ("Novedades", ("novedades",)),
        _name_key("Power Metal"): ("Power metal", ("power metal",)),
        _name_key("Prog Metal"): ("Metal progresivo", ("metal progresivo",)),
        _name_key("Symphonic Metal"): ("Metal sinfonico", ("metal sinfonico",)),
    }


def _neuralforge_combo_presets(
    playlist_by_name_key: Mapping[str, StationPlaylist],
) -> List[AssignmentCandidate]:
    preset_specs = [
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
) -> None:
    enabled_playlists = [playlist for playlist in playlists if playlist.is_enabled]
    if not enabled_playlists:
        raise ScheduleValidationError("No enabled playlists available for schedule generation.")

    label_map = _station_label_map(station_slug)
    playlist_by_name_key = {_name_key(item.name): item for item in enabled_playlists}
    solo_candidates = [
        _solo_candidate(item, station_slug, label_map) for item in enabled_playlists
    ]
    combo_candidates = (
        _neuralforge_combo_presets(playlist_by_name_key)
        if station_slug.strip().lower() == "neuralforge"
        else []
    )

    forced_combo_block_indices: set[int] = set()
    if combo_candidates:
        playlist_blocks_with_durations: List[Tuple[int, int]] = [
            (index, int(block.get("_duration_minutes") or 0))
            for index, block in enumerate(raw_blocks)
            if str(block.get("mode")) == "playlist"
        ]
        long_enough = [item for item in playlist_blocks_with_durations if item[1] >= 90]
        if long_enough:
            long_enough.sort(key=lambda item: (item[1], -item[0]), reverse=True)
            forced_combo_block_indices.add(long_enough[0][0])
            if len(long_enough) >= 4 and rng.random() < 0.35:
                for index, _duration in long_enough[1:]:
                    if abs(index - next(iter(forced_combo_block_indices))) > 1:
                        forced_combo_block_indices.add(index)
                        break

    usage_counts: Counter[str] = Counter()
    usage_minutes: Counter[str] = Counter()
    previous_playlist_ids: set[str] = set()
    previous_signatures: List[Tuple[str, ...]] = []

    for block_index, block in enumerate(raw_blocks):
        if str(block.get("mode")) != "playlist":
            continue

        duration_minutes = int(block.get("_duration_minutes") or 0)
        force_combo = block_index in forced_combo_block_indices
        allow_combos = bool(combo_candidates) and (
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

        no_overlap_pool = [
            candidate
            for candidate in candidate_pool
            if not (set(candidate.playlist_ids) & previous_playlist_ids)
        ]
        if no_overlap_pool:
            candidate_pool = no_overlap_pool

        if force_combo:
            combo_only_pool = [candidate for candidate in candidate_pool if candidate.kind == "combo"]
            if combo_only_pool:
                candidate_pool = combo_only_pool

        weights = [
            _candidate_selection_weight(
                candidate=candidate,
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
) -> WeeklySchedulePlan:
    _ = model

    enabled_playlists = [playlist for playlist in playlists if playlist.is_enabled]
    playlist_by_id = {playlist.id: playlist for playlist in enabled_playlists}
    if not playlist_by_id:
        raise RuntimeError("No enabled playlists available for schedule generation.")

    seed = _stable_seed(
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

    last_error: Optional[Exception] = None
    for attempt in range(1, 9):
        rng = random.Random(seed + (attempt * 1009))
        try:
            raw_blocks = _build_randomized_scaffold(
                open_ratio_min=open_ratio_min,
                open_ratio_max=open_ratio_max,
                min_open_slots=min_open_slots,
                max_open_slots=max_open_slots,
                min_block_minutes=min_block_minutes,
                max_block_minutes=max_block_minutes,
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
            )
            expanded = expand_daily_template_to_week(daily_template, week_start)
            plan_hash = build_plan_hash(
                station=station_slug,
                timezone_name=timezone_name,
                week_start=week_start,
                daily_template=daily_template,
            )
            combo_note = (
                " NeuralForge usa combinaciones curadas (prog+instrumental, power+sinfonico, folk rock+folk metal)."
                if station_slug.strip().lower() == "neuralforge"
                else ""
            )
            rationale = (
                "Plan semanal generado por codigo (sin LLM), con bloques variables, "
                "ventana nocturna abierta fija y seleccion pseudoaleatoria estable por semana."
                f"{combo_note}"
            )
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
                "[code] Schedule generation attempt failed (%s/8): %s",
                attempt,
                exc,
            )

    LOGGER.warning(
        "[code] Falling back to deterministic template after repeated failures: %s",
        last_error,
    )
    deterministic_template = build_deterministic_daily_template(
        playlist_by_id=playlist_by_id,
        open_ratio_min=open_ratio_min,
        open_ratio_max=open_ratio_max,
        min_block_minutes=min_block_minutes,
        max_block_minutes=max_block_minutes,
    )
    deterministic_open_slots = sum(
        1 for block in deterministic_template if block.mode == "open"
    )
    if deterministic_open_slots < min_open_slots or deterministic_open_slots > max_open_slots:
        raise ScheduleValidationError(
            "Deterministic fallback template violates open-slot count bounds "
            f"[{min_open_slots}, {max_open_slots}] (got {deterministic_open_slots})."
        )
    expanded = expand_daily_template_to_week(deterministic_template, week_start)
    plan_hash = build_plan_hash(
        station=station_slug,
        timezone_name=timezone_name,
        week_start=week_start,
        daily_template=deterministic_template,
    )
    rationale = "Plan semanal deterministico de respaldo (sin LLM)."
    if last_error is not None:
        rationale = f"{rationale} Error previo: {last_error}"

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
    )
