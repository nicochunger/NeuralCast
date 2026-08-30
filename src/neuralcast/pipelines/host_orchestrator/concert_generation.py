"""Concert parsing, validation, repair, and generation workflow."""

from __future__ import annotations

import datetime as dt
import random
import re
import unicodedata
from typing import Any, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import urlparse

from .channels import HostLocale, get_channel_registry
from .config import (
    CONCERT_COUNTRY_ALIASES,
    CONCERT_OUTPUT_RE,
    CONCERT_TARGET_COUNTRY_KEYS,
    LOGGER,
    REPAIR_CONCERT_CONTRACT,
    SYSTEM_TZ,
)
from .models import (
    Archetype,
    ConcertEventMeta,
    ConcertSegment,
    GeneratedSegmentMetadata,
    QueueTrack,
    ScheduleContext,
    StationPersonality,
)
from .prompts import build_prompt, build_system_prompt
from .script_processing import _postprocess_schedule_script
from .structured_output import parse_structured_script_and_meta, parse_timestamp
from .text_generation import gemini_generate_text
from .utils import run_with_retries


def _default_locale() -> HostLocale:
    return get_channel_registry().locales["es-AR"]


def _resolved_locale(locale: Optional[HostLocale]) -> HostLocale:
    return locale or _default_locale()


def parse_concert_output(
    raw: str, expected_locale: str = "es-AR"
) -> Tuple[Optional[ConcertSegment], str]:
    script, meta, reason = parse_structured_script_and_meta(raw, CONCERT_OUTPUT_RE)
    if reason != "ok":
        return None, reason
    assert script is not None
    assert meta is not None

    language = str(meta.get("language") or "").strip()
    events = meta.get("events")
    if language.casefold() != expected_locale.casefold():
        return None, f"language must be {expected_locale}"
    if not isinstance(events, list) or not events:
        return None, "events must be a non-empty list"
    if len(events) > 3:
        return None, "events must include at most 3 entries"

    parsed_events: List[ConcertEventMeta] = []
    for entry in events:
        if not isinstance(entry, Mapping):
            return None, "event entry must be object"
        artist = str(entry.get("artist") or "").strip()
        country = str(entry.get("country") or "").strip()
        city = str(entry.get("city") or "").strip()
        venue = str(entry.get("venue") or "").strip()
        event_date = str(entry.get("event_date") or "").strip()
        source_url = str(entry.get("source_url") or "").strip()
        if not artist or not country or not city or not venue or not event_date or not source_url:
            return (
                None,
                "event entries require artist/country/city/venue/event_date/source_url",
            )
        parsed_events.append(
            ConcertEventMeta(
                artist=artist,
                country=country,
                city=city,
                venue=venue,
                event_date=event_date,
                source_url=source_url,
            )
        )

    return ConcertSegment(script=script, events=parsed_events), "ok"


def attempt_concert_repair(
    original_output: str,
    temperature: float,
    top_p: float,
    station_name: str,
    personality: StationPersonality,
    locale: Optional[HostLocale] = None,
) -> str:
    locale = _resolved_locale(locale)
    repair_prompt = REPAIR_CONCERT_CONTRACT.replace("es-AR", locale.tag).format(
        original_output=original_output
    )
    return gemini_generate_text(
        prompt=repair_prompt,
        system_prompt=build_system_prompt(station_name, personality, locale),
        temperature=temperature,
        top_p=top_p,
        with_search=False,
    )


def normalize_ascii_for_match(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value or "")
    normalized = normalized.encode("ascii", "ignore").decode("ascii")
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized.lower())
    return re.sub(r"\s+", " ", normalized).strip()


def normalize_concert_country(value: str) -> Optional[str]:
    normalized = normalize_ascii_for_match(value)
    return CONCERT_COUNTRY_ALIASES.get(normalized)


def is_valid_http_url(url: str) -> bool:
    parsed = urlparse(url.strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def artist_matches_targets(candidate: str, targets: Sequence[str]) -> bool:
    normalized_candidate = normalize_ascii_for_match(candidate)
    if not normalized_candidate:
        return False
    for target in targets:
        normalized_target = normalize_ascii_for_match(target)
        if not normalized_target:
            continue
        if normalized_candidate == normalized_target:
            return True
        if (
            normalized_candidate in normalized_target
            or normalized_target in normalized_candidate
        ):
            return True
    return False


def parse_concert_event_date(value: str) -> Optional[dt.date]:
    parsed_ts = parse_timestamp(value)
    return parsed_ts.date() if parsed_ts is not None else None


def validate_concert_segment(
    segment: ConcertSegment,
    current_track: QueueTrack,
    next_track: QueueTrack,
) -> Tuple[bool, str]:
    target_artists = (current_track.artist, next_track.artist)
    today_local = dt.datetime.now(SYSTEM_TZ).date()

    for event in segment.events:
        if not artist_matches_targets(event.artist, target_artists):
            return (
                False,
                f"event artist is not current/next track artist: {event.artist}",
            )

        normalized_country = normalize_concert_country(event.country)
        if normalized_country not in CONCERT_TARGET_COUNTRY_KEYS:
            return False, f"event country not allowed: {event.country}"

        event_date = parse_concert_event_date(event.event_date)
        if event_date is None:
            return False, f"invalid event_date: {event.event_date}"
        if event_date < today_local:
            return False, f"event date is in the past: {event.event_date}"

        if not is_valid_http_url(event.source_url):
            return False, f"invalid source_url: {event.source_url}"

    return True, "ok"


def _generate_concert_check_script(
    *,
    station_name: str,
    personality: StationPersonality,
    current_track: QueueTrack,
    next_track: QueueTrack,
    schedule_context: Optional[ScheduleContext],
    prompt_kwargs: Mapping[str, Any],
    temperature: float,
    top_p: float,
    rng: random.Random,
    generate_with_retries,
    fallback,
) -> Tuple[str, GeneratedSegmentMetadata, Archetype]:
    locale = _resolved_locale(prompt_kwargs.get("locale"))
    generation_attempts = 2
    for generation_attempt in range(generation_attempts):
        prompt = build_prompt(archetype=Archetype.CONCERT_CHECK, **prompt_kwargs)
        generated = generate_with_retries(
            prompt=prompt,
            label="Gemini generation (concert_check)",
            with_search=True,
        )

        segment, reason = parse_concert_output(generated, locale.tag)
        if reason == "NO_SCRIPT":
            LOGGER.info(
                "[concert_check] No qualifying concerts found; falling back to ultra_minimal."
            )
            return fallback()

        if segment is None:
            LOGGER.warning(
                "[concert_check] Parse failed (%s); attempting one repair pass.",
                reason,
            )
            repaired = run_with_retries(
                label="Concert format repair",
                func=lambda: attempt_concert_repair(
                    generated,
                    temperature=temperature,
                    top_p=top_p,
                    station_name=station_name,
                    personality=personality,
                    locale=locale,
                ),
            )
            segment, reason = parse_concert_output(repaired, locale.tag)
            if segment is None:
                LOGGER.warning(
                    "[concert_check] Output remained invalid after repair (%s).",
                    reason,
                )
                if generation_attempt < generation_attempts - 1:
                    continue
                LOGGER.warning(
                    "[concert_check] Exhausted retries; falling back to ultra_minimal."
                )
                return fallback()

        assert segment is not None
        ok, validation_reason = validate_concert_segment(
            segment=segment,
            current_track=current_track,
            next_track=next_track,
        )
        if ok:
            return (
                _postprocess_schedule_script(
                    script_text=segment.script,
                    archetype=Archetype.CONCERT_CHECK,
                    schedule_context=schedule_context,
                    rng=rng,
                    locale=locale,
                ),
                GeneratedSegmentMetadata(concert_segment=segment),
                Archetype.CONCERT_CHECK,
            )

        LOGGER.warning(
            "[concert_check] Validation failed (%s/%s): %s",
            generation_attempt + 1,
            generation_attempts,
            validation_reason,
        )
        if generation_attempt < generation_attempts - 1:
            continue

    LOGGER.warning("[concert_check] Exhausted retries; falling back to ultra_minimal.")
    return fallback()


__all__ = [
    "artist_matches_targets",
    "normalize_concert_country",
    "parse_concert_event_date",
    "parse_concert_output",
    "validate_concert_segment",
]
