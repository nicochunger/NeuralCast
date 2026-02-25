"""Prompting, generation, parsing, and validation for host orchestrator."""

from __future__ import annotations

import datetime as dt
import json
import random
import re
import unicodedata
from email.utils import parsedate_to_datetime
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import urlparse

from .config import (
    CONCERT_COUNTRY_ALIASES,
    CONCERT_OUTPUT_RE,
    CONCERT_TARGET_COUNTRIES,
    CONCERT_TARGET_COUNTRY_KEYS,
    HOST_CONSTITUTION_TEMPLATE,
    LOGGER,
    NEWS_MAX_AGE_HOURS,
    NEWS_OUTPUT_RE,
    NEWS_PREFERRED_MAX_AGE_HOURS,
    NEWS_TOPICS,
    REPAIR_CONCERT_CONTRACT,
    REPAIR_NEWS_CONTRACT,
    SCRIPT_STYLE_BASELINE,
    STATION_GENERATION_NAMES,
    STATION_PERSONALITIES,
    SYSTEM_TZ,
    TTS_INSTRUCTIONS_PATH,
    WRAPPER_BACK_SELL,
    WRAPPER_BLOCK_INTRO,
    WRAPPER_CONCERT_CHECK,
    WRAPPER_DEEP_DIVE,
    WRAPPER_NEWS,
    WRAPPER_ULTRA_MINIMAL,
    load_personality_guide,
)
from .models import (
    Archetype,
    ConcertEventMeta,
    ConcertSegment,
    NewsSegment,
    NewsStoryMeta,
    OrchestratorState,
    QueueTrack,
    ScheduleContext,
    StationPersonality,
    TrackMetadata,
)
from .state import (
    build_news_dedup_key,
    choose_hook,
    prune_news_history,
    sample_generation_settings,
)
from .utils import now_ts, run_with_retries
from neuralcast.services.openai_client import get_gemini_client


def format_shared_input(
    station_name: str,
    personality: StationPersonality,
    current: QueueTrack,
    next_track: QueueTrack,
    current_meta: TrackMetadata,
    next_meta: TrackMetadata,
    angle: Optional[str],
    hook: str,
    banned_list: Sequence[str],
    recent_scripts: Sequence[str],
    schedule_context: Optional[ScheduleContext],
    deep_dive_focus: Optional[str] = None,
) -> str:
    now_local = dt.datetime.now(SYSTEM_TZ).strftime("%Y-%m-%d %H:%M:%S %Z")
    hook_text = (hook or "").strip()
    if hook_text:
        hook_line = (
            f"- Hook cue (idea de entrada, no texto literal; opcional): {hook_text}"
        )
    else:
        hook_line = "- Hook cue (idea de entrada, no texto literal; opcional): none (free opener allowed)"

    def _compose_track(label: str, track: QueueTrack, meta: TrackMetadata) -> List[str]:
        line = f"- {label}: {track.artist} — {track.title}"
        year = (meta.year or "").strip()
        genre = (meta.genre or "").strip()
        if year or genre:
            line += f" ({year or 'year n/d'}, {genre or 'genre n/d'})"
        parts = [line]

        optional: List[str] = []
        if meta.bpm:
            optional.append(f"bpm={meta.bpm}")
        if meta.mood_tags:
            optional.append(f"mood_tags={meta.mood_tags}")
        if meta.album:
            optional.append(f"album={meta.album}")
        if meta.notes:
            optional.append(f"notes={meta.notes}")
        if optional:
            parts.append(f"  Optional metadata: {', '.join(optional)}")
        return parts

    lines = [
        "INPUT",
        f"- Station: {station_name}",
        f"- Station personality: {personality.script_profile}",
        f"- Local time (Europe/Zurich): {now_local}",
    ]
    lines.extend(_compose_track("Current track", current, current_meta))
    lines.extend(_compose_track("Next track", next_track, next_meta))
    lines.extend(
        [
            f"- Angle (sub-perspective): {angle or 'none'}",
            hook_line,
            "- Banned topics/phrases list:",
        ]
    )
    if banned_list:
        lines.extend([f"  - {item}" for item in banned_list])
    else:
        lines.append("  - none")
    if recent_scripts:
        lines.extend(
            [
                "- Recent generated host scripts (most recent first):",
                "  Use this as anti-repetition context: avoid obvious reuse of opening phrases or repeated chunks, but keep the phrasing natural.",
            ]
        )
        lines.extend(
            [
                f"  - Script {index}: {previous_script}"
                for index, previous_script in enumerate(recent_scripts, start=1)
            ]
        )
    else:
        lines.append("- Recent generated host scripts (most recent first): none")

    if schedule_context is not None:
        lines.extend(
            [
                "- Active programming block:",
                "  - Timing note: this block context is for when this host break will air (immediately before Next track).",
                f"  - Section: {schedule_context.section_label}",
                f"  - Genres: {', '.join(schedule_context.genre_labels)}",
                f"  - Phase: {schedule_context.phase} ({int(schedule_context.progress_ratio * 100)}%)",
                f"  - Next section: {schedule_context.next_section_label or 'n/d'}",
            ]
        )
        if schedule_context.mode == "open":
            lines.append(
                "  - Block mode: open weighted rotation (AzuraCast chooses by playlist weights)."
            )
        elif schedule_context.playlist_name:
            lines.append(f"  - Block mode: fixed playlist ({schedule_context.playlist_name}).")

        if schedule_context.mention_intent == "start":
            lines.append(
                "- Schedule mention guidance: this script airs at the block start boundary; present the section as starting now (right before its first song)."
            )
        elif schedule_context.mention_intent == "mid":
            lines.append(
                "- Schedule mention guidance: include one short, natural clause saying we are in this section/block right now."
            )
            lines.append(
                "- Schedule mention style: weave the block mention into the chosen archetype flow (not as a separate announcement); these callouts happen only occasionally (about every 2-3 host breaks), so include it this time."
            )
        else:
            lines.append(
                "- Schedule mention guidance: optional; avoid repeating section callouts."
            )

    if deep_dive_focus in {"current", "next"}:
        focus_label = (
            "current (tema que acaba de sonar)"
            if deep_dive_focus == "current"
            else "next (tema que va a sonar ahora)"
        )
        lines.extend(
            [
                f"- Deep-dive focus mode (obligatorio si el arquetipo es deep_dive): {focus_label}",
                "- Deep-dive secuencia oral obligatoria (seguir exactamente este orden narrativo):",
            ]
        )
        if deep_dive_focus == "current":
            lines.extend(
                [
                    "  - 1) Decir de forma natural que el tema actual (Current track) acaba de sonar.",
                    "  - 2) Contar la profundizacion/historia sobre el tema actual (Current track).",
                    "  - 3) Cerrar presentando el proximo tema (Next track).",
                ]
            )
        else:
            lines.extend(
                [
                    "  - 1) Decir de forma natural que el tema actual (Current track) acaba de sonar.",
                    "  - 2) Decir cual es el proximo tema (Next track).",
                    "  - 3) Contar la profundizacion/historia sobre el proximo tema (Next track).",
                    "  - 4) Cerrar con pase corto y natural hacia ese tema.",
                ]
            )

    lines.append("- Output language for spoken script: es-AR")
    return "\n".join(lines)


def build_prompt(
    archetype: Archetype,
    station_name: str,
    personality: StationPersonality,
    current: QueueTrack,
    next_track: QueueTrack,
    current_meta: TrackMetadata,
    next_meta: TrackMetadata,
    angle: Optional[str],
    hook: str,
    banned_list: Sequence[str],
    recent_scripts: Sequence[str],
    schedule_context: Optional[ScheduleContext],
    deep_dive_focus: Optional[str] = None,
    story_count: Optional[int] = None,
    news_topics: Optional[Sequence[str]] = None,
) -> str:
    if archetype == Archetype.NEWS:
        now_utc = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
        wrapper = WRAPPER_NEWS.format(
            story_count=story_count or 1,
            news_topics=", ".join(news_topics or NEWS_TOPICS),
            news_max_age_hours=NEWS_MAX_AGE_HOURS,
            news_preferred_max_age_hours=NEWS_PREFERRED_MAX_AGE_HOURS,
            news_now_utc=now_utc.isoformat().replace("+00:00", "Z"),
            news_cutoff_utc=(
                now_utc - dt.timedelta(hours=NEWS_MAX_AGE_HOURS)
            ).isoformat().replace("+00:00", "Z"),
            news_preferred_cutoff_utc=(
                now_utc - dt.timedelta(hours=NEWS_PREFERRED_MAX_AGE_HOURS)
            ).isoformat().replace("+00:00", "Z"),
        )
    elif archetype == Archetype.CONCERT_CHECK:
        wrapper = WRAPPER_CONCERT_CHECK.format(
            concert_countries=", ".join(CONCERT_TARGET_COUNTRIES),
        )
    else:
        wrapper = {
            Archetype.BACK_SELL: WRAPPER_BACK_SELL,
            Archetype.DEEP_DIVE: WRAPPER_DEEP_DIVE,
            Archetype.BLOCK_INTRO: WRAPPER_BLOCK_INTRO,
            Archetype.ULTRA_MINIMAL: WRAPPER_ULTRA_MINIMAL,
        }.get(archetype, WRAPPER_ULTRA_MINIMAL)

    shared_input = format_shared_input(
        station_name=station_name,
        personality=personality,
        current=current,
        next_track=next_track,
        current_meta=current_meta,
        next_meta=next_meta,
        angle=angle,
        hook=hook,
        banned_list=banned_list,
        recent_scripts=recent_scripts,
        schedule_context=schedule_context,
        deep_dive_focus=deep_dive_focus,
    )

    return f"{wrapper}\n\n{shared_input}"


def station_name_for_generation(station_slug: str, fallback_name: str) -> str:
    normalized = (station_slug or "").strip().lower()
    return STATION_GENERATION_NAMES.get(normalized, fallback_name)


def resolve_station_personality(station_slug: str) -> StationPersonality:
    normalized = (station_slug or "").strip().lower()
    return STATION_PERSONALITIES.get(normalized, STATION_PERSONALITIES["neuralcast"])


def build_system_prompt(station_name: str, personality: StationPersonality) -> str:
    personality_guide = load_personality_guide()
    return (
        f"{HOST_CONSTITUTION_TEMPLATE.format(station_name=station_name).strip()}\n\n"
        f"{personality_guide}\n\n"
        f"{SCRIPT_STYLE_BASELINE.strip()}\n\n"
        "Station personality profile:\n"
        f"- {personality.script_profile}\n"
    )


def build_tts_instructions(personality: StationPersonality) -> str:
    base = TTS_INSTRUCTIONS_PATH.read_text(encoding="utf-8").strip()
    if not personality.tts_profile.strip():
        return base
    return f"{base}\n\nAjuste de personalidad de estacion:\n{personality.tts_profile}\n"


def gemini_generate_text(
    prompt: str,
    system_prompt: str,
    temperature: float,
    top_p: float,
    with_search: bool,
    model: str = "gemini-3-flash-preview",
) -> str:
    client = get_gemini_client()
    try:
        from google.genai import types
    except ImportError as exc:  # pragma: no cover - environment guard
        raise RuntimeError(
            "Gemini client is not installed. Install with: pip install google-genai"
        ) from exc

    config_kwargs: Dict[str, Any] = {
        "system_instruction": system_prompt,
        "temperature": temperature,
        "top_p": top_p,
    }
    if with_search:
        # Explicit Google Search grounding for research-backed generations.
        grounding_tool = types.Tool(google_search=types.GoogleSearch())
        config_kwargs["tools"] = [grounding_tool]

    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(**config_kwargs),
    )
    text = (response.text or "").strip()
    if with_search and text == "NO_SCRIPT":
        try:
            candidates = getattr(response, "candidates", None) or []
            finish_reason = None
            grounding_titles: List[str] = []
            grounding_chunk_count = 0
            if candidates:
                candidate0 = candidates[0]
                finish = getattr(candidate0, "finish_reason", None)
                finish_reason = str(finish) if finish is not None else None
                grounding = getattr(candidate0, "grounding_metadata", None)
                chunks = getattr(grounding, "grounding_chunks", None) or []
                grounding_chunk_count = len(chunks)
                for chunk in chunks[:5]:
                    web = getattr(chunk, "web", None)
                    title = (getattr(web, "title", None) or "").strip() if web else ""
                    if title:
                        grounding_titles.append(title)
            usage = getattr(response, "usage_metadata", None)
            total_tokens = getattr(usage, "total_token_count", None)
            LOGGER.warning(
                "[gemini/search] NO_SCRIPT response_id=%s finish=%s grounding_chunks=%s grounding_titles=%s total_tokens=%s",
                getattr(response, "response_id", None),
                finish_reason,
                grounding_chunk_count,
                grounding_titles,
                total_tokens,
            )
        except Exception:  # noqa: BLE001
            LOGGER.debug("[gemini/search] Failed to summarize grounding metadata for NO_SCRIPT.")
    if not text:
        raise RuntimeError("Gemini returned an empty text response.")
    return text


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


def _script_has_block_reference(script_text: str, schedule_context: ScheduleContext) -> bool:
    script_norm = _normalize_text_for_contains(script_text)
    if not script_norm:
        return False

    section_norm = _normalize_text_for_contains(schedule_context.section_label)
    if section_norm and section_norm in script_norm:
        return True

    playlist_norm = _normalize_text_for_contains(schedule_context.playlist_name or "")
    if playlist_norm and playlist_norm in script_norm:
        return True

    if "bloque" in script_norm or "seccion" in script_norm:
        for genre in schedule_context.genre_labels:
            genre_norm = _normalize_text_for_contains(genre)
            if genre_norm and genre_norm in script_norm:
                return True

    # Fallback: phrases that usually indicate block orientation.
    if "estamos en" in script_norm or "seguimos en" in script_norm:
        return True

    return False


def _build_mid_block_clause(
    schedule_context: ScheduleContext,
    archetype: Archetype,
    rng: random.Random,
) -> str:
    section = schedule_context.section_label.strip() or "este bloque"
    genres = ", ".join([item for item in schedule_context.genre_labels if item][:2]).strip()

    if archetype == Archetype.ULTRA_MINIMAL:
        options = [
            f"seguimos en {section}",
            f"metidos en {section}",
            f"en {section}",
        ]
        return rng.choice(options)

    if genres:
        options = [
            f"seguimos en {section}, con ese clima de {genres}",
            f"seguimos en {section}, bien en esa linea de {genres}",
            f"aca en {section}, con ese color de {genres}",
        ]
    else:
        options = [
            f"seguimos en {section}",
            f"aca en {section}",
            f"metidos en {section}",
        ]
    return rng.choice(options)


def ensure_mid_block_reference(
    script_text: str,
    archetype: Archetype,
    schedule_context: Optional[ScheduleContext],
    rng: random.Random,
) -> str:
    if (
        schedule_context is None
        or schedule_context.mention_intent != "mid"
        or archetype
        not in {Archetype.BACK_SELL, Archetype.DEEP_DIVE, Archetype.ULTRA_MINIMAL}
    ):
        return script_text

    if _script_has_block_reference(script_text, schedule_context):
        LOGGER.info(
            "[schedule] Mid-block mention already present in generated %s script for '%s'.",
            archetype.value,
            schedule_context.section_label,
        )
        return script_text

    clause = _build_mid_block_clause(schedule_context, archetype, rng)
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


def parse_structured_script_and_meta(
    raw: str, pattern: re.Pattern[str]
) -> Tuple[Optional[str], Optional[Mapping[str, Any]], str]:
    text = raw.strip()
    if text == "NO_SCRIPT":
        return None, None, "NO_SCRIPT"

    match = pattern.search(text)
    if not match:
        return None, None, "invalid format"

    script = cleanup_generated_script(match.group("script"))
    meta_raw = match.group("meta").strip()

    if meta_raw.startswith("```"):
        meta_raw = re.sub(r"^```(?:json)?", "", meta_raw, flags=re.IGNORECASE).strip()
        meta_raw = re.sub(r"```$", "", meta_raw).strip()

    try:
        meta = json.loads(meta_raw)
    except json.JSONDecodeError:
        return None, None, "invalid json"

    if not isinstance(meta, Mapping):
        return None, None, "meta must be object"

    if not script:
        return None, None, "script is empty"

    return script, meta, "ok"


def parse_news_output(raw: str) -> Tuple[Optional[NewsSegment], str]:
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
    if language.lower() != "es-ar":
        return None, "language must be es-AR"
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
) -> str:
    repair_prompt = REPAIR_NEWS_CONTRACT.format(original_output=original_output)
    return gemini_generate_text(
        prompt=repair_prompt,
        system_prompt=build_system_prompt(station_name, personality),
        temperature=temperature,
        top_p=top_p,
        with_search=False,
    )


def parse_concert_output(raw: str) -> Tuple[Optional[ConcertSegment], str]:
    script, meta, reason = parse_structured_script_and_meta(raw, CONCERT_OUTPUT_RE)
    if reason != "ok":
        return None, reason
    assert script is not None
    assert meta is not None

    language = str(meta.get("language") or "").strip()
    events = meta.get("events")
    if language.lower() != "es-ar":
        return None, "language must be es-AR"
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
) -> str:
    repair_prompt = REPAIR_CONCERT_CONTRACT.format(original_output=original_output)
    return gemini_generate_text(
        prompt=repair_prompt,
        system_prompt=build_system_prompt(station_name, personality),
        temperature=temperature,
        top_p=top_p,
        with_search=False,
    )


def parse_timestamp(value: Optional[str]) -> Optional[dt.datetime]:
    if not value:
        return None

    text = value.strip()
    if not text:
        return None

    try:
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed.astimezone(dt.timezone.utc)
    except ValueError:
        pass

    try:
        parsed = parsedate_to_datetime(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed.astimezone(dt.timezone.utc)
    except (TypeError, ValueError):
        return None


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


def pick_news_topics(story_count: int, rng: random.Random) -> List[str]:
    topics = list(NEWS_TOPICS)
    if story_count <= 1:
        return [rng.choice(topics)]
    if len(topics) < story_count:
        return [rng.choice(topics) for _ in range(story_count)]
    return rng.sample(topics, k=story_count)


def should_enable_search(archetype: Archetype, _angle: Optional[str]) -> bool:
    return archetype in {Archetype.NEWS, Archetype.DEEP_DIVE, Archetype.CONCERT_CHECK}


def fallback_to_ultra_minimal(
    station_name: str,
    personality: StationPersonality,
    current_track: QueueTrack,
    next_track: QueueTrack,
    current_meta: TrackMetadata,
    next_meta: TrackMetadata,
    banned_list: Sequence[str],
    schedule_context: Optional[ScheduleContext],
    state: OrchestratorState,
    rng: random.Random,
) -> Tuple[str, None, Archetype]:
    fallback_hook = choose_hook(Archetype.ULTRA_MINIMAL, state, rng)
    fallback_script, _, fallback_arch = generate_archetype_script(
        archetype=Archetype.ULTRA_MINIMAL,
        station_name=station_name,
        personality=personality,
        current_track=current_track,
        next_track=next_track,
        current_meta=current_meta,
        next_meta=next_meta,
        angle=None,
        hook=fallback_hook,
        banned_list=banned_list,
        schedule_context=schedule_context,
        state=state,
        rng=rng,
        forced_mode=False,
    )
    return fallback_script, None, fallback_arch


def generate_archetype_script(
    archetype: Archetype,
    station_name: str,
    personality: StationPersonality,
    current_track: QueueTrack,
    next_track: QueueTrack,
    current_meta: TrackMetadata,
    next_meta: TrackMetadata,
    angle: Optional[str],
    hook: str,
    banned_list: Sequence[str],
    schedule_context: Optional[ScheduleContext],
    state: OrchestratorState,
    rng: random.Random,
    forced_mode: bool,
) -> Tuple[str, Optional[NewsSegment], Archetype]:
    """Generate script and optional structured metadata.

    Returns: (script, news_segment, archetype_used)
    """

    temperature, top_p = sample_generation_settings(archetype, rng)
    system_prompt = build_system_prompt(station_name, personality)
    deep_dive_focus: Optional[str] = None
    if archetype == Archetype.DEEP_DIVE:
        deep_dive_focus = "current" if rng.random() < 0.5 else "next"
        LOGGER.info("[deep_dive] Focus mode selected: %s", deep_dive_focus)
    prompt_kwargs = {
        "station_name": station_name,
        "personality": personality,
        "current": current_track,
        "next_track": next_track,
        "current_meta": current_meta,
        "next_meta": next_meta,
        "angle": angle,
        "hook": hook,
        "banned_list": banned_list,
        "recent_scripts": state.recent_scripts,
        "schedule_context": schedule_context,
        "deep_dive_focus": deep_dive_focus,
    }

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

    def fallback() -> Tuple[str, None, Archetype]:
        return fallback_to_ultra_minimal(
            station_name=station_name,
            personality=personality,
            current_track=current_track,
            next_track=next_track,
            current_meta=current_meta,
            next_meta=next_meta,
            banned_list=banned_list,
            schedule_context=schedule_context,
            state=state,
            rng=rng,
        )

    if archetype not in {Archetype.NEWS, Archetype.CONCERT_CHECK}:
        prompt = build_prompt(archetype=archetype, **prompt_kwargs)
        generated = generate_with_retries(
            prompt=prompt,
            label=f"Gemini generation ({archetype.value})",
            with_search=should_enable_search(archetype, angle),
        )
        cleaned = cleanup_generated_script(generated)
        cleaned = ensure_mid_block_reference(
            script_text=cleaned,
            archetype=archetype,
            schedule_context=schedule_context,
            rng=rng,
        )
        return cleaned, None, archetype

    if archetype == Archetype.CONCERT_CHECK:
        generation_attempts = 2
        for generation_attempt in range(generation_attempts):
            prompt = build_prompt(archetype=Archetype.CONCERT_CHECK, **prompt_kwargs)
            generated = generate_with_retries(
                prompt=prompt,
                label="Gemini generation (concert_check)",
                with_search=True,
            )

            segment, reason = parse_concert_output(generated)
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
                    ),
                )
                segment, reason = parse_concert_output(repaired)
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
                    cleanup_generated_script(segment.script),
                    None,
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

        LOGGER.warning(
            "[concert_check] Exhausted retries; falling back to ultra_minimal."
        )
        return fallback()

    # News mode with validation, repair, and topic retries.
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

        segment, reason = parse_news_output(generated)
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
            # NEWS is a strict grounded + structured task; high-variance sampling can cause
            # spurious NO_SCRIPT decisions even when fresh headlines exist.
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
                segment, reason = parse_news_output(generated)

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
                ),
            )
            segment, reason = parse_news_output(repaired)
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
            return cleanup_generated_script(segment.script), segment, Archetype.NEWS

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

    LOGGER.warning(
        "[news] Exhausted topic retries; falling back to ultra_minimal."
    )
    return fallback()
