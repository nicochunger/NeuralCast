"""Public orchestration facade for AI host script generation."""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .archetype_policies import ResolvedArchetypeProfile
from .channels import HostLocale, get_channel_registry
from .config import LOGGER
from .concert_generation import (
    _generate_concert_check_script,
    artist_matches_targets,
    normalize_concert_country,
    parse_concert_event_date,
    parse_concert_output,
    validate_concert_segment,
)
from .models import (
    Archetype,
    GeneratedSegmentMetadata,
    OrchestratorState,
    QueueTrack,
    ScheduleContext,
    StationPersonality,
    TrackFocus,
    TrackMetadata,
)
from .state import choose_hook, sample_generation_settings
from .news_generation import (
    _generate_news_script,
    parse_news_output,
    pick_news_topics,
    validate_news_freshness_and_dedup,
)
from .prompts import (
    build_prompt,
    build_system_prompt,
    build_tts_instructions,
    format_shared_input,
    resolve_station_personality,
    station_name_for_generation,
)
from .script_processing import (
    _postprocess_schedule_script,
    cleanup_generated_script,
    ensure_mid_block_reference,
    ensure_schedule_genre_reference,
)
from .structured_output import parse_structured_script_and_meta, parse_timestamp
from .text_generation import gemini_generate_text
from .utils import run_with_retries


@dataclass(frozen=True)
class ArchetypePromptVariants:
    short_story_focus: Optional[str] = None
    album_spotlight_focus: Optional[str] = None
    era_snapshot_lane: Optional[str] = None
    era_snapshot_focus: Optional[str] = None
    deep_dive_lane: Optional[str] = None
    deep_dive_focus: Optional[str] = None


def _default_locale() -> HostLocale:
    return get_channel_registry().locales["es-AR"]


def _resolved_locale(locale: Optional[HostLocale]) -> HostLocale:
    return locale or _default_locale()


def _segment_metadata_for_archetype(
    archetype: Archetype,
    prompt_kwargs: Mapping[str, Any],
) -> GeneratedSegmentMetadata:
    focus_keys = {
        Archetype.SHORT_STORY: "short_story_focus",
        Archetype.ALBUM_SPOTLIGHT: "album_spotlight_focus",
        Archetype.ERA_SNAPSHOT: "era_snapshot_focus",
        Archetype.DEEP_DIVE: "deep_dive_focus",
    }
    focus_value = prompt_kwargs.get(focus_keys.get(archetype, ""))
    try:
        focus = TrackFocus(focus_value) if focus_value else None
    except ValueError:
        focus = None
    return GeneratedSegmentMetadata(track_focus=focus)


def should_enable_search(
    archetype: Archetype,
    _angle: Optional[str],
    archetype_policy: Optional[ResolvedArchetypeProfile] = None,
) -> bool:
    if archetype_policy is not None:
        return archetype_policy.for_archetype(archetype).search_enabled
    return archetype in {
        Archetype.NEWS,
        Archetype.SHORT_STORY,
        Archetype.ALBUM_SPOTLIGHT,
        Archetype.ERA_SNAPSHOT,
        Archetype.DEEP_DIVE,
        Archetype.CONCERT_CHECK,
    }


def select_album_spotlight_focus(
    current_meta: TrackMetadata,
    next_meta: TrackMetadata,
    rng: random.Random,
) -> str:
    current_has_album = bool((current_meta.album or "").strip())
    next_has_album = bool((next_meta.album or "").strip())
    if current_has_album and not next_has_album:
        return "current"
    if next_has_album and not current_has_album:
        return "next"
    return "current" if rng.random() < 0.5 else "next"


def select_era_snapshot_lane(rng: random.Random) -> str:
    return rng.choice(
        [
            "escena y geografia",
            "mutacion del genero",
            "momento cultural / industrial",
            "la banda dentro de esa epoca",
        ]
    )


def build_local_ultra_minimal_script(
    current_track: QueueTrack,
    next_track: QueueTrack,
    schedule_context: Optional[ScheduleContext],
    rng: random.Random,
    locale: Optional[HostLocale] = None,
) -> str:
    locale = _resolved_locale(locale)
    current_artist = str(current_track.artist or "").strip()
    current_title = str(current_track.title or "").strip()
    next_artist = str(next_track.artist or "").strip()
    next_title = str(next_track.title or "").strip()

    options: List[str] = []
    if current_title and next_artist and next_title:
        templates = locale.presentation.get("fallback_current_next") or []
        options.extend(
            str(template).format(
                current_artist=current_artist,
                current_title=current_title,
                next_artist=next_artist,
                next_title=next_title,
            )
            for template in templates
        )
    if next_artist and next_title:
        templates = locale.presentation.get("fallback_next") or []
        options.extend(
            str(template).format(next_artist=next_artist, next_title=next_title)
            for template in templates
        )

    fallback_script = (
        rng.choice(options)
        if options
        else str(locale.presentation.get("fallback_music") or "Music continues.")
    )
    return _postprocess_schedule_script(
        script_text=fallback_script,
        archetype=Archetype.ULTRA_MINIMAL,
        schedule_context=schedule_context,
        rng=rng,
        locale=locale,
    )


def fallback_to_ultra_minimal(
    station_name: str,
    personality: StationPersonality,
    current_track: QueueTrack,
    next_track: QueueTrack,
    upcoming_tracks: Sequence[QueueTrack],
    current_meta: TrackMetadata,
    next_meta: TrackMetadata,
    banned_list: Sequence[str],
    schedule_context: Optional[ScheduleContext],
    state: OrchestratorState,
    rng: random.Random,
    locale: Optional[HostLocale] = None,
    archetype_policy: Optional[ResolvedArchetypeProfile] = None,
) -> Tuple[str, GeneratedSegmentMetadata, Archetype]:
    locale = _resolved_locale(locale)
    fallback_hook = choose_hook(
        Archetype.ULTRA_MINIMAL, state, rng, archetype_policy
    )
    fallback_script, _, fallback_arch = generate_archetype_script(
        archetype=Archetype.ULTRA_MINIMAL,
        station_name=station_name,
        personality=personality,
        current_track=current_track,
        next_track=next_track,
        upcoming_tracks=upcoming_tracks,
        current_meta=current_meta,
        next_meta=next_meta,
        angle=None,
        hook=fallback_hook,
        banned_list=banned_list,
        schedule_context=schedule_context,
        state=state,
        rng=rng,
        forced_mode=False,
        allow_ultra_minimal_fallback=False,
        locale=locale,
        archetype_policy=archetype_policy,
    )
    return fallback_script, GeneratedSegmentMetadata(), fallback_arch


def _resolve_prompt_variants(
    archetype: Archetype,
    forced_track_focus: Optional[TrackFocus],
    current_meta: TrackMetadata,
    next_meta: TrackMetadata,
    rng: random.Random,
) -> ArchetypePromptVariants:
    if archetype == Archetype.SHORT_STORY:
        if forced_track_focus is not None:
            short_story_focus = forced_track_focus.value
            LOGGER.info("[short_story] Focus mode forced: %s", short_story_focus)
        else:
            short_story_focus = "current" if rng.random() < 0.5 else "next"
            LOGGER.info("[short_story] Focus mode selected: %s", short_story_focus)
        return ArchetypePromptVariants(short_story_focus=short_story_focus)

    if archetype == Archetype.ALBUM_SPOTLIGHT:
        if forced_track_focus is not None:
            album_spotlight_focus = forced_track_focus.value
            LOGGER.info(
                "[album_spotlight] Focus mode forced: %s",
                album_spotlight_focus,
            )
        else:
            album_spotlight_focus = select_album_spotlight_focus(
                current_meta=current_meta,
                next_meta=next_meta,
                rng=rng,
            )
            LOGGER.info(
                "[album_spotlight] Focus mode selected: %s",
                album_spotlight_focus,
            )
        return ArchetypePromptVariants(album_spotlight_focus=album_spotlight_focus)

    if archetype == Archetype.ERA_SNAPSHOT:
        if forced_track_focus is not None:
            era_snapshot_focus = forced_track_focus.value
            LOGGER.info("[era_snapshot] Focus mode forced: %s", era_snapshot_focus)
        else:
            era_snapshot_focus = "current" if rng.random() < 0.5 else "next"
            LOGGER.info("[era_snapshot] Focus mode selected: %s", era_snapshot_focus)
        era_snapshot_lane = select_era_snapshot_lane(rng)
        LOGGER.info("[era_snapshot] Story lane selected: %s", era_snapshot_lane)
        return ArchetypePromptVariants(
            era_snapshot_lane=era_snapshot_lane,
            era_snapshot_focus=era_snapshot_focus,
        )

    if archetype == Archetype.DEEP_DIVE:
        if forced_track_focus is not None:
            deep_dive_focus = forced_track_focus.value
            LOGGER.info("[deep_dive] Focus mode forced: %s", deep_dive_focus)
        else:
            deep_dive_focus = "current" if rng.random() < 0.5 else "next"
            LOGGER.info("[deep_dive] Focus mode selected: %s", deep_dive_focus)
        deep_dive_lane = rng.choice(
            [
                "historia de la banda",
                "era y contexto",
                "historia de album",
                "genealogia de cancion",
                "mitologia en vivo",
            ]
        )
        LOGGER.info("[deep_dive] Story lane selected: %s", deep_dive_lane)
        return ArchetypePromptVariants(
            deep_dive_lane=deep_dive_lane,
            deep_dive_focus=deep_dive_focus,
        )

    return ArchetypePromptVariants()


def _build_prompt_kwargs(
    station_name: str,
    personality: StationPersonality,
    current_track: QueueTrack,
    next_track: QueueTrack,
    upcoming_tracks: Sequence[QueueTrack],
    current_meta: TrackMetadata,
    next_meta: TrackMetadata,
    angle: Optional[str],
    hook: str,
    banned_list: Sequence[str],
    schedule_context: Optional[ScheduleContext],
    state: OrchestratorState,
    variants: ArchetypePromptVariants,
    locale: Optional[HostLocale] = None,
    archetype_policy: Optional[ResolvedArchetypeProfile] = None,
) -> Dict[str, Any]:
    return {
        "station_name": station_name,
        "personality": personality,
        "current": current_track,
        "next_track": next_track,
        "upcoming_tracks": upcoming_tracks,
        "current_meta": current_meta,
        "next_meta": next_meta,
        "angle": angle,
        "hook": hook,
        "banned_list": banned_list,
        "recent_scripts": state.recent_scripts,
        "schedule_context": schedule_context,
        "short_story_focus": variants.short_story_focus,
        "album_spotlight_focus": variants.album_spotlight_focus,
        "era_snapshot_lane": variants.era_snapshot_lane,
        "era_snapshot_focus": variants.era_snapshot_focus,
        "deep_dive_lane": variants.deep_dive_lane,
        "deep_dive_focus": variants.deep_dive_focus,
        "locale": _resolved_locale(locale),
        "archetype_policy": archetype_policy,
    }


def _generate_standard_archetype_script(
    *,
    archetype: Archetype,
    prompt_kwargs: Mapping[str, Any],
    angle: Optional[str],
    schedule_context: Optional[ScheduleContext],
    rng: random.Random,
    allow_ultra_minimal_fallback: bool,
    generate_with_retries,
    fallback,
    terminal_ultra_minimal_fallback,
) -> Tuple[str, GeneratedSegmentMetadata, Archetype]:
    prompt = build_prompt(archetype=archetype, **prompt_kwargs)
    generated = generate_with_retries(
        prompt=prompt,
        label=f"Gemini generation ({archetype.value})",
        with_search=should_enable_search(
            archetype, angle, prompt_kwargs.get("archetype_policy")
        ),
    )
    if generated.strip() == "NO_SCRIPT":
        LOGGER.info(
            "[%s] Gemini returned NO_SCRIPT; falling back to ultra_minimal.",
            archetype.value,
        )
        if archetype == Archetype.ULTRA_MINIMAL or not allow_ultra_minimal_fallback:
            return terminal_ultra_minimal_fallback()
        return fallback()

    cleaned = _postprocess_schedule_script(
        script_text=generated,
        archetype=archetype,
        schedule_context=schedule_context,
        rng=rng,
        locale=prompt_kwargs.get("locale"),
    )
    if not cleaned.strip() or cleaned.strip() == "NO_SCRIPT":
        LOGGER.info(
            "[%s] Empty/invalid script after cleanup; falling back to ultra_minimal.",
            archetype.value,
        )
        if archetype == Archetype.ULTRA_MINIMAL or not allow_ultra_minimal_fallback:
            return terminal_ultra_minimal_fallback()
        return fallback()

    return cleaned, _segment_metadata_for_archetype(archetype, prompt_kwargs), archetype


def generate_archetype_script(
    archetype: Archetype,
    station_name: str,
    personality: StationPersonality,
    current_track: QueueTrack,
    next_track: QueueTrack,
    upcoming_tracks: Sequence[QueueTrack],
    current_meta: TrackMetadata,
    next_meta: TrackMetadata,
    angle: Optional[str],
    hook: str,
    banned_list: Sequence[str],
    schedule_context: Optional[ScheduleContext],
    state: OrchestratorState,
    rng: random.Random,
    forced_mode: bool,
    forced_track_focus: Optional[TrackFocus] = None,
    allow_ultra_minimal_fallback: bool = True,
    locale: Optional[HostLocale] = None,
    archetype_policy: Optional[ResolvedArchetypeProfile] = None,
) -> Tuple[str, GeneratedSegmentMetadata, Archetype]:
    """Generate script and structured presentation metadata.

    Returns: (script, segment_metadata, archetype_used).

    ``segment_metadata`` carries validated news/concert facts and the selected
    current/next track focus used by the listener-facing title builder.
    """

    locale = _resolved_locale(locale)
    temperature, top_p = sample_generation_settings(
        archetype, rng, archetype_policy
    )
    system_prompt = build_system_prompt(station_name, personality, locale=locale)
    variants = _resolve_prompt_variants(
        archetype=archetype,
        forced_track_focus=forced_track_focus,
        current_meta=current_meta,
        next_meta=next_meta,
        rng=rng,
    )
    prompt_kwargs = _build_prompt_kwargs(
        station_name=station_name,
        personality=personality,
        current_track=current_track,
        next_track=next_track,
        upcoming_tracks=upcoming_tracks,
        current_meta=current_meta,
        next_meta=next_meta,
        angle=angle,
        hook=hook,
        banned_list=banned_list,
        schedule_context=schedule_context,
        state=state,
        variants=variants,
        locale=locale,
        archetype_policy=archetype_policy,
    )

    def generate_with_retries(
        prompt: str,
        label: str,
        with_search: bool,
        temperature_override: Optional[float] = None,
        top_p_override: Optional[float] = None,
    ) -> str:
        call_temperature = (
            temperature if temperature_override is None else temperature_override
        )
        call_top_p = top_p if top_p_override is None else top_p_override
        return run_with_retries(
            label=label,
            func=lambda: gemini_generate_text(
                prompt=prompt,
                system_prompt=system_prompt,
                temperature=call_temperature,
                top_p=call_top_p,
                with_search=with_search,
            ),
        )

    def fallback() -> Tuple[str, GeneratedSegmentMetadata, Archetype]:
        return fallback_to_ultra_minimal(
            station_name=station_name,
            personality=personality,
            current_track=current_track,
            next_track=next_track,
            upcoming_tracks=upcoming_tracks,
            current_meta=current_meta,
            next_meta=next_meta,
            banned_list=banned_list,
            schedule_context=schedule_context,
            state=state,
            rng=rng,
            locale=locale,
            archetype_policy=archetype_policy,
        )

    def terminal_ultra_minimal_fallback() -> Tuple[str, GeneratedSegmentMetadata, Archetype]:
        LOGGER.warning(
            "[ultra_minimal] Gemini did not produce a usable script; using deterministic local fallback."
        )
        return (
            build_local_ultra_minimal_script(
                current_track=current_track,
                next_track=next_track,
                schedule_context=schedule_context,
                rng=rng,
                locale=locale,
            ),
            GeneratedSegmentMetadata(),
            Archetype.ULTRA_MINIMAL,
        )

    if archetype not in {Archetype.NEWS, Archetype.CONCERT_CHECK}:
        return _generate_standard_archetype_script(
            archetype=archetype,
            prompt_kwargs=prompt_kwargs,
            angle=angle,
            schedule_context=schedule_context,
            rng=rng,
            allow_ultra_minimal_fallback=allow_ultra_minimal_fallback,
            generate_with_retries=generate_with_retries,
            fallback=fallback,
            terminal_ultra_minimal_fallback=terminal_ultra_minimal_fallback,
        )

    if archetype == Archetype.CONCERT_CHECK:
        return _generate_concert_check_script(
            station_name=station_name,
            personality=personality,
            current_track=current_track,
            next_track=next_track,
            schedule_context=schedule_context,
            prompt_kwargs=prompt_kwargs,
            temperature=temperature,
            top_p=top_p,
            rng=rng,
            generate_with_retries=generate_with_retries,
            fallback=fallback,
        )

    return _generate_news_script(
        station_name=station_name,
        personality=personality,
        schedule_context=schedule_context,
        state=state,
        prompt_kwargs=prompt_kwargs,
        temperature=temperature,
        top_p=top_p,
        forced_mode=forced_mode,
        rng=rng,
        generate_with_retries=generate_with_retries,
        fallback=fallback,
    )


__all__ = [
    "artist_matches_targets",
    "build_local_ultra_minimal_script",
    "build_prompt",
    "build_system_prompt",
    "build_tts_instructions",
    "cleanup_generated_script",
    "ensure_mid_block_reference",
    "ensure_schedule_genre_reference",
    "fallback_to_ultra_minimal",
    "format_shared_input",
    "gemini_generate_text",
    "generate_archetype_script",
    "normalize_concert_country",
    "parse_concert_event_date",
    "parse_concert_output",
    "parse_news_output",
    "parse_structured_script_and_meta",
    "parse_timestamp",
    "resolve_station_personality",
    "select_album_spotlight_focus",
    "select_era_snapshot_lane",
    "should_enable_search",
    "station_name_for_generation",
    "validate_concert_segment",
    "validate_news_freshness_and_dedup",
]
