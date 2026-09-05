"""News parsing, validation, repair, and generation workflow."""

from __future__ import annotations

import datetime as dt
import random
from typing import Any, List, Mapping, Optional, Tuple

from .channels import HostLocale, get_channel_registry
from .config import (
    LOGGER,
    NEWS_MAX_AGE_HOURS,
    NEWS_OUTPUT_RE,
    NEWS_TOPICS,
    get_prompt_template_from,
)
from .models import (
    Archetype,
    GeneratedSegmentMetadata,
    NewsSegment,
    NewsStoryMeta,
    OrchestratorState,
    ScheduleContext,
    StationPersonality,
)
from .prompts import build_prompt, build_system_prompt
from .script_processing import _postprocess_schedule_script
from .state import build_news_dedup_key, prune_news_history
from .structured_output import parse_structured_script_and_meta, parse_timestamp
from .text_generation import gemini_generate_text
from .utils import now_ts, run_with_retries


def _default_locale() -> HostLocale:
    return get_channel_registry().locales["es-AR"]


def _resolved_locale(locale: Optional[HostLocale]) -> HostLocale:
    return locale or _default_locale()


def parse_news_output(
    raw: str, expected_locale: str = "es-AR"
) -> Tuple[Optional[NewsSegment], str]:
    script, meta, reason = parse_structured_script_and_meta(raw, NEWS_OUTPUT_RE)
    if reason != "ok":
        return None, reason
    assert script is not None
    assert meta is not None

    story_count = meta.get("story_count")
    language = str(meta.get("language") or "").strip()
    stories = meta.get("stories")

    if story_count not in (1, 2):
        return None, "story_count must be 1 or 2"
    if language.casefold() != expected_locale.casefold():
        return None, f"language must be {expected_locale}"
    if not isinstance(stories, list) or len(stories) != story_count:
        return None, "stories must match story_count"

    parsed_stories: List[NewsStoryMeta] = []
    for entry in stories:
        if not isinstance(entry, Mapping):
            return None, "story entry must be object"
        topic = str(entry.get("topic") or "").strip()
        headline = str(entry.get("headline") or "").strip()
        source_url = str(entry.get("source_url") or "").strip()
        published_at = str(entry.get("published_at") or "").strip() or None

        if not topic or not headline or not source_url:
            return None, "stories require topic/headline/source_url"
        parsed_stories.append(
            NewsStoryMeta(
                topic=topic,
                headline=headline,
                source_url=source_url,
                published_at=published_at,
            )
        )

    return NewsSegment(
        script=script, story_count=int(story_count), stories=parsed_stories
    ), "ok"


def attempt_news_repair(
    original_output: str,
    temperature: float,
    top_p: float,
    station_name: str,
    personality: StationPersonality,
    locale: Optional[HostLocale] = None,
) -> str:
    locale = _resolved_locale(locale)
    repair_prompt = get_prompt_template_from(
        locale.prompt_directory,
        "repair_news_contract",
        original_output=original_output,
    ).replace("es-AR", locale.tag)
    return gemini_generate_text(
        prompt=repair_prompt,
        system_prompt=build_system_prompt(station_name, personality, locale),
        temperature=temperature,
        top_p=top_p,
        with_search=False,
    )


def validate_news_freshness_and_dedup(
    segment: NewsSegment,
    state: OrchestratorState,
    ts: float,
) -> Tuple[bool, str]:
    recent = prune_news_history(state.recent_news_dedup, ts)
    recent_keys = {str(entry.get("key") or "") for entry in recent}
    now_utc = dt.datetime.fromtimestamp(ts, tz=dt.timezone.utc)

    for story in segment.stories:
        dedup_key = build_news_dedup_key(story.topic, story.headline, story.source_url)
        if dedup_key in recent_keys:
            return False, f"duplicate headline detected: {story.headline}"

        published = parse_timestamp(story.published_at)
        if published is None:
            return False, f"missing/invalid published_at for headline: {story.headline}"

        age_hours = (now_utc - published).total_seconds() / 3600.0
        if age_hours > NEWS_MAX_AGE_HOURS:
            return False, (
                f"headline too old ({age_hours:.1f}h > {NEWS_MAX_AGE_HOURS}h): "
                f"{story.headline}"
            )

    return True, "ok"


def pick_news_topics(story_count: int, rng: random.Random) -> List[str]:
    topics = list(NEWS_TOPICS)
    if story_count <= 1:
        return [rng.choice(topics)]
    if len(topics) < story_count:
        return [rng.choice(topics) for _ in range(story_count)]
    return rng.sample(topics, k=story_count)


def _generate_news_script(
    *,
    station_name: str,
    personality: StationPersonality,
    schedule_context: Optional[ScheduleContext],
    state: OrchestratorState,
    prompt_kwargs: Mapping[str, Any],
    temperature: float,
    top_p: float,
    forced_mode: bool,
    rng: random.Random,
    generate_with_retries,
    fallback,
) -> Tuple[str, GeneratedSegmentMetadata, Archetype]:
    locale = _resolved_locale(prompt_kwargs.get("locale"))
    story_count = rng.randint(1, 2)
    topic_attempts = 3
    conservative_news_temperature = min(0.65, temperature)
    conservative_news_top_p = max(0.90, top_p)
    LOGGER.info(
        "[news] Starting news generation | story_count=%s | topic_attempts=%s | sampled_temp=%.3f | sampled_top_p=%.3f",
        story_count,
        topic_attempts,
        temperature,
        top_p,
    )
    for topic_attempt in range(topic_attempts):
        topics = pick_news_topics(story_count, rng)
        LOGGER.info(
            "[news] Topic roll %s/%s | topics=%s",
            topic_attempt + 1,
            topic_attempts,
            topics,
        )
        prompt = build_prompt(
            archetype=Archetype.NEWS,
            story_count=story_count,
            news_topics=topics,
            **prompt_kwargs,
        )
        generated = generate_with_retries(
            prompt=prompt,
            label="Gemini generation (news)",
            with_search=True,
        )

        segment, reason = parse_news_output(generated, locale.tag)
        if reason == "NO_SCRIPT":
            LOGGER.warning(
                "[news] Gemini returned NO_SCRIPT (%s/%s) at sampled settings temp=%.3f top_p=%.3f topics=%s. Raw=%r",
                topic_attempt + 1,
                topic_attempts,
                temperature,
                top_p,
                topics,
                generated[:500],
            )
            if temperature > conservative_news_temperature or top_p < conservative_news_top_p:
                LOGGER.info(
                    "[news] Retrying same topic with conservative settings temp=%.2f top_p=%.2f.",
                    conservative_news_temperature,
                    conservative_news_top_p,
                )
                generated = generate_with_retries(
                    prompt=prompt,
                    label="Gemini generation (news, conservative retry)",
                    with_search=True,
                    temperature_override=conservative_news_temperature,
                    top_p_override=conservative_news_top_p,
                )
                segment, reason = parse_news_output(generated, locale.tag)

        if reason == "NO_SCRIPT":
            if topic_attempt < topic_attempts - 1:
                LOGGER.warning(
                    "[news] NO_SCRIPT after retries (%s/%s); trying a different topic.",
                    topic_attempt + 1,
                    topic_attempts,
                )
                continue
            if forced_mode:
                raise RuntimeError(
                    "Forced news archetype returned NO_SCRIPT after topic retries; failing as requested for test visibility."
                )
            LOGGER.warning(
                "[news] Gemini returned NO_SCRIPT after topic retries; falling back to ultra_minimal."
            )
            return fallback()

        if segment is None:
            LOGGER.warning(
                "[news] Parse failed (%s); attempting one repair pass.",
                reason,
            )
            repaired = run_with_retries(
                label="News format repair",
                func=lambda: attempt_news_repair(
                    generated,
                    temperature=temperature,
                    top_p=top_p,
                    station_name=station_name,
                    personality=personality,
                    locale=locale,
                ),
            )
            segment, reason = parse_news_output(repaired, locale.tag)
            if segment is None:
                if forced_mode:
                    raise RuntimeError(
                        f"Forced news archetype failed output contract after repair: {reason}"
                    )
                LOGGER.warning(
                    "[news] Output remained invalid after repair; falling back to ultra_minimal."
                )
                return fallback()

        ok, freshness_reason = validate_news_freshness_and_dedup(
            segment, state, now_ts()
        )
        if ok:
            LOGGER.info(
                "[news] Accepted news segment | topics=%s | stories=%s",
                topics,
                segment.story_count,
            )
            return (
                _postprocess_schedule_script(
                    script_text=segment.script,
                    archetype=Archetype.NEWS,
                    schedule_context=schedule_context,
                    rng=rng,
                    locale=locale,
                ),
                GeneratedSegmentMetadata(news_segment=segment),
                Archetype.NEWS,
            )

        LOGGER.warning(
            "[news] Freshness/dedup failed (%s/%s): %s",
            topic_attempt + 1,
            topic_attempts,
            freshness_reason,
        )
        if topic_attempt < topic_attempts - 1:
            continue
        if forced_mode:
            raise RuntimeError(
                "Forced news archetype failed freshness/dedup requirements after topic retries."
            )

    LOGGER.warning("[news] Exhausted topic retries; falling back to ultra_minimal.")
    return fallback()


__all__ = [
    "parse_news_output",
    "pick_news_topics",
    "validate_news_freshness_and_dedup",
]
