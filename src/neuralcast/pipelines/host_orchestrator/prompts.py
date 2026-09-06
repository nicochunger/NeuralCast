"""Prompt construction and station voice instructions for host generation."""

from __future__ import annotations

import datetime as dt
import pathlib
from typing import List, Optional, Sequence

from .archetype_policies import (
    ResolvedArchetypeProfile,
    get_archetype_policy_registry,
)
from .channels import HostLocale, get_channel_registry
from .config import (
    HOOKS_BY_ARCHETYPE,
    STATION_GENERATION_NAMES,
    STATION_PERSONALITIES,
    SYSTEM_TZ,
    get_prompt_template_from,
    load_personality_guide_from,
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
    if schedule_context.mode == "open":
        return str(locale.schedule.get("open_label") or "open rotation")

    section = schedule_context.section_label.strip()
    return section or str(locale.schedule.get("open_label") or "this block")


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
    if locale.tag == "fr-CH":
        return _format_shared_input_fr(
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
        spoken_section_label = _spoken_section_label(schedule_context, locale)
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


_FRENCH_ANGLES = {
    "Minimalist": "Minimaliste",
    "Connector": "Connexion",
    "Fanatic": "Passionné",
    "Back-sell + bloque que sigue": "Retour sur le morceau et séquence à venir",
    "Nombrar 2-3 bandas y quedarse": "Nommer 2 ou 3 groupes et inviter à rester",
    "Puente corto con tease casual": "Courte transition avec aperçu naturel",
}

_FRENCH_LANES = {
    "historia de la banda": "histoire du groupe",
    "era y contexto": "époque et contexte",
    "historia de album": "histoire de l'album",
    "genealogia de cancion": "généalogie de la chanson",
    "mitologia en vivo": "mythologie du live",
    "escena y geografia": "scène et géographie",
    "mutacion del genero": "mutation du genre",
    "momento cultural / industrial": "moment culturel ou industriel",
    "la banda dentro de esa epoca": "le groupe dans son époque",
}

_FRENCH_SCHEDULE_TERMS = {
    "Bloque libre": "Rotation libre",
    "Cruce libre": "Mélange libre",
    "Mezcla libre": "Mélange libre",
    "Sin tematica": "Sans thème",
    "Novedades": "Nouveautés",
    "Metal neo clasico": "Metal néoclassique",
    "Death melodico": "Death metal mélodique",
    "Metal clasico": "Metal classique",
    "Heavy britanico clasico": "Heavy metal britannique classique",
    "Metal celta": "Metal celtique",
    "Metal progresivo": "Metal progressif",
    "Metal sinfonico": "Metal symphonique",
    "Prog instrumental": "Prog instrumental",
    "catalogo completo": "catalogue complet",
    "death melodico": "death metal mélodique",
    "heavy metal clasico": "heavy metal classique",
    "metal celta": "metal celtique",
    "metal clasico": "metal classique",
    "metal extremo": "metal extrême",
    "metal neo clasico": "metal néoclassique",
    "metal progresivo": "metal progressif",
    "metal sinfonico": "metal symphonique",
    "mix variado": "mélange varié",
    "novedades": "nouveautés",
    "sin tematica": "sans thème",
    "virtuosismo": "virtuosité",
}

_FRENCH_HOOKS_BY_ARCHETYPE = {
    Archetype.BACK_SELL: (
        "un détail de la fin qui résonne encore",
        "la texture laissée par le morceau",
        "un contraste doux avec ce qui arrive",
        "une continuité avec un léger tournant",
        "une courte observation sur le morceau puis la transition",
        "cette fin encore suspendue",
        "une nuance de la fin pour faire le lien",
        "ce qui reste dans l'air et la direction que cela prend",
        "une transition par la dynamique, pas par l'exagération",
        "la fin du morceau puis le tournant vers le prochain",
    ),
    Archetype.UP_NEXT_TEASE: (
        "une courte conclusion et un aperçu des groupes qui arrivent",
        "nommer rapidement la séquence à venir et inviter à rester",
        "une transition naturelle avec deux ou trois noms de la séquence",
        "montrer le fil de la séquence sans sonner comme une liste",
        "enchaîner les artistes comme dans une conversation puis continuer",
        "une transition rapide qui donne l'impression d'une séquence en direct",
        "mettre en valeur la continuité sans trop l'expliquer",
        "donner envie avec une petite suite de groupes",
        "passer naturellement de cette fin à ce qui arrive",
        "inviter à rester pour la série qui arrive",
    ),
    Archetype.SHORT_STORY: (
        "un petit détail qui change l'écoute",
        "une courte histoire derrière le morceau",
        "le contexte d'une époque à partir d'un point concret",
        "un gros plan sur un geste sonore",
        "un fait précis et une courte lecture",
        "pourquoi ce morceau frappe autrement avec son contexte",
        "une couche supplémentaire sans devenir un essai",
        "le moment du groupe à cette étape",
        "un détail de production s'il apparaît",
        "une brève lecture interprétative si les faits manquent",
    ),
    Archetype.ALBUM_SPOTLIGHT: (
        "le disque autour de ce morceau",
        "pourquoi cet album change l'écoute",
        "l'identité de l'album en un court instantané",
        "la place de ce morceau dans le disque",
        "l'ambiance générale de l'album et son poids",
        "une lecture de l'album sans faire une critique",
        "le moment du groupe sur ce disque",
        "la texture et le concept de l'album en quelques lignes",
        "ce qui fait que cet album donne un autre impact à ce morceau",
        "un bref regard sur l'album puis une transition naturelle",
    ),
    Archetype.ERA_SNAPSHOT: (
        "un instantané de l'époque autour du morceau",
        "ce qui changeait dans cette scène",
        "le moment du genre sans faire un documentaire",
        "le groupe plongé dans ce climat historique",
        "une courte image de l'époque et de son son",
        "ce qui se passait autour de cette sortie",
        "la scène, le contexte et leur importance pour écouter ceci",
        "un fragment d'époque avec un fil musical",
        "le mouvement de la scène et son arrivée sur le morceau",
        "un contexte large mais vivant, avec une transition vers le prochain",
    ),
    Archetype.DEEP_DIVE: (
        "une longue histoire du groupe avec un arc clair",
        "origine, rupture et héritage dans un récit",
        "un mini-documentaire radio sur une période",
        "une chronologie vivante avec des tournants",
        "débuts, réinvention et moment charnière",
        "l'histoire d'un album avec son contexte d'époque",
        "la généalogie d'une chanson et son évolution en live",
        "la trame d'une scène et la place qu'y prend le groupe",
        "une version longue dans une ambiance de radio nocturne",
        "un récit approfondi avec une transition vers le prochain morceau",
    ),
    Archetype.NEWS: (
        "un rapide tour utile puis le retour à la musique",
        "une information qui compte aujourd'hui, sans ton de bulletin",
        "un titre bref avec la raison de son importance",
        "une parenthèse d'actualité sur un ton proche",
        "selon le média, le fait essentiel puis le retour à l'antenne",
        "deux titres comme une conversation, pas un journal",
        "une actualité claire et sans alarmisme",
        "une information rapide puis on continue",
        "un bref résumé avec une transition chaleureuse",
        "le monde extérieur pendant une minute puis le retour",
    ),
    Archetype.CONCERT_CHECK: (
        "un rapide regard sur les dates des deux artistes",
        "s'il y a un concert proche, dire qui, quand et où",
        "un agenda utile sur un ton radiophonique",
        "un bref point sur les tournées puis le retour à la musique",
        "voir si des dates approchent dans la région",
        "une ou deux dates fortes, sans sonner comme une base de données",
        "la date d'abord, puis la ville et la salle",
        "un radar des concerts avec une transition vers le prochain morceau",
        "un croisement de calendriers sans remplissage",
        "pratique et proche, jamais une liste sèche",
    ),
    Archetype.BLOCK_INTRO: (
        "le début de la séquence avec son ambiance sonore",
        "la section qui commence et ce qui arrive",
        "une courte présentation de la séquence actuelle",
        "indiquer le genre ou l'ambiance puis laisser place à la musique",
        "une entrée chaleureuse sans ton d'annonce",
        "orienter rapidement puis laisser vivre la séquence",
        "ouvrir la séquence avec un indice sonore concret",
    ),
    Archetype.ULTRA_MINIMAL: (
        "une courte transition vers le prochain morceau",
        "une seule proposition puis on continue",
        "nommer le morceau puis s'effacer",
        "presque sans pause, mais humain",
        "une transition minimale avec une fin brève",
        "directement vers le prochain, sans détour",
        "une entrée courte, sans métaphore",
        "continuer sur une ligne simple",
        "présenter puis laisser de l'air",
        "une micro-transition puis la musique",
    ),
}


def _french_hook(archetype: Archetype, hook: str) -> str:
    source_hooks = HOOKS_BY_ARCHETYPE.get(archetype, ())
    localized_hooks = _FRENCH_HOOKS_BY_ARCHETYPE.get(archetype, ())
    try:
        index = source_hooks.index(hook)
    except ValueError:
        return hook
    if index >= len(localized_hooks):
        return hook
    return localized_hooks[index]


def _french_banned_item(item: str) -> str:
    exact = {
        "Alright folks": "Bon, tout le monde",
        "Hope you're having a great day": "J'espère que vous passez une excellente journée",
        "Bueno gente": "Bon, les amis",
        "Hola a todos": "Bonjour à toutes et à tous",
        "Querida audiencia": "Chers auditeurs",
    }
    if item in exact:
        return exact[item]
    if item.startswith("overused style cliche: "):
        cliche = item.removeprefix("overused style cliche: ")
        cliches = {
            "acero": "acier",
            "voltaje": "voltage",
            "fuego": "feu",
            "rugir": "rugissement",
            "tormenta": "tempête",
            "explosion": "explosion",
            "incendio": "incendie",
            "pulso": "pulsation",
        }
        return f"cliché de style trop employé : {cliches.get(cliche, cliche)}"
    if item.startswith("repeat previous hook: "):
        return "ne pas répéter l'accroche précédente"
    if item.startswith("repeat previous archetype: "):
        value = item.removeprefix("repeat previous archetype: ")
        return f"ne pas répéter l'archétype précédent : {value}"
    if item.startswith("repeat previous angle for "):
        return "ne pas répéter l'angle précédent de cet archétype"
    if item.startswith("recent headline already used: "):
        value = item.removeprefix("recent headline already used: ")
        return f"titre récent déjà employé : {value}"
    return item


def _french_schedule_term(value: str) -> str:
    return _FRENCH_SCHEDULE_TERMS.get(value.strip(), value.strip())


def _format_shared_input_fr(
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
    short_story_focus: Optional[str],
    album_spotlight_focus: Optional[str],
    era_snapshot_lane: Optional[str],
    era_snapshot_focus: Optional[str],
    deep_dive_lane: Optional[str],
    deep_dive_focus: Optional[str],
    locale: HostLocale,
) -> str:
    now_local = dt.datetime.now(SYSTEM_TZ).strftime("%Y-%m-%d %H:%M:%S %Z")
    hook_line = (
        "- Idée d'accroche (impulsion, pas texte littéral ; facultative) : "
        + _french_hook(archetype, hook)
        if (hook or "").strip()
        else "- Idée d'accroche (impulsion, pas texte littéral ; facultative) : aucune, ouverture libre permise"
    )

    def compose_track(
        label: str, track: QueueTrack, meta: TrackMetadata
    ) -> List[str]:
        line = f"- {label} : {track.artist} — {track.title}"
        year = (meta.year or "").strip()
        genre = (meta.genre or "").strip()
        if year or genre:
            line += f" ({year or 'année n/d'}, {genre or 'genre n/d'})"
        parts = [line]
        optional: List[str] = []
        if meta.bpm:
            optional.append(f"bpm={meta.bpm}")
        if meta.mood_tags:
            optional.append(f"tags_ambiance={meta.mood_tags}")
        if meta.album:
            optional.append(f"album={meta.album}")
        if meta.notes:
            optional.append(f"notes={meta.notes}")
        if optional:
            parts.append(f"  Métadonnées facultatives : {', '.join(optional)}")
        return parts

    lines = [
        "ENTRÉE",
        f"- Station : {station_name}",
        f"- Personnalité de la station : {personality.script_profile}",
        f"- Heure locale ({SYSTEM_TZ.key}) : {now_local}",
    ]
    lines.extend(compose_track("Morceau actuel", current, current_meta))
    lines.extend(compose_track("Prochain morceau", next_track, next_meta))

    if archetype == Archetype.UP_NEXT_TEASE:
        immediate_upcoming = list(upcoming_tracks[:4])
        if immediate_upcoming:
            lines.append("- File immédiate des morceaux à venir, dans l'ordre :")
            lines.extend(
                f"  - {index}) {track.artist} — {track.title}"
                for index, track in enumerate(immediate_upcoming, start=1)
            )
            unique_artists: List[str] = []
            seen_artists: set[str] = set()
            for track in immediate_upcoming:
                artist = str(track.artist or "").strip()
                if artist and artist.casefold() not in seen_artists:
                    seen_artists.add(artist.casefold())
                    unique_artists.append(artist)
            if unique_artists:
                lines.append(
                    "- Groupes ou artistes à mentionner comme la suite : "
                    + ", ".join(unique_artists[:4])
                )
            lines.append(
                "- Règle de l'archétype up_next_tease : mentionner naturellement 2 à 4 groupes de cette file et inviter à rester dans la séquence."
            )
        else:
            lines.append(
                "- File immédiate des morceaux à venir : indisponible ; ne pas inventer de groupes."
            )

    lines.extend(
        [
            f"- Angle, sous-perspective : {_FRENCH_ANGLES.get(angle or '', angle or 'aucun')}",
            hook_line,
            "- Liste de sujets ou formules interdits :",
        ]
    )
    if banned_list:
        lines.extend(f"  - {_french_banned_item(item)}" for item in banned_list)
    else:
        lines.append("  - aucun")

    if recent_scripts:
        lines.extend(
            [
                "- Textes récents générés pour l'animateur, du plus récent au plus ancien :",
                "  Les employer comme contexte anti-répétition : éviter de reprendre des ouvertures ou fragments très proches, tout en gardant une parole naturelle.",
            ]
        )
        lines.extend(
            f"  - Texte {index} : {previous_script}"
            for index, previous_script in enumerate(recent_scripts, start=1)
        )
    else:
        lines.append("- Textes récents générés pour l'animateur : aucun")

    if schedule_context is not None:
        spoken_section_label = _french_schedule_term(
            _spoken_section_label(schedule_context, locale)
        )
        next_section_line = (
            "  - Prochaine section : masquée pour une mention en milieu de séquence, afin d'éviter une impression de clôture."
            if schedule_context.mention_intent == "mid"
            else "  - Prochaine section : "
            + _french_schedule_term(schedule_context.next_section_label or "n/d")
        )
        lines.extend(
            [
                "- Séquence de programmation active :",
                "  - Note de timing : ce contexte correspond au moment où cette intervention sera diffusée, juste avant le prochain morceau.",
                f"  - Section : {spoken_section_label}",
                "  - Genres : "
                + ", ".join(
                    _french_schedule_term(label)
                    for label in schedule_context.genre_labels
                ),
                f"  - Phase : {schedule_context.phase} ({int(schedule_context.progress_ratio * 100)}%)",
                next_section_line,
            ]
        )
        if schedule_context.mode == "open":
            lines.extend(
                [
                    "  - Mode de la séquence : rotation libre, sans thème ; AzuraCast mélange des morceaux de tout le catalogue selon leurs poids.",
                    "  - Si tu mentionnes cette rotation libre, ajoute une courte proposition indiquant que tous les genres ou croisements du catalogue peuvent passer.",
                ]
            )
        elif schedule_context.playlist_name:
            lines.append(
                f"  - Mode de la séquence : playlist fixe ({schedule_context.playlist_name})."
            )

        if schedule_context.mention_intent == "start":
            lines.extend(
                [
                    "- Guide de mention de la grille : ce texte passe à la limite de début de la séquence ; présenter la section comme commençant maintenant, juste avant son premier morceau, et citer 1 à 2 genres qui la définissent.",
                    "- Variation de formulation : varier naturellement le nom employé, par exemple séquence, segment, section, partie ou tranche, au lieu de toujours répéter le même.",
                ]
            )
        elif schedule_context.mention_intent == "mid":
            lines.extend(
                [
                    "- Guide de mention de la grille : inclure une courte proposition naturelle disant que nous sommes dans cette section maintenant, avec 1 à 2 genres qui la représentent.",
                    "- Variation de formulation : alterner naturellement séquence, segment, section, partie ou tranche pour éviter les répétitions.",
                    "- Style : intégrer la mention dans le flux de l'archétype choisi, pas comme annonce séparée ; ces rappels sont occasionnels, environ toutes les 2 à 3 interventions, il faut donc l'inclure cette fois.",
                    "- Règle obligatoire de milieu de séquence : traiter la séquence comme ACTUELLEMENT EN COURS ; ne pas dire ni suggérer qu'elle se ferme, se termine ou va changer.",
                    "- Formulation préférée : employer le présent et une orientation de continuité, par exemple « on reste dans... », « nous sommes dans... », « ici, dans... ».",
                ]
            )
        else:
            lines.append(
                "- Guide de mention de la grille : facultatif ; éviter de répéter les mentions de section."
            )

    def append_focus_sequence(kind: str, focus: str, subject: str) -> None:
        focus_label = (
            "actuel, le morceau qui vient de passer"
            if focus == "current"
            else "suivant, le morceau qui va passer maintenant"
        )
        lines.extend(
            [
                f"- {kind} focus mode, obligatoire pour cet archétype : {focus_label}",
                f"- {kind} séquence orale obligatoire, suivre exactement cet ordre narratif :",
                "  - 1) Dire naturellement que le morceau actuel vient de passer.",
            ]
        )
        if focus == "current":
            lines.extend(
                [
                    f"  - 2) {subject} autour du morceau actuel.",
                    "  - 3) Finir en présentant le prochain morceau.",
                ]
            )
        else:
            lines.extend(
                [
                    "  - 2) Dire quel est le prochain morceau.",
                    f"  - 3) {subject} autour du prochain morceau.",
                    "  - 4) Finir par une transition courte et naturelle vers ce morceau.",
                ]
            )

    if short_story_focus in {"current", "next"}:
        append_focus_sequence(
            "Short-story", short_story_focus, "Raconter l'approfondissement ou l'histoire"
        )
    if album_spotlight_focus in {"current", "next"}:
        append_focus_sequence(
            "Album-spotlight",
            album_spotlight_focus,
            "Élargir le regard à son album en privilégiant le disque à la biographie générale",
        )
    if era_snapshot_lane:
        lines.extend(
            [
                "- Era-snapshot lane, obligatoire pour cet archétype : "
                + _FRENCH_LANES.get(era_snapshot_lane, era_snapshot_lane),
                "- Format cible de l'era-snapshot : contexte large mais plus léger qu'un deep-dive, environ 260 à 420 mots.",
            ]
        )
    if era_snapshot_focus in {"current", "next"}:
        append_focus_sequence(
            "Era-snapshot",
            era_snapshot_focus,
            "Raconter l'instantané de l'époque ou de la scène selon l'axe indiqué",
        )
    if deep_dive_lane:
        lines.extend(
            [
                "- Deep-dive lane, obligatoire pour cet archétype : "
                + _FRENCH_LANES.get(deep_dive_lane, deep_dive_lane),
                "- Format cible du deep-dive : récit long de 3 à 5 minutes, environ 420 à 700 mots.",
            ]
        )
    if deep_dive_focus in {"current", "next"}:
        append_focus_sequence(
            "Deep-dive", deep_dive_focus, "Raconter le deep-dive selon l'axe indiqué"
        )

    lines.extend(
        [
            f"- Langue de sortie du texte parlé : {locale.tag} ({locale.output_language})",
            f"- Instruction de langue obligatoire et prioritaire : {locale.script_guidance}",
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
    archetype_policy: Optional[ResolvedArchetypeProfile] = None,
) -> str:
    locale = _resolved_locale(locale)
    profile = archetype_policy or get_archetype_policy_registry().profiles["base"]
    if archetype == Archetype.NEWS:
        now_utc = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
        news_policy = profile.for_archetype(Archetype.NEWS).news
        if news_policy is None:
            raise ValueError("The news archetype requires a news policy.")
        selected_news_topic_ids = list(news_topics or news_policy.topic_ids)
        selected_news_topics = [
            f"{topic_id} ({profile.news_topic_label(topic_id, locale.tag)})"
            for topic_id in selected_news_topic_ids
        ]
        max_age_hours = news_policy.max_age_hours
        preferred_max_age_hours = news_policy.preferred_max_age_hours
        wrapper = get_prompt_template_from(
            locale.prompt_directory, "wrapper_news"
        ).replace("es-AR", locale.tag).format(
            story_count=story_count or 1,
            news_topics=", ".join(selected_news_topics),
            news_topic_ids=", ".join(selected_news_topic_ids),
            news_max_age_hours=max_age_hours,
            news_preferred_max_age_hours=preferred_max_age_hours,
            news_now_utc=now_utc.isoformat().replace("+00:00", "Z"),
            news_cutoff_utc=(
                now_utc - dt.timedelta(hours=max_age_hours)
            ).isoformat().replace("+00:00", "Z"),
            news_preferred_cutoff_utc=(
                now_utc - dt.timedelta(hours=preferred_max_age_hours)
            ).isoformat().replace("+00:00", "Z"),
        )
    elif archetype == Archetype.CONCERT_CHECK:
        concert_policy = profile.for_archetype(Archetype.CONCERT_CHECK).concert_check
        if concert_policy is None:
            raise ValueError("The concert_check archetype requires a concert policy.")
        country_codes = concert_policy.country_codes
        concert_countries = ", ".join(
            f"{profile.concert_country_label(code, locale.tag)} ({code})"
            for code in country_codes
        )
        wrapper = get_prompt_template_from(
            locale.prompt_directory, "wrapper_concert_check"
        ).replace("es-AR", locale.tag).format(
            concert_countries=concert_countries,
            concert_country_codes="|".join(country_codes),
        )
    else:
        template_name = {
            Archetype.BACK_SELL: "wrapper_back_sell",
            Archetype.UP_NEXT_TEASE: "wrapper_up_next_tease",
            Archetype.DEEP_DIVE: "wrapper_deep_dive",
            Archetype.SHORT_STORY: "wrapper_short_story",
            Archetype.ALBUM_SPOTLIGHT: "wrapper_album_spotlight",
            Archetype.ERA_SNAPSHOT: "wrapper_era_snapshot",
            Archetype.BLOCK_INTRO: "wrapper_block_intro",
            Archetype.ULTRA_MINIMAL: "wrapper_ultra_minimal",
        }.get(archetype, "wrapper_ultra_minimal")
        wrapper = get_prompt_template_from(
            locale.prompt_directory, template_name
        ).replace("es-AR", locale.tag)

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

    language_heading = (
        "RÈGLE DE LANGUE (priorité absolue) :"
        if locale.tag == "fr-CH"
        else "LANGUAGE OVERRIDE (highest priority):"
    )
    return f"{wrapper}\n\n{shared_input}\n\n{language_heading}\n{locale.script_guidance}\n"


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
    personality_guide = load_personality_guide_from(locale.prompt_directory)
    host_constitution = get_prompt_template_from(
        locale.prompt_directory, "host_constitution"
    )
    script_style_baseline = get_prompt_template_from(
        locale.prompt_directory, "script_style_baseline"
    )
    personality_heading = (
        "Profil de personnalité de la station :"
        if locale.tag == "fr-CH"
        else "Perfil de personalidad de la estacion:"
    )
    language_heading = (
        "Règle de langue (priorité absolue) :"
        if locale.tag == "fr-CH"
        else "Language rule (highest priority):"
    )
    return (
        f"{host_constitution.format(station_name=station_name).strip()}\n\n"
        f"{personality_guide}\n\n"
        f"{script_style_baseline.strip()}\n\n"
        f"{personality_heading}\n"
        f"- {personality.script_profile}\n\n"
        f"{language_heading}\n"
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
