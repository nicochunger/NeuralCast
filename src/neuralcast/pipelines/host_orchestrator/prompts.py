"""Prompt construction and station voice instructions for host generation."""

from __future__ import annotations

import datetime as dt
import pathlib
from typing import List, Optional, Sequence

from .channels import HostLocale, get_channel_registry
from .config import (
    CONCERT_TARGET_COUNTRIES,
    HOST_CONSTITUTION_TEMPLATE,
    NEWS_MAX_AGE_HOURS,
    NEWS_PREFERRED_MAX_AGE_HOURS,
    NEWS_TOPICS,
    SCRIPT_STYLE_BASELINE,
    STATION_GENERATION_NAMES,
    STATION_PERSONALITIES,
    SYSTEM_TZ,
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
    QueueTrack,
    ScheduleContext,
    StationPersonality,
    TrackMetadata,
)


def _default_locale() -> HostLocale:
    return get_channel_registry().locales["es-AR"]


def _resolved_locale(locale: Optional[HostLocale]) -> HostLocale:
    return locale or _default_locale()


def _spoken_section_label(
    schedule_context: ScheduleContext,
    locale: Optional[HostLocale] = None,
) -> str:
    locale = _resolved_locale(locale)
    return schedule_context.spoken_section_label(
        fallback=str(locale.presentation.get("default_section") or "music selection")
    )


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
    locale: Optional[HostLocale] = None,
) -> str:
    locale = _resolved_locale(locale)
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
        f"- Hora local ({SYSTEM_TZ.key}): {now_local}",
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

    lines.extend(
        [
            f"- Idioma de salida del guion hablado: {locale.tag} ({locale.output_language})",
            f"- Instruccion de idioma obligatoria y prioritaria: {locale.script_guidance}",
        ]
    )
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
    locale: Optional[HostLocale] = None,
) -> str:
    locale = _resolved_locale(locale)
    if archetype == Archetype.NEWS:
        now_utc = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
        wrapper = WRAPPER_NEWS.replace("es-AR", locale.tag).format(
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
        wrapper = WRAPPER_CONCERT_CHECK.replace("es-AR", locale.tag).format(
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
        locale=locale,
    )

    return (
        f"{wrapper}\n\n{shared_input}\n\n"
        "LANGUAGE OVERRIDE (highest priority):\n"
        f"{locale.script_guidance}\n"
    )


def station_name_for_generation(station_slug: str, fallback_name: str) -> str:
    normalized = (station_slug or "").strip().lower()
    return STATION_GENERATION_NAMES.get(normalized, fallback_name)


def resolve_station_personality(station_slug: str) -> StationPersonality:
    normalized = (station_slug or "").strip().lower()
    return STATION_PERSONALITIES.get(normalized, STATION_PERSONALITIES["neuralcast"])


def build_system_prompt(
    station_name: str,
    personality: StationPersonality,
    locale: Optional[HostLocale] = None,
) -> str:
    locale = _resolved_locale(locale)
    personality_guide = load_personality_guide()
    return (
        f"{HOST_CONSTITUTION_TEMPLATE.format(station_name=station_name).strip()}\n\n"
        f"{personality_guide}\n\n"
        f"{SCRIPT_STYLE_BASELINE.strip()}\n\n"
        "Perfil de personalidad de la estacion:\n"
        f"- {personality.script_profile}\n\n"
        "Language rule (highest priority):\n"
        f"- {locale.script_guidance}\n"
    )


def build_tts_instructions(
    personality: StationPersonality,
    locale: Optional[HostLocale] = None,
    override_path: Optional[pathlib.Path] = None,
) -> str:
    locale = _resolved_locale(locale)
    base = (override_path or locale.tts_instructions_path).read_text(
        encoding="utf-8"
    ).strip()
    if override_path is not None:
        return base
    if not personality.tts_profile.strip():
        return base
    return f"{base}\n\nStation personality adjustment:\n{personality.tts_profile}\n"


__all__ = [
    "build_prompt",
    "build_system_prompt",
    "build_tts_instructions",
    "format_shared_input",
    "resolve_station_personality",
    "station_name_for_generation",
]
