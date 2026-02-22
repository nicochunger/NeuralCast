"""Configuration constants and prompt template loading for host orchestrator."""

from __future__ import annotations

import logging
import pathlib
import re
from functools import lru_cache
from typing import Any, Dict, Tuple
from zoneinfo import ZoneInfo

from neuralcast.config import ASSETS_ROOT
from neuralcast.pipelines.host_orchestrator_models import Archetype, StationPersonality

STORY_ROOT = ASSETS_ROOT / "stories"
STORY_OUTPUT_DIR = STORY_ROOT / "snippets"
PROMPTS_DIR = STORY_ROOT / "prompts"
TTS_INSTRUCTIONS_PATH = PROMPTS_DIR / "tts_instructions.md"
PERSONALITY_GUIDE_PATH = PROMPTS_DIR / "personality.md"

STATE_VERSION = 1
STATE_FILENAME = "ai_host_orchestrator_state.json"
SCHEDULE_STATE_FILENAME = "ai_schedule_state.json"
LOCK_FILENAME = "ai_host_orchestrator.lock"
LOCK_STALE_SECONDS = 10 * 60

LEAD_TIME_SECONDS = 90
SPEAK_DEADLINE_MINUTES = 45
WAIT_RANGE_SONGS = (2, 5)
NEWS_MAX_AGE_HOURS = 7 * 24
NEWS_PREFERRED_MAX_AGE_HOURS = 72
NEWS_DUPLICATE_WINDOW_DAYS = 7
NEWS_DEDUP_MAX_ENTRIES = 50
RECENT_SCRIPT_MEMORY_SIZE = 3
SCHEDULE_START_WINDOW_MINUTES = 10
SCHEDULE_MID_PROGRESS_RANGE = (0.40, 0.70)
SCHEDULE_MENTION_RETENTION_DAYS = 14
SCHEDULE_MENTION_MAX_ENTRIES = 512

SYSTEM_TZ = ZoneInfo("Europe/Zurich")

LOGGER = logging.getLogger("host_orchestrator")


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


ANGLE_OPTIONS: Dict[Archetype, Tuple[str, ...]] = {
    Archetype.BACK_SELL: (
        "Minimalist",
        "Connector",
        "Fanatic",
    ),
}

WEIGHTED_ARCHETYPES: Dict[Archetype, float] = {
    Archetype.BACK_SELL: 0.55,
    Archetype.DEEP_DIVE: 0.20,
    Archetype.NEWS: 0.15,
    Archetype.CONCERT_CHECK: 0.10,
}

COOLDOWN_SECONDS: Dict[Archetype, int] = {
    Archetype.BACK_SELL: 30 * 60,
    Archetype.DEEP_DIVE: 60 * 60,
    Archetype.NEWS: 120 * 60,
    Archetype.CONCERT_CHECK: 180 * 60,
}

TEMPERATURE_TOP_P_RANGES: Dict[
    Archetype, Tuple[Tuple[float, float], Tuple[float, float]]
] = {
    Archetype.BACK_SELL: ((0.4, 0.7), (0.7, 0.9)),
    Archetype.DEEP_DIVE: ((1.0, 1.5), (0.9, 0.98)),
    # NEWS uses a strict grounded + structured contract; lower variance is more reliable.
    Archetype.NEWS: ((0.45, 0.85), (0.88, 0.95)),
    Archetype.CONCERT_CHECK: ((0.6, 1.0), (0.85, 0.95)),
    Archetype.BLOCK_INTRO: ((0.4, 0.7), (0.75, 0.9)),
    Archetype.ULTRA_MINIMAL: ((0.3, 0.6), (0.7, 0.9)),
}

HOOKS_BY_ARCHETYPE: Dict[Archetype, Tuple[str, ...]] = {
    Archetype.BACK_SELL: (
        "detalle del cierre que quedó sonando",
        "textura que dejó el tema",
        "contraste suave con lo que viene",
        "pulso que sigue pero cambia",
        "observación corta del tema y puente",
        "ese final todavía flotando",
        "un matiz del cierre para enlazar",
        "lo que quedó en el aire y hacia dónde va",
        "puente por dinámica, no por hype",
        "cierre del tema y giro al próximo",
    ),
    Archetype.DEEP_DIVE: (
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
    Archetype.BACK_SELL: 0.40,
    Archetype.DEEP_DIVE: 0.35,
    Archetype.NEWS: 0.25,
    Archetype.CONCERT_CHECK: 0.25,
    Archetype.BLOCK_INTRO: 0.30,
    Archetype.ULTRA_MINIMAL: 0.20,
}

NEWS_TOPICS: Tuple[str, ...] = (
    "Tech/AI",
    "Absurd/odd",
    "Argentina (politics/general)",
    "Switzerland (general)",
    "Important global news",
)

CONCERT_TARGET_COUNTRIES: Tuple[str, ...] = ("Argentina", "Switzerland")
CONCERT_COUNTRY_ALIASES: Dict[str, str] = {
    "argentina": "argentina",
    "ar": "argentina",
    "arg": "argentina",
    "switzerland": "switzerland",
    "swiss": "switzerland",
    "suiza": "switzerland",
    "ch": "switzerland",
    "schweiz": "switzerland",
}
CONCERT_TARGET_COUNTRY_KEYS = frozenset({"argentina", "switzerland"})

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
    "wrapper_deep_dive": "wrapper_deep_dive.md",
    "wrapper_news": "wrapper_news.md",
    "wrapper_concert_check": "wrapper_concert_check.md",
    "wrapper_block_intro": "wrapper_block_intro.md",
    "wrapper_ultra_minimal": "wrapper_ultra_minimal.md",
    "repair_news_contract": "repair_news_contract.md",
    "repair_concert_contract": "repair_concert_contract.md",
}


@lru_cache(maxsize=1)
def load_prompt_templates() -> Dict[str, str]:
    templates: Dict[str, str] = {}
    for template_name, filename in PROMPT_TEMPLATE_FILES.items():
        path = PROMPTS_DIR / filename
        if not path.is_file():
            raise FileNotFoundError(f"Missing prompt template file: {path}")
        templates[template_name] = path.read_text(encoding="utf-8").strip()
    return templates


@lru_cache(maxsize=1)
def load_personality_guide() -> str:
    if not PERSONALITY_GUIDE_PATH.is_file():
        raise FileNotFoundError(
            f"Missing personality guide file: {PERSONALITY_GUIDE_PATH}"
        )
    return PERSONALITY_GUIDE_PATH.read_text(encoding="utf-8").strip()


def get_prompt_template(template_name: str, **template_vars: Any) -> str:
    templates = load_prompt_templates()
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
WRAPPER_DEEP_DIVE = get_prompt_template("wrapper_deep_dive")
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
        tts_profile=(""),
    ),
    "neuralforge": StationPersonality(
        script_profile=(
            "NeuralForge script profile: "
            "mantener la naturalidad, espontaneidad y credibilidad conversacional del estilo base, "
            "con energia firme, enfoque directo y presencia segura, sin sobreactuacion. "
            "El tono debe sentirse decidido y vivo, nunca caricaturesco ni gritado. "
            "Usar frases mas compactas y activas, con empuje controlado y precision radial. "
            "Evitar cliches de metal y metaforas gastadas (por ejemplo, acero, voltaje, fuego, rugir), "
            "salvo que haya un dato concreto del track que las justifique. "
            "Conservar transiciones humanas y claras hacia el siguiente tema, sin sonar teatral."
        ),
        tts_profile=(""),
    ),
}

STATION_GENERATION_NAMES: Dict[str, str] = {
    "neuralcast": "NéuralCast",
    "neuralforge": "NéuralForsh",
}
