"""Cleanup and schedule-aware post-processing for generated host scripts."""

from __future__ import annotations

import random
import re
import unicodedata
from typing import List, Optional

from .channels import HostLocale, get_channel_registry
from .config import LOGGER
from .models import Archetype, ScheduleContext


def _default_locale() -> HostLocale:
    return get_channel_registry().locales["es-AR"]


def _resolved_locale(locale: Optional[HostLocale]) -> HostLocale:
    return locale or _default_locale()


def cleanup_generated_script(raw: str) -> str:
    text = raw.strip()
    text = re.sub(r"\[([^\]]+)\]\(\s*https?://[^\)]+\)", r"\1", text)
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"\[\s*\d+\s*\]", "", text)
    text = re.sub(r"\s{2,}", " ", text)
    text = text.replace("```", "")
    return text.strip()


def _normalize_text_for_contains(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower()
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _locale_string_list(locale: HostLocale, key: str) -> List[str]:
    values = locale.schedule.get(key)
    if not isinstance(values, list):
        return []
    return [str(value).strip() for value in values if str(value).strip()]


def _script_has_block_reference(
    script_text: str,
    schedule_context: ScheduleContext,
    locale: Optional[HostLocale] = None,
) -> bool:
    locale = _resolved_locale(locale)
    script_norm = _normalize_text_for_contains(script_text)
    if not script_norm:
        return False

    section_norm = _normalize_text_for_contains(schedule_context.section_label)
    if section_norm and section_norm in script_norm:
        return True

    playlist_norm = _normalize_text_for_contains(schedule_context.playlist_name or "")
    if playlist_norm and playlist_norm in script_norm:
        return True

    if any(
        _normalize_text_for_contains(term) in script_norm
        for term in _locale_string_list(locale, "block_terms")
    ):
        for genre in schedule_context.genre_labels:
            genre_norm = _normalize_text_for_contains(genre)
            if genre_norm and genre_norm in script_norm:
                return True

    # Fallback: phrases that usually indicate block orientation.
    if any(
        _normalize_text_for_contains(marker) in script_norm
        for marker in _locale_string_list(locale, "current_markers")
    ):
        return True

    return False


def _script_has_genre_reference(
    script_text: str,
    schedule_context: ScheduleContext,
    locale: Optional[HostLocale] = None,
) -> bool:
    locale = _resolved_locale(locale)
    script_norm = _normalize_text_for_contains(script_text)
    if not script_norm:
        return False

    if schedule_context.mode == "open":
        for marker in _locale_string_list(locale, "open_markers"):
            if marker in script_norm:
                return True

    for genre in schedule_context.genre_labels:
        genre_norm = _normalize_text_for_contains(genre)
        if genre_norm and genre_norm in script_norm:
            return True
    return False


def _spoken_section_label(
    schedule_context: ScheduleContext,
    locale: Optional[HostLocale] = None,
) -> str:
    locale = _resolved_locale(locale)
    if schedule_context.mode == "open":
        return str(locale.schedule.get("open_label") or "open rotation")

    section = schedule_context.section_label.strip()
    return section or str(locale.schedule.get("open_label") or "this block")


def _formatted_schedule_options(
    locale: HostLocale,
    key: str,
    *,
    section: str = "",
    genres: str = "",
) -> List[str]:
    return [
        template.format(section=section, genres=genres)
        for template in _locale_string_list(locale, key)
    ]


def _build_mid_block_clause(
    schedule_context: ScheduleContext,
    archetype: Archetype,
    rng: random.Random,
    locale: Optional[HostLocale] = None,
) -> str:
    locale = _resolved_locale(locale)
    section = _spoken_section_label(schedule_context, locale)
    genres = ", ".join([item for item in schedule_context.genre_labels if item][:2]).strip()

    if schedule_context.mode == "open":
        key = "mid_open_short" if archetype == Archetype.ULTRA_MINIMAL else "mid_open_long"
        options = _formatted_schedule_options(locale, key)
        return rng.choice(options)

    if archetype == Archetype.ULTRA_MINIMAL:
        options = _formatted_schedule_options(
            locale, "mid_section_short", section=section, genres=genres
        )
        return rng.choice(options)

    if genres:
        options = _formatted_schedule_options(
            locale, "mid_section_long", section=section, genres=genres
        )
    else:
        options = _formatted_schedule_options(
            locale, "mid_section_short", section=section, genres=genres
        )
    return rng.choice(options)


def ensure_mid_block_reference(
    script_text: str,
    archetype: Archetype,
    schedule_context: Optional[ScheduleContext],
    rng: random.Random,
    locale: Optional[HostLocale] = None,
) -> str:
    locale = _resolved_locale(locale)
    if schedule_context is None:
        return script_text

    if schedule_context.mention_intent != "mid":
        return script_text

    if archetype not in {
        Archetype.BACK_SELL,
        Archetype.UP_NEXT_TEASE,
        Archetype.SHORT_STORY,
        Archetype.ALBUM_SPOTLIGHT,
        Archetype.ERA_SNAPSHOT,
        Archetype.DEEP_DIVE,
        Archetype.ULTRA_MINIMAL,
    }:
        return script_text

    if _script_has_block_reference(script_text, schedule_context, locale):
        LOGGER.info(
            "[schedule] Mid-block mention already present in generated %s script for '%s'.",
            archetype.value,
            schedule_context.section_label,
        )
        return script_text

    clause = _build_mid_block_clause(schedule_context, archetype, rng, locale)
    text = script_text.strip()
    if not text:
        return text

    if archetype == Archetype.ULTRA_MINIMAL:
        # Keep it one sentence by inserting a short leading clause.
        stitched = f"{clause}, {text[0].lower() + text[1:]}" if len(text) > 1 else f"{clause}, {text.lower()}"
    else:
        stitched = f"{clause}... {text}"

    stitched = re.sub(r"\s{2,}", " ", stitched).strip()
    LOGGER.info(
        "[schedule] Auto-injected mid-block mention into %s script for '%s'.",
        archetype.value,
        schedule_context.section_label,
    )
    return stitched


def _build_schedule_genre_clause(
    schedule_context: ScheduleContext,
    archetype: Archetype,
    rng: random.Random,
    locale: Optional[HostLocale] = None,
) -> str:
    locale = _resolved_locale(locale)
    section = _spoken_section_label(schedule_context, locale)
    genres = [str(item).strip() for item in schedule_context.genre_labels if str(item).strip()]
    genres_text = ", ".join(genres[:2]).strip()
    if not genres_text and schedule_context.mode != "open":
        return ""

    mention_intent = schedule_context.mention_intent or "none"
    if schedule_context.mode == "open":
        if mention_intent == "start":
            key = "start_open_short" if archetype == Archetype.ULTRA_MINIMAL else "start_open_long"
            options = _formatted_schedule_options(locale, key)
        elif mention_intent == "mid":
            key = "mid_open_short" if archetype == Archetype.ULTRA_MINIMAL else "mid_open_long"
            options = _formatted_schedule_options(locale, key)
        else:
            options = [str(locale.schedule.get("open_label") or "open rotation")]
    elif mention_intent == "start":
        key = "start_section_short" if archetype == Archetype.ULTRA_MINIMAL else "start_section_long"
        options = _formatted_schedule_options(
            locale, key, section=section, genres=genres_text
        )
    elif mention_intent == "mid":
        key = "mid_section_short" if archetype == Archetype.ULTRA_MINIMAL else "mid_section_long"
        options = _formatted_schedule_options(
            locale, key, section=section, genres=genres_text
        )
    else:
        options = _formatted_schedule_options(
            locale, "genre_default", section=section, genres=genres_text
        )

    return rng.choice(options)


def ensure_schedule_genre_reference(
    script_text: str,
    archetype: Archetype,
    schedule_context: Optional[ScheduleContext],
    rng: random.Random,
    locale: Optional[HostLocale] = None,
) -> str:
    locale = _resolved_locale(locale)
    if schedule_context is None:
        return script_text

    if schedule_context.mention_intent not in {"start", "mid"}:
        return script_text

    if _script_has_genre_reference(script_text, schedule_context, locale):
        return script_text

    clause = _build_schedule_genre_clause(schedule_context, archetype, rng, locale)
    if not clause:
        return script_text

    text = (script_text or "").strip()
    if not text:
        return clause

    if archetype == Archetype.ULTRA_MINIMAL:
        stitched = (
            f"{clause}, {text[0].lower() + text[1:]}" if len(text) > 1 else f"{clause}, {text.lower()}"
        )
    else:
        stitched = f"{clause}... {text}"

    stitched = re.sub(r"\s{2,}", " ", stitched).strip()
    LOGGER.info(
        "[schedule] Auto-injected genre reference into %s script for '%s'.",
        archetype.value,
        schedule_context.section_label,
    )
    return stitched


def _postprocess_schedule_script(
    script_text: str,
    archetype: Archetype,
    schedule_context: Optional[ScheduleContext],
    rng: random.Random,
    locale: Optional[HostLocale] = None,
) -> str:
    locale = _resolved_locale(locale)
    cleaned = cleanup_generated_script(script_text)
    cleaned = ensure_mid_block_reference(
        script_text=cleaned,
        archetype=archetype,
        schedule_context=schedule_context,
        rng=rng,
        locale=locale,
    )
    cleaned = ensure_schedule_genre_reference(
        script_text=cleaned,
        archetype=archetype,
        schedule_context=schedule_context,
        rng=rng,
        locale=locale,
    )
    return cleaned


__all__ = [
    "cleanup_generated_script",
    "ensure_mid_block_reference",
    "ensure_schedule_genre_reference",
]
