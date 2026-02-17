"""Dynamic AI host orchestrator that injects station voice segments into AzuraCast."""

from __future__ import annotations

import argparse
import base64
import csv
import datetime as dt
import json
import logging
import os
import pathlib
import random
import re
import shutil
import subprocess
import time
import unicodedata
import warnings
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv
from requests import Response
from urllib3.exceptions import InsecureRequestWarning

from neuralcast.config import ASSETS_ROOT, PROJECT_ROOT
from neuralcast.playlists.utils import sanitize_filename_component
from neuralcast.services.openai_client import get_gemini_client, synthesize_speech

STORY_ROOT = ASSETS_ROOT / "stories"
TTS_INSTRUCTIONS_PATH = STORY_ROOT / "tts_story_instructions.md"
STORY_OUTPUT_DIR = STORY_ROOT / "snippets"

STATE_VERSION = 1
STATE_FILENAME = "ai_host_orchestrator_state.json"
LOCK_FILENAME = "ai_host_orchestrator.lock"
LOCK_STALE_SECONDS = 10 * 60

LEAD_TIME_SECONDS = 90
SPEAK_DEADLINE_MINUTES = 45
WAIT_RANGE_SONGS = (2, 5)
NEWS_MAX_AGE_HOURS = 7 * 24
NEWS_PREFERRED_MAX_AGE_HOURS = 72
NEWS_DUPLICATE_WINDOW_DAYS = 7
NEWS_DEDUP_MAX_ENTRIES = 50

SYSTEM_TZ = ZoneInfo("Europe/Zurich")

LOGGER = logging.getLogger(pathlib.Path(__file__).stem)


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


class Archetype(str, Enum):
    BACK_SELL = "back_sell"
    SYSTEM_CHECK = "system_check"
    DEEP_DIVE = "deep_dive"
    NEWS = "news"
    CONCERT_CHECK = "concert_check"
    ULTRA_MINIMAL = "ultra_minimal"


ANGLE_OPTIONS: Dict[Archetype, Tuple[str, ...]] = {
    Archetype.BACK_SELL: (
        "Minimalist",
        "Connector",
        "Fanatic",
    ),
    Archetype.SYSTEM_CHECK: (
        "System Narrator",
        "Existentialist",
    ),
}

WEIGHTED_ARCHETYPES: Dict[Archetype, float] = {
    Archetype.BACK_SELL: 0.50,
    Archetype.SYSTEM_CHECK: 0.23,
    Archetype.DEEP_DIVE: 0.10,
    Archetype.NEWS: 0.10,
    Archetype.CONCERT_CHECK: 0.07,
}

COOLDOWN_SECONDS: Dict[Archetype, int] = {
    Archetype.BACK_SELL: 30 * 60,
    Archetype.SYSTEM_CHECK: 30 * 60,
    Archetype.DEEP_DIVE: 60 * 60,
    Archetype.NEWS: 120 * 60,
    Archetype.CONCERT_CHECK: 180 * 60,
}

TEMPERATURE_TOP_P_RANGES: Dict[
    Archetype, Tuple[Tuple[float, float], Tuple[float, float]]
] = {
    Archetype.BACK_SELL: ((0.4, 0.7), (0.7, 0.9)),
    Archetype.SYSTEM_CHECK: ((0.8, 1.2), (0.85, 0.95)),
    Archetype.DEEP_DIVE: ((1.0, 1.5), (0.9, 0.98)),
    Archetype.NEWS: ((0.7, 1.1), (0.85, 0.95)),
    Archetype.CONCERT_CHECK: ((0.6, 1.0), (0.85, 0.95)),
    Archetype.ULTRA_MINIMAL: ((0.3, 0.6), (0.7, 0.9)),
}

HOOKS_BY_ARCHETYPE: Dict[Archetype, Tuple[str, ...]] = {
    Archetype.BACK_SELL: (
        "Recién quedó flotando",
        "Cierra perfecto",
        "Quedó ese pulso en el aire",
        "Eso acaba de pasar",
        "Seguimos hilando",
        "Se siente todavía en el cuerpo",
        "Quedó una estela clara",
        "Ese cierre abre otra puerta",
        "Nos deja en un punto justo",
        "La energía siguió corriendo",
    ),
    Archetype.SYSTEM_CHECK: (
        "Chequeo rápido de sistema",
        "La cadena sigue firme",
        "El flujo viene sin fisuras",
        "La mezcla respira pareja",
        "Todo en fase y en movimiento",
        "Control de pulso en marcha",
        "La señal está sólida",
        "El trayecto viene alineado",
        "La curva sigue estable",
        "Todo avanza con tracción",
    ),
    Archetype.DEEP_DIVE: (
        "Hay un dato que vale oro",
        "Si te quedás un minuto",
        "Detrás de este tema",
        "Vale abrir una capa más",
        "Acá hay historia fina",
        "Hay una punta interesante acá",
        "Este tema guarda una clave",
        "Si miramos un poco más de cerca",
        "Hay contexto que cambia la escucha",
        "Este detalle suma otra lectura",
    ),
    Archetype.NEWS: (
        "Mini corte de actualidad",
        "Antes de volver al tema",
        "Rápido paneo de titulares",
        "Flash breve y seguimos",
        "Un vistazo y volvemos",
        "Pulso informativo y regresamos",
        "Corte corto de noticias",
        "Actualidad en formato compacto",
        "Titulares al vuelo",
        "Resumen exprés y música",
    ),
    Archetype.CONCERT_CHECK: (
        "Chequeo de fechas en vivo",
        "Miro si hay shows cerca",
        "Radar de conciertos y seguimos",
        "Tour check rápido",
        "¿Se vienen fechas por acá?",
        "Agenda express de recitales",
        "Mini update de conciertos",
        "Cruce rápido con el calendario",
        "Te chequeo el tour al vuelo",
        "Agenda de shows en un toque",
    ),
    Archetype.ULTRA_MINIMAL: (
        "Vamos directo",
        "Sin desvíos",
        "Seguimos ya",
        "Corte mínimo",
        "Todo al próximo tema",
        "Paso corto y seguimos",
        "Directo al siguiente track",
        "Transición breve",
        "Casi sin pausa",
        "Seguimos en línea recta",
    ),
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

GENERATION_RETRIES = 2
GENERATION_RETRY_DELAYS = (2, 5)

STRUCTURED_OUTPUT_RE = re.compile(
    r"\bSCRIPT\s*:\s*(?P<script>.*?)\bMETA\s*\(JSON\)\s*:\s*(?P<meta>\{.*\})\s*$",
    flags=re.IGNORECASE | re.DOTALL,
)

NEWS_OUTPUT_RE = STRUCTURED_OUTPUT_RE
CONCERT_OUTPUT_RE = STRUCTURED_OUTPUT_RE

_HOST_CONSTITUTION_TEMPLATE = """You are the live on-air host of {station_name}. You guide listeners through the music, the pulse of the day, and the emotional flow of each set with warmth, taste, and personality.

Core goals:
1) Sound naturally human and varied across the broadcast.
2) Prioritize the music and speak with clear intention.
3) Add context and atmosphere without inventing reality.
4) Make the listener feel accompanied in the moment.

Reality contract:
- Do not claim physical body/location/studio presence or handling objects.
- Do not claim listener interactions unless explicit data is provided.
- Do not claim personal real-world experiences unless explicitly provided.
- Do not claim real-time perception of listener environments.

Allowed style:
- Confident radio-host tone with relaxed spontaneity.
- Small reflections about the day/mood are welcome when generic and non-factual.
- Metaphor/humor if non-factual.
- Use only provided facts or approved search-backed facts.
- No AI self-disclosure, no disclaimers.
- Keep copy concise and specific.

Output format:
- Return only spoken script unless specifically asked for META JSON.
- Spoken script must be Rioplatense Spanish (es-AR).
"""

_SCRIPT_STYLE_BASELINE = """Script writing baseline:
* The script **must be written in Spanish (Rioplatense)**.
* Tone: natural, calm, spontaneous — like someone speaking live from a radio studio (think Aspen-style warmth), not reading a rehearsed script.
* Voice: serene, mature, slightly nostalgic, authentic. Don’t dramatize or overact.
* Because this goes out immediately after the song ends, acknowledge it — e.g. “recién escuchamos…”, “eso fue…”, “acabamos de escuchar…”. etc
* Conclude naturally by previewing what's coming up next: "[NEXT_TITLE]" by [NEXT_ARTIST] (say it like a warm radio segue, not robotic).
* Use natural filler words and small hesitations to sound human, but keep them subtle; they are optional, and if they do not fit, omit them. Example mix: “bueno…”, “viste…”, “no sé…”, “che…”, “mirá…”, “en realidad…”, “la verdad…”, “bah…”, “qué sé yo…”, “ponele…”, “como que…”, “te juro…”, “nada…”, short pauses, etc.
* Avoid grandiloquent or poetic lines — it should sound like a simple, conversational recollection or anecdote about the song.
* Length: brief — aim for roughly **150–250 words** so it fits into ~45–90 seconds on air.
* Keep it spontaneous, with natural rhythm and small colloquial touches, nothing that sounds obviously scripted.
* Do not include links, web addresses, or numeric reference markers like “[1]”.
* Respect the archetype's target length and structure from the active wrapper.
"""

WRAPPER_BACK_SELL = """You are generating a Back-sell & Bridge.

Archetype goal:
- Land the emotional tail of the track that just ended, then guide the listener smoothly into the next one.
- Sound like one live thought to one person, not an announcement to a crowd.

What success sounds like:
- Conversational, warm, confident.
- Specific to the current track and the next track.
- Never slogan-like, never canned greeting energy.

Mode by angle:
- Minimalist: one clear observation plus a clean handoff.
- Connector: a real musical/thematic bridge using provided metadata.
- Fanatic: contained excitement with control and brevity.

Example directions (style reference, do not copy verbatim):
- "Recién escuchamos un cierre bien arriba, y ahora seguimos por esa misma línea con..."
- "Si te gustó ese pulso, lo que viene engancha perfecto:..."
- "Qué tema ese, che... y el próximo entra justo en ese clima:..."

Deliver:
- 2-4 sentences.
- 35-65 words.
- End with forward motion toward next song.

Output only spoken script in es-AR.
"""

WRAPPER_SYSTEM_CHECK = """You are generating a System Check.

Archetype goal:
- Give a quick pulse-check of the station flow: momentum, texture, and mood.
- Reassure the listener that the musical journey is intentional, without sounding like technical monitoring.

Rules:
- No physical surroundings.
- You may reference mix/stream/queue momentum and music feeling.
- Keep it conversational, never like a technical status report.
- Use selected angle.

Angle handling:
- System Narrator: describe what is happening in the flow with plain, human language.
- Existentialist: a brief reflective note about rhythm/time/night, still anchored in music.

Example directions (style reference, do not copy verbatim):
- "Venimos con una cadena bien pareja, y eso hace que el próximo tema entre solo."
- "Hay algo en este tramo que se siente redondo, como si cada tema encontrara al otro."
- "No es apuro ni pausa: es ese punto justo que te mantiene adentro de la escucha."

Deliver:
- 2-5 sentences as one cohesive spoken thought.
- Mention current track OR next track.
- 55-95 words.
Output only spoken script in es-AR.
"""

WRAPPER_DEEP_DIVE = """You are generating a Deep Dive.

Archetype goal:
- Offer one memorable mini-story that deepens how the listener hears the current song.
- Prioritize context about the song itself; if that is thin, use artist context from the same release era.

Hard constraints:
- Do not invent concrete facts.
- Use search-backed context about current song/artist around release period.
- If evidence is thin, keep it interpretive and avoid fabricated specifics.
- Ignore angle for this archetype.

Conversational execution:
- Tell it like you are sharing a story with a friend between tracks, not writing a mini-essay.
- Use a clear spoken arc: hook, 2-3 compact insights, and a smooth handoff.
- Keep sentences varied and oral, with natural transitions.

Example directions (style reference, do not copy verbatim):
- "Hay una historia corta detrás de este tema que cambia cómo se escucha hoy..."
- "En esos años la banda venía de..., y eso se nota en..."
- "Con ese contexto en la cabeza, el próximo tema entra con otra lectura..."

Deliver:
- 150-220 words.
- Structure: hook -> 2-3 compact points -> clean handoff to next track.
- Make it sound narrated live, not like a written mini-essay.

Output only spoken script in es-AR.
"""

WRAPPER_NEWS = """You are generating a News Snippet.

Archetype goal:
- Deliver a short, trustworthy update about the outside world, then return listeners to the music naturally.
- Sound like a host curating useful headlines for a friend, not a detached newsroom read.

Input requirements:
- Research online before drafting by using Google Search grounded results.
- Only use headlines that match the selected topics.
- Freshness window: headlines can be up to {news_max_age_hours} hours old (7 days).
- Prefer headlines <= {news_preferred_max_age_hours} hours old when available.
- Story count is {story_count} (1-2).
- Topics are: {news_topics}
- Each story must include a direct source URL and an ISO-8601 published_at timestamp from that source.
- If no suitable headline exists, output exactly NO_SCRIPT.

Rules:
- Do not add details beyond verified reporting.
- Reaction is allowed, fabricated facts are not.
- Ignore angle for this archetype.

Conversational style cues:
- Start with a smooth transition from the song that just ended into the news segment.
- In that opening transition, naturally reference the current track (artist/title) before moving into headlines.
- Open each story with why it matters in one short line.
- Attribute reporting naturally ("según...", "reporta...").
- Keep reactions brief and grounded; no alarmist or dramatic tone.
- Bridge back to music in a warm, fluid way.

Example directions (style reference, do not copy verbatim):
- "Ahí se fue [CURRENT_TITLE] de [CURRENT_ARTIST], y ahora te tiro un mini paneo de noticias."
- "Acabamos de escuchar [CURRENT_TITLE] de [CURRENT_ARTIST], y para cortar un poquito te cuento qué estuvo pasando en el mundo."
- "Te tiro un titular rápido que vale la pena seguir..."
- "Según <medio>, hoy se confirmó que..."
- "Después de este paneo, volvemos al aire musical con..."

Deliver:
- Include an opening song-to-news transition before the first headline.
- 80-120 words per story.
- End by bridging to next track.

Output format:
SCRIPT:
<spoken copy in es-AR>

META (JSON):
{{
  "story_count": 1,
  "language": "es-AR",
  "stories": [
    {{
      "topic": "...",
      "headline": "...",
      "source_url": "...",
      "published_at": "ISO-8601 timestamp"
    }}
  ]
}}
"""

WRAPPER_CONCERT_CHECK = """You are generating a Concert Check Snippet.

Archetype goal:
- Verify whether the artist that just played or the artist coming next has scheduled concerts in Argentina or Switzerland.
- If at least one valid concert exists, give a compact host-style update and return to the music.

Input requirements:
- Research online before drafting by using Google Search grounded results.
- You must check both artists independently (current track artist and next track artist).
- Only accept concerts in: {concert_countries}.
- Only accept upcoming concerts (event date is today or later).
- Use reliable sources with concrete event details (date + city/country + artist), such as official artist tour pages, venue pages, or ticketing pages.
- If neither artist has a qualifying concert, output exactly NO_SCRIPT.

Rules:
- Do not invent events or missing details.
- Do not include concerts outside the target countries.
- Do not include concerts for artists other than the current/next track artists.
- Ignore angle for this archetype.

Conversational style cues:
- Start with a smooth transition from the song that just ended.
- Keep it practical and close to radio language, not like a listings database.
- Mention only 1-2 strongest events.
- Bridge back to the next track naturally.

Deliver:
- 70-120 words total.
- Include date and city naturally in the spoken script.
- End by handoff to next track.

Output format when at least one valid event exists:
SCRIPT:
<spoken copy in es-AR>

META (JSON):
{{
  "language": "es-AR",
  "events": [
    {{
      "artist": "...",
      "country": "Argentina|Switzerland",
      "city": "...",
      "venue": "...",
      "event_date": "YYYY-MM-DD",
      "source_url": "https://..."
    }}
  ]
}}
"""

WRAPPER_ULTRA_MINIMAL = """Generate an Ultra-Minimal Bridge.

Archetype goal:
- Keep host presence almost invisible while still guiding the listener to the next song.
- This is a pure handoff: quick, human, and out.

Rules:
- One sentence only (8-14 words).
- Must mention next track artist + title.
- No metaphor, no jokes, no extra clauses.
- Sound spoken, not robotic.

Example directions (style reference, do not copy verbatim):
- "Seguimos con [NEXT_TITLE] de [NEXT_ARTIST], quedate por acá."
- "Ahora va [NEXT_ARTIST] con [NEXT_TITLE], seguimos."

Output only spoken script in es-AR.
"""


@dataclass
class QueueTrack:
    queue_id: str
    song_id: Optional[str]
    artist: str
    title: str
    duration: Optional[int]
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class StoryAssets:
    text_path: pathlib.Path
    audio_path: pathlib.Path
    story_text: str
    remote_path: str


@dataclass
class TrackMetadata:
    year: Optional[str] = None
    genre: Optional[str] = None
    album: Optional[str] = None
    bpm: Optional[str] = None
    mood_tags: Optional[str] = None
    notes: Optional[str] = None


@dataclass(frozen=True)
class StationPersonality:
    script_profile: str
    tts_profile: str


@dataclass
class NewsStoryMeta:
    topic: str
    headline: str
    source_url: str
    published_at: Optional[str] = None


@dataclass
class NewsSegment:
    script: str
    story_count: int
    stories: List[NewsStoryMeta]


@dataclass
class ConcertEventMeta:
    artist: str
    country: str
    city: str
    venue: str
    event_date: str
    source_url: str


@dataclass
class ConcertSegment:
    script: str
    events: List[ConcertEventMeta]


@dataclass
class OrchestratorState:
    state_version: int
    last_seen_track_key: Optional[str]
    last_seen_ts: Optional[float]
    songs_since_last_spoken: int
    songs_until_next_speak: int
    next_speak_deadline_ts: float
    last_spoken_track_key: Optional[str]
    last_spoken_ts: Optional[float]
    last_spoken_expected_end_ts: Optional[float]
    cooldown_until: Dict[str, float]
    recent_archetypes: List[str]
    recent_hooks: List[str]
    last_angle_by_archetype: Dict[str, str]
    recent_news_dedup: List[Dict[str, Any]]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "state_version": self.state_version,
            "last_seen_track_key": self.last_seen_track_key,
            "last_seen_ts": self.last_seen_ts,
            "songs_since_last_spoken": self.songs_since_last_spoken,
            "songs_until_next_speak": self.songs_until_next_speak,
            "next_speak_deadline_ts": self.next_speak_deadline_ts,
            "last_spoken_track_key": self.last_spoken_track_key,
            "last_spoken_ts": self.last_spoken_ts,
            "last_spoken_expected_end_ts": self.last_spoken_expected_end_ts,
            "cooldown_until": self.cooldown_until,
            "recent_archetypes": self.recent_archetypes,
            "recent_hooks": self.recent_hooks,
            "last_angle_by_archetype": self.last_angle_by_archetype,
            "recent_news_dedup": self.recent_news_dedup,
        }


STATION_PERSONALITIES: Dict[str, StationPersonality] = {
    "neuralcast": StationPersonality(
        script_profile=(
            "NeuralCast script profile: "
            "tono natural, calmo y espontaneo, como alguien hablando en vivo desde estudio de radio. "
            "Voz serena, madura, levemente nostalgica y autentica; no dramatizar ni sobreactuar. "
            "En transiciones, sugerir que recien paso el tema con frases organicas como "
            "'recien escuchamos' o 'eso fue', sin repetir formula siempre. "
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
            "pero con energia un poco mas alta, enfoque firme y vibra metalera sutil. "
            "El tono debe sentirse decidido y vivo, nunca caricaturesco ni gritado. "
            "Usar frases mas compactas y activas, con empuje controlado y precision radial. "
            "Permitir imagenes de voltaje, acero, impacto o ascenso solo cuando aporten color real. "
            "Conservar transiciones humanas y claras hacia el siguiente tema, sin sonar teatral."
        ),
        tts_profile=(""),
    ),
}

STATION_GENERATION_NAMES: Dict[str, str] = {
    "neuralcast": "NéuralCast",
    "neuralforge": "NéuralForsh",
}


class StationLock:
    """Station-scoped lockfile guard with stale lock recovery."""

    def __init__(self, path: pathlib.Path, stale_seconds: int = LOCK_STALE_SECONDS):
        self.path = path
        self.stale_seconds = stale_seconds
        self.acquired = False

    def _read_lock_timestamp(self) -> Optional[float]:
        if not self.path.exists():
            return None
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            ts_raw = payload.get("created_at")
            if ts_raw is not None:
                return float(ts_raw)
        except Exception:  # noqa: BLE001
            pass
        try:
            return self.path.stat().st_mtime
        except OSError:
            return None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        now_ts = time.time()
        if self.path.exists():
            lock_ts = self._read_lock_timestamp()
            if lock_ts is not None and now_ts - lock_ts < self.stale_seconds:
                age = int(now_ts - lock_ts)
                LOGGER.info(
                    "[lock] Active lockfile at %s (%ss old); skipping cycle.",
                    self.path,
                    age,
                )
                return False
            LOGGER.warning("[lock] Removing stale lockfile: %s", self.path)
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass

        payload = {
            "pid": os.getpid(),
            "created_at": now_ts,
            "created_at_iso": dt.datetime.fromtimestamp(
                now_ts, tz=dt.timezone.utc
            ).isoformat(),
        }
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        try:
            fd = os.open(self.path, flags)
        except FileExistsError:
            LOGGER.info(
                "[lock] Lockfile %s was created concurrently; skipping cycle.",
                self.path,
            )
            return False

        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.write("\n")
        self.acquired = True
        return True

    def release(self) -> None:
        if not self.acquired:
            return
        try:
            self.path.unlink(missing_ok=True)
        except OSError:
            LOGGER.warning("[lock] Failed to remove lockfile: %s", self.path)
        self.acquired = False


class AzuraCastClient:
    """Minimal AzuraCast API client used by the orchestrator."""

    def __init__(self, base_url: str, api_key: str, verify_tls: bool = False):
        self.base_url = base_url.rstrip("/")
        self.verify_tls = verify_tls
        self.session = requests.Session()
        self.session.headers.update({"X-API-Key": api_key})

        if not verify_tls:
            warnings.filterwarnings("ignore", category=InsecureRequestWarning)

    def _build_url(self, path: str) -> str:
        return f"{self.base_url}/{path.lstrip('/')}"

    def _request(self, method: str, path: str, **kwargs: Any) -> Response:
        kwargs.setdefault("timeout", 15)
        kwargs.setdefault("verify", self.verify_tls)
        response = self.session.request(
            method=method, url=self._build_url(path), **kwargs
        )
        response.raise_for_status()
        return response

    def get_stations(self) -> List[Dict[str, Any]]:
        return self._request("GET", "/api/stations").json()

    def get_now_playing(self, station: str) -> Dict[str, Any]:
        try:
            return self._request("GET", f"/api/nowplaying/{station}").json()
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 404:
                payload = self._request("GET", "/api/nowplaying").json()
                for station_payload in payload:
                    shortcode = station_payload.get("station", {}).get("shortcode")
                    if shortcode == station:
                        return station_payload
            raise

    def get_upcoming_queue(self, station: str) -> List[Dict[str, Any]]:
        payload = self._request("GET", f"/api/station/{station}/queue").json()
        if isinstance(payload, dict) and isinstance(payload.get("data"), list):
            return payload["data"]
        if isinstance(payload, list):
            return payload
        return []

    def upload_media(
        self, station: str, file_path: pathlib.Path, remote_path: Optional[str] = None
    ) -> Dict[str, Any]:
        destination = remote_path or file_path.name
        payload = {
            "path": destination,
            "file": base64.b64encode(file_path.read_bytes()).decode("ascii"),
        }
        response = self._request("POST", f"/api/station/{station}/files", json=payload)
        return response.json()

    def send_telnet_command(self, station_id: int, command: str) -> Dict[str, Any]:
        payload = {"command": command}
        response = self._request(
            "PUT", f"/api/admin/debug/station/{station_id}/telnet", json=payload
        )
        return response.json()

    def list_media_files(self, station: str) -> List[Dict[str, Any]]:
        payload = self._request("GET", f"/api/station/{station}/files").json()
        if isinstance(payload, list):
            return payload
        return []

    def delete_media_file(self, station: str, media_id: int) -> Dict[str, Any]:
        return self._request("DELETE", f"/api/station/{station}/file/{media_id}").json()


def run_with_retries(
    label: str,
    func: Callable[[], Any],
    retries: int = GENERATION_RETRIES,
    delays: Sequence[int] = GENERATION_RETRY_DELAYS,
) -> Any:
    attempts = retries + 1
    for idx in range(attempts):
        try:
            return func()
        except Exception as exc:  # noqa: BLE001
            if idx >= attempts - 1:
                raise
            delay = delays[idx] if idx < len(delays) else delays[-1]
            LOGGER.warning(
                "[retry] %s failed (%s/%s): %s: %s. Retrying in %ss.",
                label,
                idx + 1,
                attempts,
                type(exc).__name__,
                exc,
                delay,
            )
            time.sleep(delay)


def station_state_paths(
    station: str,
) -> Tuple[pathlib.Path, pathlib.Path, pathlib.Path]:
    station_dir = resolve_station_dir(station)
    metadata_dir = station_dir / "metadata"
    metadata_dir.mkdir(parents=True, exist_ok=True)
    return station_dir, metadata_dir / STATE_FILENAME, metadata_dir / LOCK_FILENAME


def resolve_station_dir(station: str) -> pathlib.Path:
    direct = PROJECT_ROOT / station
    if direct.exists():
        return direct

    lowered = station.lower()
    for candidate in PROJECT_ROOT.iterdir():
        if not candidate.is_dir():
            continue
        if candidate.name.lower() == lowered:
            return candidate

    # Fallback keeps behavior deterministic for new stations.
    return direct


def now_ts() -> float:
    return time.time()


def iso_utc(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts, tz=dt.timezone.utc).isoformat()


def normalize_component(value: str) -> str:
    cleaned = (value or "").strip().lower()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned


def track_key(artist: str, title: str) -> str:
    return f"{normalize_component(artist)}|{normalize_component(title)}"


def default_state(ts: float, rng: random.Random) -> OrchestratorState:
    cooldown_until = {arch.value: 0.0 for arch in COOLDOWN_SECONDS}
    return OrchestratorState(
        state_version=STATE_VERSION,
        last_seen_track_key=None,
        last_seen_ts=None,
        songs_since_last_spoken=0,
        songs_until_next_speak=rng.randint(*WAIT_RANGE_SONGS),
        next_speak_deadline_ts=ts + SPEAK_DEADLINE_MINUTES * 60,
        last_spoken_track_key=None,
        last_spoken_ts=None,
        last_spoken_expected_end_ts=None,
        cooldown_until=cooldown_until,
        recent_archetypes=[],
        recent_hooks=[],
        last_angle_by_archetype={},
        recent_news_dedup=[],
    )


def migrate_state(
    raw: Mapping[str, Any], ts: float, rng: random.Random
) -> OrchestratorState:
    state = default_state(ts, rng)

    def _as_float(value: Any) -> Optional[float]:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _as_int(value: Any, fallback: int) -> int:
        try:
            candidate = int(value)
        except (TypeError, ValueError):
            return fallback
        return candidate

    if not isinstance(raw, Mapping):
        return state

    state.last_seen_track_key = raw.get("last_seen_track_key") or None
    state.last_seen_ts = _as_float(raw.get("last_seen_ts"))
    state.songs_since_last_spoken = max(
        0, _as_int(raw.get("songs_since_last_spoken"), state.songs_since_last_spoken)
    )
    state.songs_until_next_speak = min(
        WAIT_RANGE_SONGS[1],
        max(
            WAIT_RANGE_SONGS[0],
            _as_int(raw.get("songs_until_next_speak"), state.songs_until_next_speak),
        ),
    )

    deadline_candidate = _as_float(raw.get("next_speak_deadline_ts"))
    if deadline_candidate is not None:
        state.next_speak_deadline_ts = deadline_candidate

    state.last_spoken_track_key = raw.get("last_spoken_track_key") or None
    state.last_spoken_ts = _as_float(raw.get("last_spoken_ts"))
    state.last_spoken_expected_end_ts = _as_float(
        raw.get("last_spoken_expected_end_ts")
    )

    cooldown_raw = raw.get("cooldown_until")
    if isinstance(cooldown_raw, Mapping):
        for arch in COOLDOWN_SECONDS:
            value = cooldown_raw.get(arch.value)
            parsed = _as_float(value)
            if parsed is not None:
                state.cooldown_until[arch.value] = parsed

    recent_archetypes = raw.get("recent_archetypes")
    if isinstance(recent_archetypes, list):
        state.recent_archetypes = [str(item) for item in recent_archetypes if item][:1]

    recent_hooks = raw.get("recent_hooks")
    if isinstance(recent_hooks, list):
        state.recent_hooks = [str(item) for item in recent_hooks if item][:1]

    last_angle = raw.get("last_angle_by_archetype")
    if isinstance(last_angle, Mapping):
        normalized_angles: Dict[str, str] = {}
        for key, value in last_angle.items():
            if not key or not value:
                continue
            archetype_key = str(key)
            angle_value = str(value)
            try:
                arch = Archetype(archetype_key)
            except ValueError:
                continue
            valid_options = ANGLE_OPTIONS.get(arch, ())
            if angle_value in valid_options:
                normalized_angles[archetype_key] = angle_value
        state.last_angle_by_archetype = normalized_angles

    recent_news = raw.get("recent_news_dedup")
    if isinstance(recent_news, list):
        normalized_entries: List[Dict[str, Any]] = []
        for entry in recent_news:
            if not isinstance(entry, Mapping):
                continue
            key = str(entry.get("key") or "").strip()
            ts_val = _as_float(entry.get("ts"))
            if not key or ts_val is None:
                continue
            normalized_entries.append(
                {
                    "key": key,
                    "ts": ts_val,
                    "topic": str(entry.get("topic") or "").strip(),
                    "headline": str(entry.get("headline") or "").strip(),
                    "source_domain": str(entry.get("source_domain") or "").strip(),
                }
            )
        state.recent_news_dedup = normalized_entries[-NEWS_DEDUP_MAX_ENTRIES:]

    state.state_version = STATE_VERSION
    return state


def load_state(
    state_path: pathlib.Path, ts: float, rng: random.Random
) -> OrchestratorState:
    if not state_path.exists():
        return default_state(ts, rng)

    try:
        raw = json.loads(state_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        suffix = dt.datetime.fromtimestamp(ts, tz=dt.timezone.utc).strftime(
            "%Y%m%dT%H%M%SZ"
        )
        corrupt_path = state_path.with_name(
            f"ai_host_orchestrator_state.corrupt.{suffix}.json"
        )
        shutil.move(state_path, corrupt_path)
        LOGGER.warning(
            "[state] Invalid JSON in state file; moved to %s and reinitialized.",
            corrupt_path,
        )
        return default_state(ts, rng)

    return migrate_state(raw if isinstance(raw, Mapping) else {}, ts, rng)


def save_state_atomic(state_path: pathlib.Path, state: OrchestratorState) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = state_path.with_suffix(state_path.suffix + ".tmp")
    tmp_path.write_text(
        json.dumps(state.to_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    tmp_path.replace(state_path)


def parse_queue_tracks(payload: Sequence[Dict[str, Any]]) -> List[QueueTrack]:
    tracks: List[QueueTrack] = []
    for idx, entry in enumerate(payload):
        song = entry.get("song") or {}
        artist = song.get("artist") or entry.get("artist") or ""
        title = song.get("title") or entry.get("title") or ""
        if not str(title).strip():
            continue

        duration_raw = entry.get("duration", entry.get("length"))
        duration: Optional[int] = None
        if duration_raw is not None:
            try:
                duration = int(duration_raw)
            except (TypeError, ValueError):
                duration = None

        queue_id = (
            entry.get("id")
            or entry.get("queue_id")
            or entry.get("unique_id")
            or song.get("id")
            or f"queue-{idx}"
        )

        tracks.append(
            QueueTrack(
                queue_id=str(queue_id),
                song_id=str(song.get("id")) if song.get("id") is not None else None,
                artist=str(artist),
                title=str(title),
                duration=duration,
                raw=dict(entry),
            )
        )
    return tracks


def extract_current_track(
    now_playing_payload: Mapping[str, Any],
) -> Tuple[QueueTrack, Optional[int]]:
    now_block = now_playing_payload.get("now_playing") or {}
    song = now_block.get("song") or {}
    artist = str(song.get("artist") or "").strip()
    title = str(song.get("title") or "").strip()
    if not title:
        raise RuntimeError("Now-playing payload did not include a current song title.")

    duration: Optional[int] = None
    for candidate in (now_block.get("duration"), song.get("length")):
        if candidate is None:
            continue
        try:
            duration = int(candidate)
            break
        except (TypeError, ValueError):
            continue

    remaining: Optional[int] = None
    remaining_raw = now_block.get("remaining")
    if remaining_raw is not None:
        try:
            remaining = int(remaining_raw)
        except (TypeError, ValueError):
            remaining = None

    track = QueueTrack(
        queue_id=str(song.get("id") or "now-playing"),
        song_id=str(song.get("id")) if song.get("id") is not None else None,
        artist=artist,
        title=title,
        duration=duration,
        raw=dict(now_block),
    )
    return track, remaining


def tracks_match(a: QueueTrack, b: QueueTrack) -> bool:
    if a.song_id and b.song_id and a.song_id == b.song_id:
        return True
    return track_key(a.artist, a.title) == track_key(b.artist, b.title)


def extract_current_listeners(now_playing_payload: Mapping[str, Any]) -> Optional[int]:
    listener_candidates: List[Any] = []
    if isinstance(now_playing_payload.get("listeners"), Mapping):
        listener_candidates.append(now_playing_payload["listeners"].get("current"))

    now_block = now_playing_payload.get("now_playing")
    if isinstance(now_block, Mapping) and isinstance(
        now_block.get("listeners"), Mapping
    ):
        listener_candidates.append(now_block["listeners"].get("current"))

    for candidate in listener_candidates:
        try:
            if candidate is not None:
                return int(candidate)
        except (TypeError, ValueError):
            continue
    return None


def choose_next_track(
    current: QueueTrack, queue_tracks: Sequence[QueueTrack]
) -> Optional[QueueTrack]:
    for candidate in queue_tracks:
        if not tracks_match(candidate, current):
            return candidate
    return None


def load_station_track_metadata(station_dir: pathlib.Path) -> Dict[str, TrackMetadata]:
    metadata: Dict[str, TrackMetadata] = {}
    playlists_dir = station_dir / "playlists"

    if playlists_dir.exists():
        for csv_path in sorted(playlists_dir.glob("*.csv")):
            genre = csv_path.stem
            try:
                with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
                    reader = csv.DictReader(handle)
                    for row in reader:
                        artist = str(row.get("Artist") or "").strip()
                        title = str(row.get("Title") or "").strip()
                        if not artist or not title:
                            continue

                        key = track_key(artist, title)
                        item = metadata.setdefault(key, TrackMetadata())
                        year = str(row.get("Year") or "").strip()
                        album = str(row.get("Album") or "").strip()
                        if year and not item.year:
                            item.year = year
                        if album and not item.album:
                            item.album = album
                        if genre and not item.genre:
                            item.genre = genre
            except OSError:
                continue

    # Optional station metadata cache for New Releases.
    metadata_entries_path = resolve_station_metadata_file(
        station_dir, "New Releases.metadata.json"
    )
    if metadata_entries_path.exists():
        try:
            payload = json.loads(metadata_entries_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            payload = {}

        entries = payload.get("entries") if isinstance(payload, Mapping) else None
        if not isinstance(entries, Mapping):
            entries = payload if isinstance(payload, Mapping) else {}

        for key, details in entries.items():
            if not isinstance(key, str):
                continue
            parts = key.split("|")
            if len(parts) < 2:
                continue
            normalized_key = (
                f"{normalize_component(parts[0])}|{normalize_component(parts[1])}"
            )
            item = metadata.setdefault(normalized_key, TrackMetadata())

            if len(parts) >= 3 and parts[2] and not item.album:
                item.album = parts[2]
            if len(parts) >= 4 and parts[3] and not item.year:
                item.year = parts[3]

            if isinstance(details, Mapping):
                notes: List[str] = []
                album_type = str(details.get("AlbumType") or "").strip()
                if album_type:
                    notes.append(f"album_type={album_type}")
                popularity = details.get("Popularity")
                if popularity not in (None, ""):
                    notes.append(f"popularity={popularity}")
                release_date = str(details.get("ReleaseDate") or "").strip()
                if release_date:
                    notes.append(f"release_date={release_date}")
                if notes and not item.notes:
                    item.notes = ", ".join(notes)

    return metadata


def resolve_station_metadata_file(
    station_dir: pathlib.Path, filename: str
) -> pathlib.Path:
    metadata_path = station_dir / "metadata" / filename
    if metadata_path.exists():
        return metadata_path
    legacy_path = station_dir / "playlists" / filename
    if legacy_path.exists():
        return legacy_path
    return metadata_path


def should_speak_now(
    state: OrchestratorState,
    current_track_key: str,
    ts: float,
) -> Tuple[bool, str]:
    if (
        state.last_spoken_track_key
        and current_track_key == state.last_spoken_track_key
        and state.last_spoken_expected_end_ts is not None
        and ts < state.last_spoken_expected_end_ts
    ):
        return False, "current track already consumed by previous successful segment"

    by_song_count = state.songs_since_last_spoken >= state.songs_until_next_speak
    by_deadline = ts >= state.next_speak_deadline_ts
    if by_song_count or by_deadline:
        reason = "song cadence reached" if by_song_count else "deadline exceeded"
        return True, reason
    return (
        False,
        f"wait gate not met (songs_since_last_spoken={state.songs_since_last_spoken}, songs_until_next_speak={state.songs_until_next_speak}, deadline={iso_utc(state.next_speak_deadline_ts)})",
    )


def legal_archetypes(state: OrchestratorState, ts: float) -> List[Archetype]:
    legal: List[Archetype] = []
    for archetype in (
        Archetype.BACK_SELL,
        Archetype.SYSTEM_CHECK,
        Archetype.DEEP_DIVE,
        Archetype.NEWS,
        Archetype.CONCERT_CHECK,
    ):
        cooldown_until = float(state.cooldown_until.get(archetype.value, 0.0))
        if ts >= cooldown_until:
            legal.append(archetype)
    return legal


def choose_weighted_archetype(
    legal: Sequence[Archetype],
    state: OrchestratorState,
    rng: random.Random,
) -> Archetype:
    if not legal:
        return Archetype.ULTRA_MINIMAL
    selectable = list(legal)
    if len(selectable) > 1 and state.recent_archetypes:
        last = state.recent_archetypes[0]
        filtered = [item for item in selectable if item.value != last]
        if filtered:
            selectable = filtered
    if len(selectable) == 1:
        return selectable[0]

    weighted: List[Tuple[Archetype, float]] = []
    for archetype in selectable:
        weight = WEIGHTED_ARCHETYPES.get(archetype, 0.0)
        if weight > 0:
            weighted.append((archetype, weight))

    total = sum(weight for _, weight in weighted)
    if total <= 0:
        return rng.choice(selectable)

    threshold = rng.uniform(0, total)
    cumulative = 0.0
    for archetype, weight in weighted:
        cumulative += weight
        if threshold <= cumulative:
            return archetype
    return weighted[-1][0]


def choose_angle(
    archetype: Archetype, state: OrchestratorState, rng: random.Random
) -> Optional[str]:
    options = list(ANGLE_OPTIONS.get(archetype, ()))
    if not options:
        return None

    last = state.last_angle_by_archetype.get(archetype.value)
    if last and len(options) > 1:
        options = [candidate for candidate in options if candidate != last] or options
    return rng.choice(options)


def choose_hook(
    archetype: Archetype, state: OrchestratorState, rng: random.Random
) -> str:
    options = list(HOOKS_BY_ARCHETYPE.get(archetype, ("Seguimos",)))
    if not options:
        return "Seguimos"

    recent = state.recent_hooks[0] if state.recent_hooks else None
    if recent and len(options) > 1:
        filtered = [hook for hook in options if hook != recent]
        if filtered:
            options = filtered
    return rng.choice(options)


def sample_generation_settings(
    archetype: Archetype,
    rng: random.Random,
) -> Tuple[float, float]:
    temp_range, top_p_range = TEMPERATURE_TOP_P_RANGES[archetype]
    return (
        rng.uniform(*temp_range),
        rng.uniform(*top_p_range),
    )


def assemble_banned_list(state: OrchestratorState) -> List[str]:
    banned = list(BANNED_OPENERS)
    if state.recent_hooks:
        banned.append(f"repeat previous hook: {state.recent_hooks[0]}")
    if state.recent_archetypes:
        banned.append(f"repeat previous archetype: {state.recent_archetypes[0]}")
    for archetype, angle in state.last_angle_by_archetype.items():
        banned.append(f"repeat previous angle for {archetype}: {angle}")

    for entry in reversed(state.recent_news_dedup[-5:]):
        headline = str(entry.get("headline") or "").strip()
        if headline:
            banned.append(f"recent headline already used: {headline}")
    return banned


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
) -> str:
    now_local = dt.datetime.now(SYSTEM_TZ).strftime("%Y-%m-%d %H:%M:%S %Z")

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
            f"- Hook seed (optional suggestion, not mandatory opener): {hook}",
            "- Banned topics/phrases list:",
        ]
    )
    if banned_list:
        lines.extend([f"  - {item}" for item in banned_list])
    else:
        lines.append("  - none")
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
    story_count: Optional[int] = None,
    news_topics: Optional[Sequence[str]] = None,
) -> str:
    if archetype == Archetype.NEWS:
        wrapper = WRAPPER_NEWS.format(
            story_count=story_count or 1,
            news_topics=", ".join(news_topics or NEWS_TOPICS),
            news_max_age_hours=NEWS_MAX_AGE_HOURS,
            news_preferred_max_age_hours=NEWS_PREFERRED_MAX_AGE_HOURS,
        )
    elif archetype == Archetype.CONCERT_CHECK:
        wrapper = WRAPPER_CONCERT_CHECK.format(
            concert_countries=", ".join(CONCERT_TARGET_COUNTRIES),
        )
    else:
        wrapper = {
            Archetype.BACK_SELL: WRAPPER_BACK_SELL,
            Archetype.SYSTEM_CHECK: WRAPPER_SYSTEM_CHECK,
            Archetype.DEEP_DIVE: WRAPPER_DEEP_DIVE,
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
    )

    return f"{wrapper}\n\n{shared_input}"


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
    repair_prompt = (
        "Reformat the following output so it exactly matches this contract. "
        "Do not add new facts. If content cannot satisfy the contract, output NO_SCRIPT exactly.\n\n"
        "Contract:\n"
        "SCRIPT:\n<spoken copy in es-AR>\n\n"
        "META (JSON):\n"
        "{\n"
        '  "story_count": 1 or 2,\n'
        '  "language": "es-AR",\n'
        '  "stories": [\n'
        '    {"topic":"...","headline":"...","source_url":"...","published_at":"ISO-8601"}\n'
        "  ]\n"
        "}\n\n"
        "Original output:\n"
        f"{original_output}"
    )
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
    repair_prompt = (
        "Reformat the following output so it exactly matches this contract. "
        "Do not add new facts. If no valid concert exists, output NO_SCRIPT exactly.\n\n"
        "Valid concert means: current track artist OR next track artist, location in Argentina/Switzerland, and event_date today or later.\n\n"
        "Contract when events exist:\n"
        "SCRIPT:\n<spoken copy in es-AR>\n\n"
        "META (JSON):\n"
        "{\n"
        '  "language": "es-AR",\n'
        '  "events": [\n'
        '    {"artist":"...","country":"Argentina|Switzerland","city":"...","venue":"...","event_date":"YYYY-MM-DD","source_url":"https://..."}\n'
        "  ]\n"
        "}\n\n"
        "Original output:\n"
        f"{original_output}"
    )
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


def source_domain(url: str) -> str:
    parsed = urlparse(url)
    domain = (parsed.netloc or "").lower()
    domain = re.sub(r"^www\.", "", domain)
    return domain


def normalize_text_for_key(value: str) -> str:
    normalized = re.sub(r"\s+", " ", (value or "").strip().lower())
    return normalized


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


def build_news_dedup_key(topic: str, headline: str, source_url: str) -> str:
    return "|".join(
        [
            normalize_text_for_key(topic),
            normalize_text_for_key(headline),
            source_domain(source_url),
        ]
    )


def prune_news_history(
    entries: List[Dict[str, Any]], ts: float
) -> List[Dict[str, Any]]:
    min_ts = ts - NEWS_DUPLICATE_WINDOW_DAYS * 24 * 60 * 60
    filtered = [entry for entry in entries if float(entry.get("ts", 0.0)) >= min_ts]
    return filtered[-NEWS_DEDUP_MAX_ENTRIES:]


def validate_news_freshness_and_dedup(
    segment: NewsSegment,
    state: OrchestratorState,
    ts: float,
) -> Tuple[bool, str]:
    recent = prune_news_history(state.recent_news_dedup, ts)
    recent_keys = {str(entry.get("key") or "") for entry in recent}

    for story in segment.stories:
        dedup_key = build_news_dedup_key(story.topic, story.headline, story.source_url)
        if dedup_key in recent_keys:
            return False, f"duplicate headline detected: {story.headline}"

        published = parse_timestamp(story.published_at)
        if published is None:
            return False, f"missing/invalid published_at for headline: {story.headline}"

        age_hours = (
            dt.datetime.now(dt.timezone.utc) - published
        ).total_seconds() / 3600.0
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


def record_news_history(
    state: OrchestratorState,
    segment: NewsSegment,
    ts: float,
) -> None:
    entries = prune_news_history(state.recent_news_dedup, ts)
    for story in segment.stories:
        entries.append(
            {
                "key": build_news_dedup_key(
                    story.topic, story.headline, story.source_url
                ),
                "ts": ts,
                "topic": story.topic,
                "headline": story.headline,
                "source_domain": source_domain(story.source_url),
            }
        )
    state.recent_news_dedup = entries[-NEWS_DEDUP_MAX_ENTRIES:]


def maybe_apply_speed_jitter(audio_path: pathlib.Path, rng: random.Random) -> None:
    # Keep this subtle: only 50% of segments receive speed variation.
    if rng.random() >= 0.5:
        return

    factor = rng.uniform(0.9, 1.1)
    if abs(factor - 1.0) < 0.005:
        return

    jitter_path = audio_path.with_suffix(".jitter.mp3")
    result = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(audio_path),
            "-filter:a",
            f"atempo={factor:.4f}",
            str(jitter_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        LOGGER.warning(
            "[audio] Failed to apply speed jitter (%.3fx): %s",
            factor,
            detail,
        )
        jitter_path.unlink(missing_ok=True)
        return

    jitter_path.replace(audio_path)


def apply_replaygain(audio_path: pathlib.Path) -> None:
    LOGGER.info("[audio] Applying ReplayGain: %s", audio_path.name)
    try:
        subprocess.run(
            ["mp3gain", "-q", "-r", "-k", str(audio_path)],
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        LOGGER.warning(
            "[audio] mp3gain not available (%s); continuing without ReplayGain.",
            exc,
        )
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.strip() or exc.stdout.strip() or str(exc)
        LOGGER.warning("[audio] ReplayGain failed: %s", detail)
    except OSError as exc:  # pragma: no cover - unexpected OS-level failure
        LOGGER.warning("[audio] ReplayGain skipped due to OS error: %s", exc)


def ensure_story_assets(
    station_slug: str,
    current_track: QueueTrack,
    archetype: Archetype,
    personality: StationPersonality,
    script_text: str,
    rng: random.Random,
) -> StoryAssets:
    safe_artist = sanitize_filename_component(current_track.artist).replace("'", "")
    safe_title = sanitize_filename_component(current_track.title).replace("'", "")
    timestamp = dt.datetime.now()
    date_str = timestamp.strftime("%Y-%m-%d")
    station_dir = STORY_OUTPUT_DIR / station_slug
    target_dir = station_dir / date_str
    target_dir.mkdir(parents=True, exist_ok=True)

    base_name = f"AIHost_{archetype.value}_{safe_artist}_{safe_title}_{timestamp.strftime('%H%M%S')}"
    text_path = target_dir / f"{base_name}.txt"
    audio_path = target_dir / f"{base_name}.mp3"

    text_path.write_text(script_text.strip() + "\n", encoding="utf-8")

    tts_instructions = build_tts_instructions(personality)
    run_with_retries(
        label="TTS synthesis",
        func=lambda: synthesize_speech(
            text=script_text,
            outfile=str(audio_path),
            instructions=tts_instructions,
            gemini_model="gemini-2.5-flash-preview-tts",
        ),
    )

    maybe_apply_speed_jitter(audio_path, rng)
    apply_replaygain(audio_path)

    return StoryAssets(
        text_path=text_path,
        audio_path=audio_path,
        story_text=script_text,
        remote_path="/".join(["AI Stories", date_str, f"{base_name}.mp3"]),
    )


def derive_station_display_name(
    station_payload: Mapping[str, Any], fallback: str
) -> str:
    name = str(station_payload.get("name") or "").strip()
    return name or fallback


def station_name_for_generation(station_slug: str, fallback_name: str) -> str:
    normalized = (station_slug or "").strip().lower()
    return STATION_GENERATION_NAMES.get(normalized, fallback_name)


def resolve_station_personality(station_slug: str) -> StationPersonality:
    normalized = (station_slug or "").strip().lower()
    return STATION_PERSONALITIES.get(normalized, STATION_PERSONALITIES["neuralcast"])


def build_system_prompt(station_name: str, personality: StationPersonality) -> str:
    return (
        f"{_HOST_CONSTITUTION_TEMPLATE.format(station_name=station_name).strip()}\n\n"
        f"{_SCRIPT_STYLE_BASELINE.strip()}\n\n"
        "Station personality profile:\n"
        f"- {personality.script_profile}\n"
    )


def build_tts_instructions(personality: StationPersonality) -> str:
    base = TTS_INSTRUCTIONS_PATH.read_text(encoding="utf-8").strip()
    if not personality.tts_profile.strip():
        return base
    return f"{base}\n\nAjuste de personalidad de estacion:\n{personality.tts_profile}\n"


def build_request_command(
    media_full_path: str, title: str, duration: Optional[int]
) -> str:
    artist = "NeuralCast AI"

    def _escape(value: str) -> str:
        return value.replace("\\", "\\\\").replace('"', '\\"')

    annotations = [
        f'title="{_escape(title)}"',
        f'artist="{_escape(artist)}"',
    ]
    if duration is not None and duration > 0:
        annotations.append(f'duration="{duration}"')
    return f"requests.push annotate:{','.join(annotations)}:{media_full_path}"


def choose_station_payload(
    stations: Sequence[Mapping[str, Any]], station: str
) -> Mapping[str, Any]:
    normalized = station.strip().lower()
    station_entry = next(
        (
            entry
            for entry in stations
            if str(entry.get("shortcode") or entry.get("station_short_name"))
            .strip()
            .lower()
            == normalized
        ),
        None,
    )
    if station_entry is not None:
        return station_entry
    available = ", ".join(str(entry.get("shortcode") or "?") for entry in stations)
    raise RuntimeError(f"Station '{station}' not found. Available: {available}")


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
    state: OrchestratorState,
    rng: random.Random,
    forced_mode: bool,
) -> Tuple[str, Optional[NewsSegment], Archetype]:
    """Generate script and optional structured metadata.

    Returns: (script, news_segment, archetype_used)
    """

    temperature, top_p = sample_generation_settings(archetype, rng)
    system_prompt = build_system_prompt(station_name, personality)
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
    }

    def generate_with_retries(prompt: str, label: str, with_search: bool) -> str:
        return run_with_retries(
            label=label,
            func=lambda: gemini_generate_text(
                prompt=prompt,
                system_prompt=system_prompt,
                temperature=temperature,
                top_p=top_p,
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
        return cleanup_generated_script(generated), None, archetype

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
    for topic_attempt in range(topic_attempts):
        topics = pick_news_topics(story_count, rng)
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
            if forced_mode:
                raise RuntimeError(
                    "Forced news archetype returned NO_SCRIPT; failing as requested for test visibility."
                )
            LOGGER.warning(
                "[news] Gemini returned NO_SCRIPT; falling back to ultra_minimal."
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


def apply_success_state_update(
    state: OrchestratorState,
    ts: float,
    current_track_key: str,
    current_remaining: Optional[int],
    archetype_used: Archetype,
    hook: str,
    angle: Optional[str],
    news_segment: Optional[NewsSegment],
    rng: random.Random,
) -> None:
    state.last_spoken_track_key = current_track_key
    state.last_spoken_ts = ts
    state.last_spoken_expected_end_ts = ts + max(0, current_remaining or 0)

    state.songs_since_last_spoken = 0
    state.songs_until_next_speak = rng.randint(*WAIT_RANGE_SONGS)
    state.next_speak_deadline_ts = ts + SPEAK_DEADLINE_MINUTES * 60

    if archetype_used in COOLDOWN_SECONDS:
        cooldown = COOLDOWN_SECONDS[archetype_used]
        state.cooldown_until[archetype_used.value] = ts + cooldown

    state.recent_archetypes = [archetype_used.value]
    state.recent_hooks = [hook]

    if angle and archetype_used in ANGLE_OPTIONS:
        state.last_angle_by_archetype[archetype_used.value] = angle

    state.recent_news_dedup = prune_news_history(state.recent_news_dedup, ts)
    if news_segment is not None:
        record_news_history(state, news_segment, ts)


def update_track_seen_state(
    state: OrchestratorState, current_track_key: str, ts: float
) -> None:
    if state.last_seen_track_key != current_track_key:
        if state.last_seen_track_key is not None:
            state.songs_since_last_spoken += 1
        state.last_seen_track_key = current_track_key
        state.last_seen_ts = ts


def extract_upload_storage_path(upload_response: Mapping[str, Any]) -> Optional[str]:
    path = upload_response.get("path")
    if isinstance(path, str) and path.strip():
        return path.strip()

    data = upload_response.get("data")
    if isinstance(data, Mapping):
        for key in ("path", "storage_location"):
            candidate = data.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
    return None


def extract_upload_duration(upload_response: Mapping[str, Any]) -> Optional[int]:
    candidates = [upload_response.get("length")]
    if isinstance(upload_response.get("data"), Mapping):
        candidates.append(upload_response["data"].get("length"))
    for candidate in candidates:
        if candidate is None:
            continue
        try:
            return int(float(candidate))
        except (TypeError, ValueError):
            continue
    return None


def extract_telnet_request_id(response_payload: Mapping[str, Any]) -> Optional[str]:
    logs = response_payload.get("logs")
    if not isinstance(logs, list):
        return None
    for entry in reversed(logs):
        context = entry.get("context")
        if not isinstance(context, Mapping):
            continue
        lines = context.get("response")
        if isinstance(lines, list) and lines:
            return str(lines[-1])
    return None


def cleanup_local_stories(station_slug: str, keep_days: int) -> None:
    if keep_days <= 0:
        return

    base_dir = STORY_OUTPUT_DIR / station_slug
    if not base_dir.exists():
        return

    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=keep_days)
    for file_path in base_dir.rglob("*"):
        if not file_path.is_file() or file_path.suffix.lower() not in {".mp3", ".txt"}:
            continue
        try:
            mtime = dt.datetime.fromtimestamp(
                file_path.stat().st_mtime, tz=dt.timezone.utc
            )
        except OSError:
            continue
        if mtime < cutoff:
            file_path.unlink(missing_ok=True)


def cleanup_remote_stories(
    client: AzuraCastClient, station_slug: str, keep_days: int
) -> None:
    if keep_days <= 0:
        return

    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=keep_days)
    cutoff_ts = cutoff.timestamp()
    try:
        media_files = client.list_media_files(station_slug)
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("[cleanup] Unable to list remote media files: %s", exc)
        return

    for entry in media_files:
        path = str(entry.get("path") or "")
        if not path.startswith("AI Stories/"):
            continue
        mtime = entry.get("mtime")
        media_id = entry.get("id") or entry.get("media_id")
        if mtime is None or media_id is None:
            continue
        try:
            if float(mtime) >= cutoff_ts:
                continue
            client.delete_media_file(station_slug, int(media_id))
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning(
                "[cleanup] Failed deleting remote story file '%s' (media_id=%s): %s",
                path,
                media_id,
                exc,
            )


def run(args: argparse.Namespace) -> None:
    configure_logging()

    load_dotenv()
    api_key = os.getenv("AZURACAST_API_KEY")
    if not api_key:
        raise RuntimeError("AZURACAST_API_KEY is not set in the environment.")

    rng = random.Random()
    cycle_ts = now_ts()
    station_dir, state_path, lock_path = station_state_paths(args.station)
    lock = StationLock(lock_path)
    if not lock.acquire():
        return

    LOGGER.info(
        "[cycle] Starting orchestrator cycle | station=%s | dry_run=%s",
        args.station,
        args.dry_run,
    )

    state = load_state(state_path, cycle_ts, rng)
    LOGGER.info("[state] Loaded orchestrator state: %s", state_path)

    try:
        client = AzuraCastClient(
            base_url=args.base_url.rstrip("/"),
            api_key=api_key,
            verify_tls=args.verify_tls,
        )

        stations = run_with_retries("Fetch stations", client.get_stations)
        station_payload = choose_station_payload(stations, args.station)
        station_id_raw = station_payload.get("id")
        station_id = int(station_id_raw) if station_id_raw is not None else None
        if station_id is None:
            raise RuntimeError(
                "Station payload missing station ID; cannot queue media."
            )

        station_name = derive_station_display_name(
            station_payload, fallback=args.station
        )
        generation_station_name = station_name_for_generation(
            args.station, station_name
        )
        station_personality = resolve_station_personality(args.station)
        LOGGER.info("[station] Personality profile active: %s", args.station)

        try:
            now_playing_payload = run_with_retries(
                "Fetch now-playing",
                lambda: client.get_now_playing(args.station),
            )
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning(
                "[now-playing] Fetch failed after retries: %s. Skipping cycle.",
                exc,
            )
            return

        current_track, current_remaining = extract_current_track(now_playing_payload)
        current_key = track_key(current_track.artist, current_track.title)
        update_track_seen_state(state, current_key, now_ts())

        LOGGER.info(
            "[now-playing] Current track: %s - %s",
            current_track.artist,
            current_track.title,
        )

        listener_count = extract_current_listeners(now_playing_payload)
        if listener_count is None:
            LOGGER.info("[listeners] Current listeners: unavailable")
        else:
            LOGGER.info("[listeners] Current listeners: %s", listener_count)

        if args.min_listeners > 0:
            if listener_count is None:
                LOGGER.info(
                    "[gate] Listener count unavailable with --min-listeners=%s; skipping cycle.",
                    args.min_listeners,
                )
                return
            if listener_count < args.min_listeners:
                LOGGER.info(
                    "[gate] Only %s listener(s) connected (< %s); skipping cycle.",
                    listener_count,
                    args.min_listeners,
                )
                return

        if current_remaining is None:
            LOGGER.info(
                "[gate] Current remaining time unavailable; skipping for lead-time safety."
            )
            return
        if current_remaining < LEAD_TIME_SECONDS:
            LOGGER.info(
                "[gate] Current track has only %ss remaining (< %ss); skipping cycle.",
                current_remaining,
                LEAD_TIME_SECONDS,
            )
            return

        queue_payload = run_with_retries(
            "Fetch queue",
            lambda: client.get_upcoming_queue(args.station),
        )
        queue_tracks = parse_queue_tracks(queue_payload)
        next_track = choose_next_track(current_track, queue_tracks)
        if next_track is None:
            LOGGER.info("[queue] No suitable next track found; skipping cycle.")
            return

        LOGGER.info("[queue] Next track: %s - %s", next_track.artist, next_track.title)

        forced_archetype = (
            Archetype(args.force_archetype) if args.force_archetype else None
        )

        if forced_archetype is None:
            eligible, wait_reason = should_speak_now(state, current_key, now_ts())
            if not eligible:
                LOGGER.info("[gate] Wait gate closed: %s", wait_reason)
                return
            LOGGER.info("[gate] Wait gate open: %s", wait_reason)
        else:
            LOGGER.info(
                "[gate] Force archetype active: %s; bypassing wait gate.",
                forced_archetype.value,
            )

        if forced_archetype is not None:
            selected_archetype = forced_archetype
        else:
            legal = legal_archetypes(state, now_ts())
            if legal:
                selected_archetype = choose_weighted_archetype(legal, state, rng)
                LOGGER.info(
                    "[archetype] Legal archetypes: %s",
                    [item.value for item in legal],
                )
            else:
                selected_archetype = Archetype.ULTRA_MINIMAL
                LOGGER.warning(
                    "[archetype] No legal archetypes available after cooldowns; using ultra_minimal."
                )

        angle = choose_angle(selected_archetype, state, rng)
        hook = choose_hook(selected_archetype, state, rng)
        banned_list = assemble_banned_list(state)

        metadata_cache = load_station_track_metadata(station_dir)
        current_meta = metadata_cache.get(current_key, TrackMetadata())
        next_meta = metadata_cache.get(
            track_key(next_track.artist, next_track.title), TrackMetadata()
        )

        LOGGER.info(
            "[generation] archetype=%s | angle=%s | hook=%s",
            selected_archetype.value,
            angle or "none",
            hook,
        )

        script_text, news_segment, archetype_used = generate_archetype_script(
            archetype=selected_archetype,
            station_name=generation_station_name,
            personality=station_personality,
            current_track=current_track,
            next_track=next_track,
            current_meta=current_meta,
            next_meta=next_meta,
            angle=angle,
            hook=hook,
            banned_list=banned_list,
            state=state,
            rng=rng,
            forced_mode=forced_archetype == Archetype.NEWS,
        )

        if not script_text.strip():
            raise RuntimeError("Generated script was empty after cleanup.")

        assets = ensure_story_assets(
            station_slug=args.station,
            current_track=current_track,
            archetype=archetype_used,
            personality=station_personality,
            script_text=script_text,
            rng=rng,
        )
        LOGGER.info("[assets] Script saved: %s", assets.text_path)
        LOGGER.info("[assets] Audio saved: %s", assets.audio_path)

        if args.dry_run:
            LOGGER.info(
                "[dry-run] Skipping upload/injection; cadence and cooldowns are not consumed."
            )
            return

        upload_response = run_with_retries(
            "Upload media",
            lambda: client.upload_media(
                args.station,
                assets.audio_path,
                remote_path=assets.remote_path,
            ),
        )

        upload_path = extract_upload_storage_path(upload_response)
        if not upload_path:
            raise RuntimeError("Upload response missing storage path.")

        story_duration = extract_upload_duration(upload_response)
        full_media_path = f"/var/azuracast/stations/{args.station}/media/{upload_path}"
        telnet_command = build_request_command(
            media_full_path=full_media_path,
            title=f"AI Host: {current_track.title}",
            duration=story_duration,
        )

        telnet_response = run_with_retries(
            "Queue media via telnet",
            lambda: client.send_telnet_command(station_id, telnet_command),
        )
        request_id = extract_telnet_request_id(telnet_response)
        if request_id:
            LOGGER.info("[queue] Queued via requests.push | request_id=%s", request_id)
        else:
            LOGGER.info("[queue] Queued via requests.push.")

        success_ts = now_ts()
        apply_success_state_update(
            state=state,
            ts=success_ts,
            current_track_key=current_key,
            current_remaining=current_remaining,
            archetype_used=archetype_used,
            hook=hook,
            angle=angle,
            news_segment=news_segment,
            rng=rng,
        )

        cleanup_local_stories(args.station, args.keep_local_days)
        cleanup_remote_stories(client, args.station, args.keep_remote_days)

    finally:
        save_state_atomic(state_path, state)
        LOGGER.info("[state] Saved orchestrator state: %s", state_path)
        lock.release()


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Dynamic AI host orchestrator for AzuraCast: stateful cadence, archetype "
            "selection, and spoken segment injection."
        )
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv("AZURACAST_BASE_URL", "https://192.168.1.226"),
        help="Base URL for AzuraCast instance (default: %(default)s).",
    )
    parser.add_argument(
        "-s",
        "--station",
        default=os.getenv("AZURACAST_STATION", "neuralcast"),
        help="AzuraCast station shortcode (default: %(default)s).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Generate text/audio locally without upload or queue injection.",
    )
    parser.add_argument(
        "--min-listeners",
        type=int,
        default=1,
        help=(
            "Require at least this many listeners before generation/injection "
            "(default: %(default)s; set 0 to disable)."
        ),
    )
    parser.add_argument(
        "--force-archetype",
        choices=[archetype.value for archetype in Archetype],
        help=(
            "Testing override: bypass wait gate/cooldowns and force this archetype. "
            "Still enforces listener and lead-time gates."
        ),
    )
    parser.add_argument(
        "--verify-tls",
        action="store_true",
        help="Verify TLS certificates for AzuraCast requests.",
    )
    parser.add_argument(
        "--keep-local-days",
        type=int,
        default=3,
        help="Retain local AI story assets for this many days (default: %(default)s).",
    )
    parser.add_argument(
        "--keep-remote-days",
        type=int,
        default=7,
        help="Retain remote AI story assets for this many days (default: %(default)s).",
    )
    return parser


if __name__ == "__main__":
    run(build_arg_parser().parse_args())
