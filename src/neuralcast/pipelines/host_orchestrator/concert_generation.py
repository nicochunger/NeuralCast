"""Concert parsing, validation, repair, and generation workflow."""

from __future__ import annotations

import datetime as dt
import random
import re
import unicodedata
from typing import Any, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import urlparse

from .archetype_policies import (
    ResolvedArchetypeProfile,
    get_archetype_policy_registry,
)
from .channels import HostLocale, get_channel_registry
from .config import (
    CONCERT_OUTPUT_RE,
    LOGGER,
    SYSTEM_TZ,
    get_prompt_template_from,
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
        country_code = str(entry.get("country_code") or "").strip().upper() or None
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
                country_code=country_code,
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
    archetype_policy: Optional[ResolvedArchetypeProfile] = None,
) -> str:
    locale = _resolved_locale(locale)
    profile = archetype_policy or get_archetype_policy_registry().profiles["base"]
    concert_policy = profile.for_archetype(Archetype.CONCERT_CHECK).concert_check
    if concert_policy is None:
        raise ValueError("The concert_check archetype requires a concert policy.")
    country_codes = concert_policy.country_codes
    repair_prompt = get_prompt_template_from(
        locale.prompt_directory,
        "repair_concert_contract",
        original_output=original_output,
        concert_countries=", ".join(
            f"{profile.concert_country_label(code, locale.tag)} ({code})"
            for code in country_codes
        ),
        concert_country_codes="|".join(country_codes),
    ).replace("es-AR", locale.tag)
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


def normalize_concert_country(
    value: str,
    archetype_policy: Optional[ResolvedArchetypeProfile] = None,
) -> Optional[str]:
    profile = archetype_policy or get_archetype_policy_registry().profiles["base"]
    normalized = normalize_ascii_for_match(value)
    for code, country in profile.concert_countries.items():
        if normalized == normalize_ascii_for_match(code):
            return code
        if any(
            normalized == normalize_ascii_for_match(alias)
            for alias in country.aliases
        ):
            return code
        if normalized == normalize_ascii_for_match(country.label_for("en")):
            return code
    return None


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
    *,
    allowed_country_codes: Optional[Sequence[str]] = None,
    archetype_policy: Optional[ResolvedArchetypeProfile] = None,
) -> Tuple[bool, str]:
    profile = archetype_policy or get_archetype_policy_registry().profiles["base"]
    if allowed_country_codes is None:
        concert_policy = profile.for_archetype(Archetype.CONCERT_CHECK).concert_check
        assert concert_policy is not None
        allowed_country_codes = concert_policy.country_codes
    allowed_codes = {code.upper() for code in allowed_country_codes}
    target_artists = (current_track.artist, next_track.artist)
    today_local = dt.datetime.now(SYSTEM_TZ).date()

    for event in segment.events:
        if not artist_matches_targets(event.artist, target_artists):
            return (
                False,
                f"event artist is not current/next track artist: {event.artist}",
            )

        normalized_country = normalize_concert_country(event.country, profile)
        metadata_country_code = (event.country_code or "").upper() or None
        if metadata_country_code and metadata_country_code != normalized_country:
            return False, (
                "event country and country_code disagree: "
                f"{event.country}/{event.country_code}"
            )
        country_code = metadata_country_code or normalized_country
        if country_code not in allowed_codes:
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
    profile: ResolvedArchetypeProfile = prompt_kwargs.get(
        "archetype_policy"
    ) or get_archetype_policy_registry().profiles["base"]
    concert_policy = profile.for_archetype(Archetype.CONCERT_CHECK).concert_check
    search_enabled = profile.for_archetype(
        Archetype.CONCERT_CHECK
    ).search_enabled
    if concert_policy is None:
        raise ValueError("The concert_check archetype requires a concert policy.")
    generation_attempts = 2
    for generation_attempt in range(generation_attempts):
        prompt = build_prompt(archetype=Archetype.CONCERT_CHECK, **prompt_kwargs)
        generated = generate_with_retries(
            prompt=prompt,
            label="Gemini generation (concert_check)",
            with_search=search_enabled,
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
                    archetype_policy=profile,
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
            allowed_country_codes=concert_policy.country_codes,
            archetype_policy=profile,
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
