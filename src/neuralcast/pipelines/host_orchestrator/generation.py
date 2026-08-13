"""Prompting, generation, parsing, and validation for host orchestrator."""

from __future__ import annotations

import datetime as dt
import json
import random
import re
import unicodedata
from dataclasses import dataclass
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
    WRAPPER_ALBUM_SPOTLIGHT,
    WRAPPER_BACK_SELL,
    WRAPPER_BLOCK_INTRO,
    WRAPPER_CONCERT_CHECK,
    WRAPPER_DEEP_DIVE,
    WRAPPER_ERA_SNAPSHOT,
    WRAPPER_NEWS,
    WRAPPER_SHORT_STORY,
    WRAPPER_UP_NEXT_TEASE,
    WRAPPER_ULTRA_MINIMAL,
    load_personality_guide,
)
from .models import (
    Archetype,
    ConcertEventMeta,
    ConcertSegment,
    GeneratedSegmentMetadata,
    NewsSegment,
    NewsStoryMeta,
    OrchestratorState,
    QueueTrack,
    ScheduleContext,
    StationPersonality,
    TrackFocus,
    TrackMetadata,
)
from .state import (
    build_news_dedup_key,
    choose_hook,
    prune_news_history,
    sample_generation_settings,
)
from .utils import now_ts, run_with_retries
from neuralcast.services.ai_client import (
    DEFAULT_GEMINI_TEXT_MODEL,
    get_gemini_client,
)


@dataclass(frozen=True)
class ArchetypePromptVariants:
    short_story_focus: Optional[str] = None
    album_spotlight_focus: Optional[str] = None
    era_snapshot_lane: Optional[str] = None
    era_snapshot_focus: Optional[str] = None
    deep_dive_lane: Optional[str] = None
    deep_dive_focus: Optional[str] = None


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


def format_shared_input(
    archetype: Archetype,
    station_name: str,
    personality: StationPersonality,
    current: QueueTrack,
    next_track: QueueTrack,
    upcoming_tracks: Sequence[QueueTrack],
    current_meta: TrackMetadata,
    next_meta: TrackMetadata,
    angle: Optional[str],
    hook: str,
    banned_list: Sequence[str],
    recent_scripts: Sequence[str],
    schedule_context: Optional[ScheduleContext],
    short_story_focus: Optional[str] = None,
    album_spotlight_focus: Optional[str] = None,
    era_snapshot_lane: Optional[str] = None,
    era_snapshot_focus: Optional[str] = None,
    deep_dive_lane: Optional[str] = None,
    deep_dive_focus: Optional[str] = None,
) -> str:
    now_local = dt.datetime.now(SYSTEM_TZ).strftime("%Y-%m-%d %H:%M:%S %Z")
    hook_text = (hook or "").strip()
    if hook_text:
        hook_line = (
            f"- Idea de gancho (idea de entrada, no texto literal; opcional): {hook_text}"
        )
    else:
        hook_line = "- Idea de gancho (idea de entrada, no texto literal; opcional): ninguna (apertura libre permitida)"

    def _compose_track(label: str, track: QueueTrack, meta: TrackMetadata) -> List[str]:
        line = f"- {label}: {track.artist} — {track.title}"
        year = (meta.year or "").strip()
        genre = (meta.genre or "").strip()
        if year or genre:
            line += f" ({year or 'anio n/d'}, {genre or 'genero n/d'})"
        parts = [line]

        optional: List[str] = []
        if meta.bpm:
            optional.append(f"bpm={meta.bpm}")
        if meta.mood_tags:
            optional.append(f"etiquetas_clima={meta.mood_tags}")
        if meta.album:
            optional.append(f"album={meta.album}")
        if meta.notes:
            optional.append(f"notas={meta.notes}")
        if optional:
            parts.append(f"  Metadata opcional: {', '.join(optional)}")
        return parts

    lines = [
        "ENTRADA",
        f"- Estacion: {station_name}",
        f"- Personalidad de la estacion: {personality.script_profile}",
        f"- Hora local (Europe/Zurich): {now_local}",
    ]
    lines.extend(_compose_track("Tema actual", current, current_meta))
    lines.extend(_compose_track("Proximo tema", next_track, next_meta))
    if archetype == Archetype.UP_NEXT_TEASE:
        immediate_upcoming = list(upcoming_tracks[:4])
        if immediate_upcoming:
            lines.append("- Cola inmediata de temas por sonar (orden de queue):")
            lines.extend(
                [
                    f"  - {index}) {track.artist} — {track.title}"
                    for index, track in enumerate(immediate_upcoming, start=1)
                ]
            )
            unique_artists: List[str] = []
            seen_artists: set[str] = set()
            for track in immediate_upcoming:
                artist = str(track.artist or "").strip()
                if not artist:
                    continue
                artist_key = artist.lower()
                if artist_key in seen_artists:
                    continue
                seen_artists.add(artist_key)
                unique_artists.append(artist)
            if unique_artists:
                lines.append(
                    "- Bandas/artistas a mencionar como \"lo que sigue\": "
                    + ", ".join(unique_artists[:4])
                )
            lines.append(
                "- Regla del arquetipo up_next_tease: mencionar casualmente 2-4 bandas de esta cola e invitar a quedarse en este bloque."
            )
        else:
            lines.append(
                "- Cola inmediata de temas por sonar: no disponible; no inventar bandas."
            )
    lines.extend(
        [
            f"- Angulo (sub-perspectiva): {angle or 'ninguno'}",
            hook_line,
            "- Lista de temas/frases prohibidas:",
        ]
    )
    if banned_list:
        lines.extend([f"  - {item}" for item in banned_list])
    else:
        lines.append("  - ninguna")
    if recent_scripts:
        lines.extend(
            [
                "- Guiones recientes del host generados (mas reciente primero):",
                "  Usar como contexto anti-repeticion: evitar reutilizar aperturas o fragmentos muy parecidos, pero manteniendo una forma natural de hablar.",
            ]
        )
        lines.extend(
            [
                f"  - Guion {index}: {previous_script}"
                for index, previous_script in enumerate(recent_scripts, start=1)
            ]
        )
    else:
        lines.append("- Guiones recientes del host generados (mas reciente primero): ninguno")

    if schedule_context is not None:
        spoken_section_label = _spoken_section_label(schedule_context)
        next_section_line = (
            "  - Proxima seccion: oculta para mencion de mitad de bloque (evitar encuadre de cierre)."
            if schedule_context.mention_intent == "mid"
            else f"  - Proxima seccion: {schedule_context.next_section_label or 'n/d'}"
        )
        lines.extend(
            [
                "- Bloque de programacion activo:",
                "  - Nota de timing: este contexto de bloque corresponde al momento en que saldra este corte del host (inmediatamente antes del proximo tema).",
                f"  - Seccion: {spoken_section_label}",
                f"  - Generos: {', '.join(schedule_context.genre_labels)}",
                f"  - Fase: {schedule_context.phase} ({int(schedule_context.progress_ratio * 100)}%)",
                next_section_line,
            ]
        )
        if schedule_context.mode == "open":
            lines.append(
                "  - Modo del bloque: bloque libre / sin tematica (AzuraCast baraja canciones del catalogo completo segun pesos)."
            )
            lines.append(
                "  - Si mencionas este bloque open, sumar una clausula corta aclarando que puede sonar cualquier genero o cruce del catalogo."
            )
        elif schedule_context.playlist_name:
            lines.append(f"  - Modo del bloque: playlist fija ({schedule_context.playlist_name}).")

        if schedule_context.mention_intent == "start":
            lines.append(
                "- Guia de mencion de grilla: este guion sale en el limite de inicio del bloque; presentar la seccion como arrancando ahora (justo antes de su primer tema) y mencionar 1-2 etiquetas de genero que definan el bloque."
            )
            lines.append(
                "- Variacion de redaccion de grilla: al nombrar el bloque, variar el sustantivo de forma natural (por ejemplo: bloque, segmento, seccion, tramo, parte) en vez de repetir siempre la misma palabra."
            )
        elif schedule_context.mention_intent == "mid":
            lines.append(
                "- Guia de mencion de grilla: incluir una clausula corta y natural diciendo que estamos en esta seccion/bloque ahora mismo, y mencionar la linea de generos que representa (1-2 etiquetas)."
            )
            lines.append(
                "- Variacion de redaccion de grilla: al referirte al bloque en curso, podes alternar palabras como bloque, segmento, seccion, tramo o parte para que no suene repetitivo."
            )
            lines.append(
                "- Estilo de mencion de grilla: integrar la mencion del bloque en el flujo del arquetipo elegido (no como anuncio separado); estos recordatorios aparecen solo ocasionalmente (aprox. cada 2-3 cortes del host), asi que incluirlo esta vez."
            )
            lines.append(
                "- Regla de encuadre de mitad de bloque (obligatorio): tratar el bloque como ACTUALMENTE EN CURSO; no decir ni insinuar que el bloque/seccion se esta cerrando, terminando o por cambiar."
            )
            lines.append(
                "- Preferencia de redaccion para mitad de bloque: usar presente continuo/orientacion de continuidad (por ejemplo: 'seguimos en...', 'estamos en...', 'aca en...')."
            )
        else:
            lines.append(
                "- Guia de mencion de grilla: opcional; evitar repetir menciones de seccion."
            )

    if short_story_focus in {"current", "next"}:
        focus_label = (
            "actual (tema que acaba de sonar)"
            if short_story_focus == "current"
            else "proximo (tema que va a sonar ahora)"
        )
        lines.extend(
            [
                f"- Short-story focus mode (obligatorio si el arquetipo es 'short_story'): {focus_label}",
                "- Short-story secuencia oral obligatoria (seguir exactamente este orden narrativo):",
            ]
        )
        if short_story_focus == "current":
            lines.extend(
                [
                    "  - 1) Decir de forma natural que el tema actual (Tema actual) acaba de sonar.",
                    "  - 2) Contar la profundizacion/historia sobre el tema actual (Tema actual).",
                    "  - 3) Cerrar presentando el proximo tema (Proximo tema).",
                ]
            )
        else:
            lines.extend(
                [
                    "  - 1) Decir de forma natural que el tema actual (Tema actual) acaba de sonar.",
                    "  - 2) Decir cual es el proximo tema (Proximo tema).",
                    "  - 3) Contar la profundizacion/historia sobre el proximo tema (Proximo tema).",
                    "  - 4) Cerrar con pase corto y natural hacia ese tema.",
                ]
            )

    if album_spotlight_focus in {"current", "next"}:
        focus_label = (
            "actual (tema que acaba de sonar)"
            if album_spotlight_focus == "current"
            else "proximo (tema que va a sonar ahora)"
        )
        lines.extend(
            [
                f"- Album-spotlight focus mode (obligatorio si el arquetipo es 'album_spotlight'): {focus_label}",
                "- Album-spotlight secuencia oral obligatoria (seguir exactamente este orden narrativo):",
            ]
        )
        if album_spotlight_focus == "current":
            lines.extend(
                [
                    "  - 1) Decir de forma natural que el tema actual (Tema actual) acaba de sonar.",
                    "  - 2) Abrir la mirada al album del tema actual (Tema actual), priorizando el disco por encima de la biografia general.",
                    "  - 3) Cerrar presentando el proximo tema (Proximo tema).",
                ]
            )
        else:
            lines.extend(
                [
                    "  - 1) Decir de forma natural que el tema actual (Tema actual) acaba de sonar.",
                    "  - 2) Decir cual es el proximo tema (Proximo tema).",
                    "  - 3) Abrir la mirada al album del proximo tema (Proximo tema), priorizando el disco por encima de la biografia general.",
                    "  - 4) Cerrar con pase corto y natural hacia ese tema.",
                ]
            )

    if era_snapshot_lane:
        lines.extend(
            [
                f"- Era-snapshot lane (obligatorio si el arquetipo es 'era_snapshot'): {era_snapshot_lane}",
                "- Era-snapshot formato objetivo: contexto amplio pero mas ligero que un deep-dive (aprox. 260-420 palabras).",
            ]
        )
    if era_snapshot_focus in {"current", "next"}:
        focus_label = (
            "actual (tema que acaba de sonar)"
            if era_snapshot_focus == "current"
            else "proximo (tema que va a sonar ahora)"
        )
        lines.extend(
            [
                f"- Era-snapshot focus mode (obligatorio si el arquetipo es 'era_snapshot'): {focus_label}",
                "- Era-snapshot secuencia oral obligatoria (seguir exactamente este orden narrativo):",
            ]
        )
        if era_snapshot_focus == "current":
            lines.extend(
                [
                    "  - 1) Decir de forma natural que el tema actual (Tema actual) acaba de sonar.",
                    "  - 2) Contar la postal de epoca/escena sobre el tema actual (Tema actual), usando el lane indicado.",
                    "  - 3) Cerrar presentando el proximo tema (Proximo tema).",
                ]
            )
        else:
            lines.extend(
                [
                    "  - 1) Decir de forma natural que el tema actual (Tema actual) acaba de sonar.",
                    "  - 2) Decir cual es el proximo tema (Proximo tema).",
                    "  - 3) Contar la postal de epoca/escena sobre el proximo tema (Proximo tema), usando el lane indicado.",
                    "  - 4) Cerrar con pase corto y natural hacia ese tema.",
                ]
            )

    if deep_dive_lane:
        lines.extend(
            [
                f"- Deep-dive lane (obligatorio si el arquetipo es 'deep_dive'): {deep_dive_lane}",
                "- Deep-dive formato objetivo: relato largo de 3-5 minutos (aprox. 420-700 palabras).",
            ]
        )
    if deep_dive_focus in {"current", "next"}:
        focus_label = (
            "actual (tema que acaba de sonar)"
            if deep_dive_focus == "current"
            else "proximo (tema que va a sonar ahora)"
        )
        lines.extend(
            [
                f"- Deep-dive focus mode (obligatorio si el arquetipo es 'deep_dive'): {focus_label}",
                "- Deep-dive secuencia oral obligatoria (seguir exactamente este orden narrativo):",
            ]
        )
        if deep_dive_focus == "current":
            lines.extend(
                [
                    "  - 1) Decir de forma natural que el tema actual (Tema actual) acaba de sonar.",
                    "  - 2) Contar el deep-dive sobre el tema actual (Tema actual), usando el lane indicado.",
                    "  - 3) Cerrar presentando el proximo tema (Proximo tema).",
                ]
            )
        else:
            lines.extend(
                [
                    "  - 1) Decir de forma natural que el tema actual (Tema actual) acaba de sonar.",
                    "  - 2) Decir cual es el proximo tema (Proximo tema).",
                    "  - 3) Contar el deep-dive sobre el proximo tema (Proximo tema), usando el lane indicado.",
                    "  - 4) Cerrar con pase corto y natural hacia ese tema.",
                ]
            )

    lines.append("- Idioma de salida del guion hablado: es-AR")
    return "\n".join(lines)


def build_prompt(
    archetype: Archetype,
    station_name: str,
    personality: StationPersonality,
    current: QueueTrack,
    next_track: QueueTrack,
    upcoming_tracks: Sequence[QueueTrack],
    current_meta: TrackMetadata,
    next_meta: TrackMetadata,
    angle: Optional[str],
    hook: str,
    banned_list: Sequence[str],
    recent_scripts: Sequence[str],
    schedule_context: Optional[ScheduleContext],
    short_story_focus: Optional[str] = None,
    album_spotlight_focus: Optional[str] = None,
    era_snapshot_lane: Optional[str] = None,
    era_snapshot_focus: Optional[str] = None,
    deep_dive_lane: Optional[str] = None,
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
            Archetype.UP_NEXT_TEASE: WRAPPER_UP_NEXT_TEASE,
            Archetype.DEEP_DIVE: WRAPPER_DEEP_DIVE,
            Archetype.SHORT_STORY: WRAPPER_SHORT_STORY,
            Archetype.ALBUM_SPOTLIGHT: WRAPPER_ALBUM_SPOTLIGHT,
            Archetype.ERA_SNAPSHOT: WRAPPER_ERA_SNAPSHOT,
            Archetype.BLOCK_INTRO: WRAPPER_BLOCK_INTRO,
            Archetype.ULTRA_MINIMAL: WRAPPER_ULTRA_MINIMAL,
        }.get(archetype, WRAPPER_ULTRA_MINIMAL)

    shared_input = format_shared_input(
        archetype=archetype,
        station_name=station_name,
        personality=personality,
        current=current,
        next_track=next_track,
        upcoming_tracks=upcoming_tracks,
        current_meta=current_meta,
        next_meta=next_meta,
        angle=angle,
        hook=hook,
        banned_list=banned_list,
        recent_scripts=recent_scripts,
        schedule_context=schedule_context,
        short_story_focus=short_story_focus,
        album_spotlight_focus=album_spotlight_focus,
        era_snapshot_lane=era_snapshot_lane,
        era_snapshot_focus=era_snapshot_focus,
        deep_dive_lane=deep_dive_lane,
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
        "Perfil de personalidad de la estacion:\n"
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
    model: str = DEFAULT_GEMINI_TEXT_MODEL,
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


def _script_has_genre_reference(script_text: str, schedule_context: ScheduleContext) -> bool:
    script_norm = _normalize_text_for_contains(script_text)
    if not script_norm:
        return False

    if schedule_context.mode == "open":
        for marker in (
            "bloque libre",
            "sin tematica",
            "mezcla libre",
            "cualquier genero",
            "catalogo",
            "generos cruzados",
        ):
            if marker in script_norm:
                return True

    for genre in schedule_context.genre_labels:
        genre_norm = _normalize_text_for_contains(genre)
        if genre_norm and genre_norm in script_norm:
            return True
    return False


def _spoken_section_label(schedule_context: ScheduleContext) -> str:
    if schedule_context.mode == "open":
        return "bloque libre"

    section = schedule_context.section_label.strip()
    return section or "este bloque"


def _build_mid_block_clause(
    schedule_context: ScheduleContext,
    archetype: Archetype,
    rng: random.Random,
) -> str:
    section = _spoken_section_label(schedule_context)
    genres = ", ".join([item for item in schedule_context.genre_labels if item][:2]).strip()

    if schedule_context.mode == "open":
        if archetype == Archetype.ULTRA_MINIMAL:
            options = [
                "seguimos en bloque libre, sin tematica",
                "aca en bloque libre, mezcla libre",
                "en bloque libre, sin tematica",
            ]
        else:
            options = [
                "seguimos en bloque libre, sin tematica, con mezcla de todo el catalogo",
                "aca en bloque libre: pueden caer generos cruzados de todo el catalogo",
                "seguimos en mezcla libre, sin tematica fija, con catalogo barajado",
            ]
        return rng.choice(options)

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


def _build_schedule_genre_clause(
    schedule_context: ScheduleContext,
    archetype: Archetype,
    rng: random.Random,
) -> str:
    section = _spoken_section_label(schedule_context)
    genres = [str(item).strip() for item in schedule_context.genre_labels if str(item).strip()]
    genres_text = ", ".join(genres[:2]).strip()
    if not genres_text and schedule_context.mode != "open":
        return ""

    mention_intent = schedule_context.mention_intent or "none"
    if schedule_context.mode == "open":
        if mention_intent == "start":
            if archetype == Archetype.ULTRA_MINIMAL:
                options = [
                    "arranca bloque libre, sin tematica",
                    "arranca mezcla libre, sin tematica",
                ]
            else:
                options = [
                    "arranca bloque libre, sin tematica: puede sonar cualquier genero del catalogo",
                    "arranca mezcla libre, con catalogo barajado y cruce de generos",
                    "arranca bloque libre, sin tematica fija, con todo el catalogo en juego",
                ]
        elif mention_intent == "mid":
            if archetype == Archetype.ULTRA_MINIMAL:
                options = [
                    "seguimos en bloque libre, sin tematica",
                    "aca en bloque libre, mezcla libre",
                ]
            else:
                options = [
                    "seguimos en bloque libre, sin tematica, pueden caer generos de todo el catalogo",
                    "aca en mezcla libre, sin tematica fija, con catalogo barajado",
                    "seguimos en bloque libre: cruce de generos de todo el catalogo",
                ]
        else:
            options = ["en bloque libre, sin tematica"]
    elif mention_intent == "start":
        if archetype == Archetype.ULTRA_MINIMAL:
            options = [
                f"arranca {section}, con {genres_text}",
                f"arrancamos {section}, con {genres_text}",
            ]
        else:
            options = [
                f"arranca {section}, con ese eje de {genres_text}",
                f"arrancamos {section}, en clave de {genres_text}",
                f"abre {section}, con ese color de {genres_text}",
            ]
    elif mention_intent == "mid":
        if archetype == Archetype.ULTRA_MINIMAL:
            options = [
                f"seguimos en {section}, con {genres_text}",
                f"aca en {section}, con {genres_text}",
            ]
        else:
            options = [
                f"seguimos en {section}, con ese clima de {genres_text}",
                f"aca en {section}, bien en esa linea de {genres_text}",
                f"seguimos en {section}, en clave de {genres_text}",
            ]
    else:
        options = [f"en esa linea de {genres_text}"]

    return rng.choice(options)


def ensure_schedule_genre_reference(
    script_text: str,
    archetype: Archetype,
    schedule_context: Optional[ScheduleContext],
    rng: random.Random,
) -> str:
    if schedule_context is None:
        return script_text

    if schedule_context.mention_intent not in {"start", "mid"}:
        return script_text

    if _script_has_genre_reference(script_text, schedule_context):
        return script_text

    clause = _build_schedule_genre_clause(schedule_context, archetype, rng)
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
) -> str:
    cleaned = cleanup_generated_script(script_text)
    cleaned = ensure_mid_block_reference(
        script_text=cleaned,
        archetype=archetype,
        schedule_context=schedule_context,
        rng=rng,
    )
    cleaned = ensure_schedule_genre_reference(
        script_text=cleaned,
        archetype=archetype,
        schedule_context=schedule_context,
        rng=rng,
    )
    return cleaned


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
) -> str:
    current_artist = str(current_track.artist or "").strip()
    current_title = str(current_track.title or "").strip()
    next_artist = str(next_track.artist or "").strip()
    next_title = str(next_track.title or "").strip()

    options: List[str] = []
    if current_title and next_artist and next_title:
        options.extend(
            [
                f"Recien sono {current_title}; ahora sigue {next_artist} con {next_title}.",
                f"Venimos de {current_artist} con {current_title}; ahora entra {next_artist} con {next_title}.",
            ]
        )
    if next_artist and next_title:
        options.append(f"Ahora sigue {next_artist} con {next_title}.")

    fallback_script = rng.choice(options) if options else "Seguimos con musica."
    return _postprocess_schedule_script(
        script_text=fallback_script,
        archetype=Archetype.ULTRA_MINIMAL,
        schedule_context=schedule_context,
        rng=rng,
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
) -> Tuple[str, GeneratedSegmentMetadata, Archetype]:
    fallback_hook = choose_hook(Archetype.ULTRA_MINIMAL, state, rng)
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
        with_search=should_enable_search(archetype, angle),
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
                _postprocess_schedule_script(
                    script_text=segment.script,
                    archetype=Archetype.CONCERT_CHECK,
                    schedule_context=schedule_context,
                    rng=rng,
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
            return (
                _postprocess_schedule_script(
                    script_text=segment.script,
                    archetype=Archetype.NEWS,
                    schedule_context=schedule_context,
                    rng=rng,
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
) -> Tuple[str, GeneratedSegmentMetadata, Archetype]:
    """Generate script and structured presentation metadata.

    Returns: (script, segment_metadata, archetype_used).

    ``segment_metadata`` carries validated news/concert facts and the selected
    current/next track focus used by the listener-facing title builder.
    """

    temperature, top_p = sample_generation_settings(archetype, rng)
    system_prompt = build_system_prompt(station_name, personality)
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
