"""Public planner facade for fixed-template schedule generation."""

from __future__ import annotations

import datetime as dt
import random
from typing import Optional, Sequence

from .config import LOGGER
from .models import StationPlaylist, WeeklySchedulePlan
from .template import (
    build_plan_hash,
    expand_daily_template_to_week,
    validate_daily_template,
)
from .schedule_policy import (
    DEFAULT_SCHEDULE_SEED_MODE,
    SCHEDULE_SEED_MODE_CUSTOM,
    SCHEDULE_SEED_MODE_FRESH,
    SCHEDULE_SEED_MODE_STABLE_WEEK,
    SUPPORTED_SCHEDULE_SEED_MODES,
    _name_key,
    resolve_schedule_seed,
)

from .schedule_scaffold import _build_randomized_scaffold, _build_station_scaffold

from .schedule_assignment import (
    _assign_playlists_to_scaffold,
    _candidate_selection_weight,
    _neuralforge_combo_presets,
    _solo_candidate,
    _station_label_map,
)


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




__all__ = [
    "DEFAULT_SCHEDULE_SEED_MODE",
    "SCHEDULE_SEED_MODE_CUSTOM",
    "SCHEDULE_SEED_MODE_FRESH",
    "SCHEDULE_SEED_MODE_STABLE_WEEK",
    "SUPPORTED_SCHEDULE_SEED_MODES",
    "build_weekly_plan_with_code",
    "resolve_schedule_seed",
]
