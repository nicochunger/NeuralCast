"""Configuration constants and prompt template loading for host orchestrator."""

from __future__ import annotations

import logging
import pathlib
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Dict, Optional, Tuple
from zoneinfo import ZoneInfo

from neuralcast.config import ASSETS_ROOT, DEFAULT_TIMEZONE_NAME, LOGS_ROOT
from .archetype_policies import (
    ResolvedArchetypeProfile,
    get_archetype_policy_registry,
)
from .models import Archetype, StationPersonality

_ARCHETYPE_POLICY_REGISTRY = get_archetype_policy_registry()
_BASE_ARCHETYPE_POLICY = _ARCHETYPE_POLICY_REGISTRY.profiles["base"]

STORY_ROOT = ASSETS_ROOT / "stories"
STORY_OUTPUT_DIR = STORY_ROOT / "snippets"
NEURALCAST_AI_SNIPPET_COVER_PATH = (
    ASSETS_ROOT / "images" / "generated" / "neuralcast_ai_snippets_cover.png"
)
NEURALFORGE_AI_SNIPPET_COVER_PATH = (
    ASSETS_ROOT / "images" / "generated" / "neuralforge_ai_snippets_cover.png"
)
AI_SNIPPET_COVER_PATH_BY_STATION = {
    "neuralcast": NEURALCAST_AI_SNIPPET_COVER_PATH,
    "neuralforge": NEURALFORGE_AI_SNIPPET_COVER_PATH,
}
PROMPTS_DIR = STORY_ROOT / "prompts"
TTS_INSTRUCTIONS_PATH = PROMPTS_DIR / "tts_instructions.md"
PERSONALITY_GUIDE_PATH = PROMPTS_DIR / "personality.md"

STATE_VERSION = 2
STATE_FILENAME = "ai_host_orchestrator_state.json"
SCHEDULE_STATE_FILENAME = "ai_schedule_state.json"
LOCK_FILENAME = "ai_host_orchestrator.lock"
ORCHESTRATOR_LOG_FILENAME = "ai_host_orchestrator.log"
ORCHESTRATOR_SEGMENT_EVENTS_LOG_FILENAME = "ai_host_orchestrator_segments.log"
LOCK_STALE_SECONDS = 10 * 60

LEAD_TIME_SECONDS = 90
ARCHETYPE_LEAD_TIME_SECONDS: Dict[Archetype, int] = {
    archetype: policy.lead_time_seconds
    for archetype, policy in _BASE_ARCHETYPE_POLICY.archetypes.items()
    if policy.lead_time_seconds != LEAD_TIME_SECONDS
}
SPEAK_DEADLINE_MINUTES = 45
WAIT_RANGE_SONGS = (2, 5)
NEURALCAST_WAIT_RANGE_SONGS = (7, 12)
NEURALCAST_SPEAK_DEADLINE_MINUTES = 120
NEURALCAST_COOLDOWN_MULTIPLIER = 2.0
_BASE_NEWS_POLICY = _BASE_ARCHETYPE_POLICY.for_archetype(Archetype.NEWS).news
assert _BASE_NEWS_POLICY is not None
NEWS_MAX_AGE_HOURS = _BASE_NEWS_POLICY.max_age_hours
NEWS_PREFERRED_MAX_AGE_HOURS = _BASE_NEWS_POLICY.preferred_max_age_hours
NEWS_DUPLICATE_WINDOW_DAYS = 7
NEWS_DEDUP_MAX_ENTRIES = 50
RECENT_SCRIPT_MEMORY_SIZE = 3
SCHEDULE_START_WINDOW_MINUTES = 10
SCHEDULE_BLOCK_INTRO_LOOKAHEAD_MINUTES = 10
SCHEDULE_BLOCK_INTRO_CONFIRMATION_TRACKS = 3
SCHEDULE_BLOCK_INTRO_BOUNDARY_GRACE_SECONDS = 90
SCHEDULE_BLOCK_INTRO_LATE_START_WINDOW_MINUTES = 10
SCHEDULE_MID_PROGRESS_RANGE = (0.40, 0.70)
SCHEDULE_MENTION_RETENTION_DAYS = 14
SCHEDULE_MENTION_MAX_ENTRIES = 512
UP_NEXT_TEASE_MIN_SECONDS_BEFORE_BLOCK_CHANGE = 20 * 60

SYSTEM_TZ = ZoneInfo(DEFAULT_TIMEZONE_NAME)

LOGGER = logging.getLogger("host_orchestrator")
SEGMENT_EVENTS_LOGGER = logging.getLogger("host_orchestrator.segments")

HOST_ARTIST_NAME = "NueralHost"


@dataclass(frozen=True)
class StationCadenceSettings:
    wait_range_songs: Tuple[int, int]
    speak_deadline_minutes: int
    cooldown_multiplier: float = 1.0


DEFAULT_CADENCE_SETTINGS = StationCadenceSettings(
    wait_range_songs=WAIT_RANGE_SONGS,
    speak_deadline_minutes=SPEAK_DEADLINE_MINUTES,
)
DEFAULT_ARCHETYPE_SETTINGS = _BASE_ARCHETYPE_POLICY
STATION_CADENCE_SETTINGS: Dict[str, StationCadenceSettings] = {
    "neuralcast": StationCadenceSettings(
        wait_range_songs=NEURALCAST_WAIT_RANGE_SONGS,
        speak_deadline_minutes=NEURALCAST_SPEAK_DEADLINE_MINUTES,
        cooldown_multiplier=NEURALCAST_COOLDOWN_MULTIPLIER,
    ),
}
STATION_ARCHETYPE_SETTINGS: Dict[str, ResolvedArchetypeProfile] = {
    "neuralcast": _ARCHETYPE_POLICY_REGISTRY.profiles["neuralcast"],
    "neuralforge": _ARCHETYPE_POLICY_REGISTRY.profiles["neuralforge"],
}


def cadence_settings_for_station(station_slug: str) -> StationCadenceSettings:
    return STATION_CADENCE_SETTINGS.get(
        str(station_slug or "").strip().lower(),
        DEFAULT_CADENCE_SETTINGS,
    )


def archetype_settings_for_station(station_slug: str) -> ResolvedArchetypeProfile:
    return STATION_ARCHETYPE_SETTINGS.get(
        str(station_slug or "").strip().lower(),
        DEFAULT_ARCHETYPE_SETTINGS,
    )


def cooldown_seconds_for_archetype(
    archetype: Archetype,
    cadence_settings: Optional[StationCadenceSettings] = None,
    archetype_policy: Optional[ResolvedArchetypeProfile] = None,
) -> int:
    profile = archetype_policy or DEFAULT_ARCHETYPE_SETTINGS
    base_cooldown = profile.for_archetype(archetype).cooldown_seconds
    settings = cadence_settings or DEFAULT_CADENCE_SETTINGS
    return max(0, int(round(base_cooldown * settings.cooldown_multiplier)))


def lead_time_seconds_for_archetype(
    archetype: Archetype,
    archetype_policy: Optional[ResolvedArchetypeProfile] = None,
) -> int:
    profile = archetype_policy or DEFAULT_ARCHETYPE_SETTINGS
    return profile.for_archetype(archetype).lead_time_seconds


def configure_logging(level: int = logging.INFO) -> None:
    if LOGGER.handlers:
        return

    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s | %(name)s | %(levelname)-7s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    LOGGER.addHandler(handler)
    LOGGER.setLevel(level)
    LOGGER.propagate = False


def _has_file_handler(logger: logging.Logger, target_path: pathlib.Path) -> bool:
    resolved = str(target_path.resolve())
    for handler in logger.handlers:
        if isinstance(handler, logging.FileHandler):
            try:
                if str(pathlib.Path(handler.baseFilename).resolve()) == resolved:
                    return True
            except Exception:  # noqa: BLE001
                continue
    return False


def configure_station_file_logging(
    metadata_dir: pathlib.Path,
    *,
    level: int = logging.INFO,
) -> Tuple[pathlib.Path, pathlib.Path]:
    if metadata_dir.parent.name == "host_channels":
        station_name = metadata_dir.name.strip().lower()
    else:
        station_name = metadata_dir.parent.name.strip().lower()
    station_name = station_name or "unknown"
    station_logs_dir = LOGS_ROOT / "host_orchestrator" / station_name
    station_logs_dir.mkdir(parents=True, exist_ok=True)
    main_log_path = station_logs_dir / ORCHESTRATOR_LOG_FILENAME
    segment_log_path = station_logs_dir / ORCHESTRATOR_SEGMENT_EVENTS_LOG_FILENAME

    if not _has_file_handler(LOGGER, main_log_path):
        file_handler = logging.FileHandler(main_log_path, encoding="utf-8")
        file_handler.setFormatter(
            logging.Formatter(
                fmt="%(asctime)s | %(name)s | %(levelname)-7s | %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        LOGGER.addHandler(file_handler)
    LOGGER.setLevel(level)

    if not _has_file_handler(SEGMENT_EVENTS_LOGGER, segment_log_path):
        segment_handler = logging.FileHandler(segment_log_path, encoding="utf-8")
        segment_handler.setFormatter(
            logging.Formatter(
                fmt="%(asctime)s | %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        SEGMENT_EVENTS_LOGGER.addHandler(segment_handler)
    SEGMENT_EVENTS_LOGGER.setLevel(level)
    SEGMENT_EVENTS_LOGGER.propagate = False

    return main_log_path, segment_log_path


def log_segment_event(
    *,
    station: str,
    archetype: str,
    current_track: str,
    next_track: str,
    queued_request_id: Optional[str],
    expected_play_at_utc: Optional[str],
    audio_path: str,
    remote_path: str,
    schedule_section: Optional[str],
    mention_intent: Optional[str],
    news_topics: Optional[str] = None,
    segment_title: Optional[str] = None,
) -> None:
    parts = [
        f"station={station}",
        f"archetype={archetype}",
        f"title={segment_title or 'n/a'}",
        f"current={current_track}",
        f"next={next_track}",
        f"request_id={queued_request_id or 'n/a'}",
        f"expected_play_at_utc={expected_play_at_utc or 'n/a'}",
        f"schedule_section={schedule_section or 'n/a'}",
        f"mention_intent={mention_intent or 'none'}",
        f"audio={audio_path}",
        f"remote={remote_path}",
    ]
    if news_topics:
        parts.append(f"news_topics={news_topics}")
    SEGMENT_EVENTS_LOGGER.info(" | ".join(parts))

ANGLE_OPTIONS: Dict[Archetype, Tuple[str, ...]] = {
    Archetype.BACK_SELL: (
        "Minimalist",
        "Connector",
        "Fanatic",
    ),
    Archetype.UP_NEXT_TEASE: (
        "Back-sell + bloque que sigue",
        "Nombrar 2-3 bandas y quedarse",
        "Puente corto con tease casual",
    ),
}

WEIGHTED_ARCHETYPES: Dict[Archetype, float] = {
    archetype: policy.weight
    for archetype, policy in _BASE_ARCHETYPE_POLICY.archetypes.items()
    if policy.automatic
}

COOLDOWN_SECONDS: Dict[Archetype, int] = {
    archetype: policy.cooldown_seconds
    for archetype, policy in _BASE_ARCHETYPE_POLICY.archetypes.items()
    if policy.automatic
}

TEMPERATURE_TOP_P_RANGES: Dict[
    Archetype, Tuple[Tuple[float, float], Tuple[float, float]]
] = {
    archetype: (policy.temperature_range, policy.top_p_range)
    for archetype, policy in _BASE_ARCHETYPE_POLICY.archetypes.items()
}

HOOKS_BY_ARCHETYPE: Dict[Archetype, Tuple[str, ...]] = {
    Archetype.BACK_SELL: (
        "detalle del cierre que quedó sonando",
        "textura que dejó el tema",
        "contraste suave con lo que viene",
        "continuidad con un giro suave",
        "observación corta del tema y puente",
        "ese final todavía flotando",
        "un matiz del cierre para enlazar",
        "lo que quedó en el aire y hacia dónde va",
        "puente por dinámica, no por hype",
        "cierre del tema y giro al próximo",
    ),
    Archetype.UP_NEXT_TEASE: (
        "cierre corto y adelanto de las bandas que siguen",
        "nombrar rapido el tramo que viene y quedarse ahi",
        "puente casual con dos o tres nombres del bloque",
        "mostrar el hilo del bloque sin sonar listado",
        "encadenar artistas en modo charla y seguir",
        "pase rapido con efecto de bloque en vivo",
        "vender continuidad del tramo sin sobreexplicar",
        "dejar ganas con una mini secuencia de bandas",
        "pasar de este cierre a lo que sigue con naturalidad",
        "invitar a quedarse por la seguidilla que viene",
    ),
    Archetype.SHORT_STORY: (
        "detalle chico que cambia la escucha",
        "historia breve detrás del tema",
        "contexto de época en una punta concreta",
        "zoom a un gesto sonoro",
        "dato puntual y lectura corta",
        "por qué este tema pega distinto con contexto",
        "una capa más sin volverse ensayo",
        "momento de la banda en esa etapa",
        "detalle de producción si aparece",
        "lectura interpretativa breve si faltan datos",
    ),
    Archetype.ALBUM_SPOTLIGHT: (
        "el disco alrededor de este tema",
        "por que ese album cambia la escucha",
        "identidad del album en una postal corta",
        "como entra este tema dentro del disco",
        "mood general del album y su peso",
        "una lectura del album sin hacer reseña",
        "el momento de la banda en ese disco",
        "textura y concepto del album en pocas lineas",
        "que tiene ese album que hace pegar distinto este tema",
        "mirada corta al album y pase natural",
    ),
    Archetype.ERA_SNAPSHOT: (
        "postal de epoca alrededor del tema",
        "que estaba cambiando en esa escena",
        "momento del genero sin hacer documental",
        "la banda metida en ese clima historico",
        "foto corta de la epoca y su sonido",
        "lo que estaba pasando alrededor de ese lanzamiento",
        "escena, contexto y por que importa para escuchar esto",
        "un corte de epoca con hilo musical",
        "movimiento de escena y aterrizaje al tema",
        "contexto amplio pero vivo, con cierre al proximo",
    ),
    Archetype.DEEP_DIVE: (
        "historia larga de banda con arco claro",
        "origen, quiebre y legado en modo narrativo",
        "mini documental radial sobre una etapa",
        "cronologia viva con puntos de giro",
        "debut, reinvencion y momento bisagra",
        "historia de album con contexto de epoca",
        "genealogia de cancion y su evolucion en vivo",
        "trama de escena y como encaja la banda",
        "version larga con clima de radio nocturna",
        "relato profundo con cierre al proximo tema",
    ),
    Archetype.NEWS: (
        "mini paneo útil y volvemos a la música",
        "una que importa hoy, sin tono de boletín",
        "titular corto con por qué importa",
        "corte de actualidad en tono cercano",
        "según medio, dato clave y vuelta al aire",
        "dos titulares como charla, no noticiero",
        "actualidad en limpio y sin alarmismo",
        "te marco una rápida y seguimos",
        "resumen breve con puente cálido",
        "mundo afuera por un minuto y volvemos",
    ),
    Archetype.CONCERT_CHECK: (
        "mirada rápida a fechas de los dos artistas",
        "si hay show cerca, decir quién, cuándo y dónde",
        "agenda útil en tono de radio",
        "chequeo corto de tour y vuelta a música",
        "si se vienen fechas por acá",
        "una o dos fechas fuertes, sin base de datos",
        "fecha primero, después ciudad y venue",
        "radar de shows con cierre al próximo tema",
        "cruce de calendario sin relleno",
        "práctico y cercano, nada de listado seco",
    ),
    Archetype.BLOCK_INTRO: (
        "arranque de bloque con clima sonoro",
        "qué sección entra y qué se viene",
        "presentación corta del tramo actual",
        "marcar género o clima y soltar a la música",
        "entrada cálida sin tono de anuncio",
        "orientar rápido y dejar correr el bloque",
        "abrir tramo con una pista sonora concreta",
    ),
    Archetype.ULTRA_MINIMAL: (
        "paso corto al próximo tema",
        "una sola cláusula y seguimos",
        "nombrar tema y salir",
        "casi sin pausa, pero humano",
        "puente mínimo con cierre breve",
        "directo al próximo, sin vueltas",
        "entrada corta, sin metáfora",
        "seguir en una línea simple",
        "presentar y dejar aire",
        "micro-pase y música",
    ),
}

# Probability that a segment receives no hook cue at all, allowing a free opener.
HOOK_FREE_OPEN_PROB_BY_ARCHETYPE: Dict[Archetype, float] = {
    archetype: policy.hook_free_probability
    for archetype, policy in _BASE_ARCHETYPE_POLICY.archetypes.items()
}

NEWS_TOPICS: Tuple[str, ...] = tuple(
    _BASE_ARCHETYPE_POLICY.news_topic_label(topic_id, "en")
    for topic_id in _BASE_NEWS_POLICY.topic_ids
)

_BASE_CONCERT_POLICY = _BASE_ARCHETYPE_POLICY.for_archetype(
    Archetype.CONCERT_CHECK
).concert_check
assert _BASE_CONCERT_POLICY is not None
CONCERT_TARGET_COUNTRIES: Tuple[str, ...] = tuple(
    _BASE_ARCHETYPE_POLICY.concert_country_label(country_code, "en")
    for country_code in _BASE_CONCERT_POLICY.country_codes
)
CONCERT_COUNTRY_ALIASES: Dict[str, str] = {
    alias: country_code
    for country_code, definition in _BASE_ARCHETYPE_POLICY.concert_countries.items()
    for alias in definition.aliases
}
CONCERT_TARGET_COUNTRY_KEYS = frozenset(_BASE_CONCERT_POLICY.country_codes)

BANNED_OPENERS: Tuple[str, ...] = (
    "Alright folks",
    "Hope you're having a great day",
    "Bueno gente",
    "Hola a todos",
    "Querida audiencia",
)
OVERUSED_STYLE_CLICHES: Tuple[str, ...] = (
    "acero",
    "voltaje",
    "fuego",
    "rugir",
    "tormenta",
    "explosion",
    "incendio",
    "pulso",
)

GENERATION_RETRIES = 2
GENERATION_RETRY_DELAYS = (2, 5)

STRUCTURED_OUTPUT_RE = re.compile(
    r"\bSCRIPT\s*:\s*(?P<script>.*?)\bMETA\s*\(JSON\)\s*:\s*(?P<meta>\{.*\})\s*$",
    flags=re.IGNORECASE | re.DOTALL,
)

NEWS_OUTPUT_RE = STRUCTURED_OUTPUT_RE
CONCERT_OUTPUT_RE = STRUCTURED_OUTPUT_RE

PROMPT_TEMPLATE_FILES: Dict[str, str] = {
    "host_constitution": "host_constitution.md",
    "script_style_baseline": "script_style_baseline.md",
    "wrapper_back_sell": "wrapper_back_sell.md",
    "wrapper_up_next_tease": "wrapper_up_next_tease.md",
    "wrapper_deep_dive": "wrapper_deep_dive.md",
    "wrapper_short_story": "wrapper_short_story.md",
    "wrapper_album_spotlight": "wrapper_album_spotlight.md",
    "wrapper_era_snapshot": "wrapper_era_snapshot.md",
    "wrapper_news": "wrapper_news.md",
    "wrapper_concert_check": "wrapper_concert_check.md",
    "wrapper_block_intro": "wrapper_block_intro.md",
    "wrapper_ultra_minimal": "wrapper_ultra_minimal.md",
    "repair_news_contract": "repair_news_contract.md",
    "repair_concert_contract": "repair_concert_contract.md",
}


@lru_cache(maxsize=None)
def load_prompt_templates_from(prompt_directory: pathlib.Path) -> Dict[str, str]:
    templates: Dict[str, str] = {}
    for template_name, filename in PROMPT_TEMPLATE_FILES.items():
        path = prompt_directory / filename
        if not path.is_file():
            raise FileNotFoundError(f"Missing prompt template file: {path}")
        templates[template_name] = path.read_text(encoding="utf-8").strip()
    return templates


def load_prompt_templates() -> Dict[str, str]:
    return load_prompt_templates_from(PROMPTS_DIR)


@lru_cache(maxsize=None)
def load_personality_guide_from(prompt_directory: pathlib.Path) -> str:
    personality_path = prompt_directory / "personality.md"
    if not personality_path.is_file():
        raise FileNotFoundError(
            f"Missing personality guide file: {personality_path}"
        )
    return personality_path.read_text(encoding="utf-8").strip()


def load_personality_guide() -> str:
    return load_personality_guide_from(PROMPTS_DIR)


def get_prompt_template(template_name: str, **template_vars: Any) -> str:
    return _render_prompt_template(
        load_prompt_templates(), template_name, template_vars
    )


def get_prompt_template_from(
    prompt_directory: pathlib.Path,
    template_name: str,
    **template_vars: Any,
) -> str:
    templates = load_prompt_templates_from(prompt_directory)
    return _render_prompt_template(templates, template_name, template_vars)


def _render_prompt_template(
    templates: Dict[str, str],
    template_name: str,
    template_vars: Dict[str, Any],
) -> str:
    if template_name not in templates:
        available = ", ".join(sorted(templates))
        raise KeyError(
            f"Unknown prompt template '{template_name}'. Available: {available}"
        )

    template = templates[template_name]
    if not template_vars:
        return template

    try:
        return template.format(**template_vars)
    except KeyError as exc:
        missing_key = str(exc).strip("'")
        raise KeyError(
            f"Missing template variable '{missing_key}' for prompt '{template_name}'"
        ) from exc


HOST_CONSTITUTION_TEMPLATE = get_prompt_template("host_constitution")
SCRIPT_STYLE_BASELINE = get_prompt_template("script_style_baseline")
WRAPPER_BACK_SELL = get_prompt_template("wrapper_back_sell")
WRAPPER_UP_NEXT_TEASE = get_prompt_template("wrapper_up_next_tease")
WRAPPER_DEEP_DIVE = get_prompt_template("wrapper_deep_dive")
WRAPPER_SHORT_STORY = get_prompt_template("wrapper_short_story")
WRAPPER_ALBUM_SPOTLIGHT = get_prompt_template("wrapper_album_spotlight")
WRAPPER_ERA_SNAPSHOT = get_prompt_template("wrapper_era_snapshot")
WRAPPER_NEWS = get_prompt_template("wrapper_news")
WRAPPER_CONCERT_CHECK = get_prompt_template("wrapper_concert_check")
WRAPPER_BLOCK_INTRO = get_prompt_template("wrapper_block_intro")
WRAPPER_ULTRA_MINIMAL = get_prompt_template("wrapper_ultra_minimal")
REPAIR_NEWS_CONTRACT = get_prompt_template("repair_news_contract")
REPAIR_CONCERT_CONTRACT = get_prompt_template("repair_concert_contract")


STATION_PERSONALITIES: Dict[str, StationPersonality] = {
    "neuralcast": StationPersonality(
        script_profile=(
            "NeuralCast script profile: "
            "tono natural, calmo y espontaneo, como alguien hablando en vivo desde estudio de radio. "
            "Voz serena, madura, levemente nostalgica y autentica; no dramatizar ni sobreactuar. "
            "En transiciones, reconocer de forma organica que el tema ya termino, "
            "sin depender siempre de la misma formula de apertura. "
            "Cerrar de forma calida y no robotica presentando el proximo track. "
            "Permitir micro-muletillas sutiles (bueno, viste, mira, no se) solo si salen naturales. "
            "Sonar como conversacion real, con criterio musical y elegancia relajada."
        ),
        tts_profile=(
            "NeuralCast TTS profile: "
            "acento rioplatense natural, calido y sereno. "
            "Energia contenida, madura y confiable; sin euforia, sin apuro y sin sobreactuar. "
            "Ritmo pausado pero vivo, con respiracion clara, voz abierta y presencia levemente nostalgica. "
            "Debe sonar como locutor real compartiendo una cancion con calma, criterio y cercania, nunca como voz corporativa."
        ),
    ),
    "neuralforge": StationPersonality(
        script_profile=(
            "NeuralForge script profile: "
            "mantener la naturalidad, espontaneidad y credibilidad conversacional del estilo base, "
            "con energia firme, enfoque directo y presencia segura, sin sobreactuacion. "
            "Host de radio de metal en espanol rioplatense, calido y cercano, con energia alta pero controlada. "
            "El tono debe sentirse decidido y vivo, nunca caricaturesco, gritado ni vendedor. "
            "Usar frases mas compactas y activas, con empuje controlado y precision radial. "
            "Escribir para la boca y no para la vista: priorizar lineas decibles, voseo consistente y ritmo oral real. "
            "Hablar desde la escucha y el detalle concreto del tema, no desde una ficha tecnica, una reseña ni una tesis sobre el metal. "
            "Puede haber micro-reformulaciones si hacen mas humano el guion, sin ensuciarlo. "
            "Evitar cliches de metal y metaforas gastadas (por ejemplo, acero, voltaje, fuego, rugir), "
            "salvo que haya un dato concreto del track que las justifique. "
            "Conservar transiciones humanas y claras hacia el siguiente tema, sin sonar teatral ni a flyer de festival."
        ),
        tts_profile=(
            "NeuralForge TTS profile: "
            "acento rioplatense natural, cercano y seguro. "
            "Energia alta pero controlada; arriba, con actitud, sin gritar ni sobreactuar. "
            "La energia debe sentirse despierta y presente, con ataque claro en las palabras, sin sonar gritado ni teatral. "
            "Marcar pausas cortas y respiracion real para que cada idea salga clara, abierta y decible. "
            "Si una linea se siente demasiado prolija o escrita, bajarle rigidez y volverla mas conversada. "
            "Debe sonar como host real de radio de metal hablando al aire, no como locutor corporativo ni personaje caricaturesco."
        ),
    ),
}

STATION_GENERATION_NAMES: Dict[str, str] = {
    "neuralcast": "NéuralCast",
    "neuralforge": "NéuralForsh",
}
