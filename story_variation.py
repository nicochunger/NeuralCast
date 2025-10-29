"""Helpers to introduce deterministic storytelling and TTS variation."""

from __future__ import annotations

import hashlib
import json
import pathlib
import random
import time
import warnings
from dataclasses import dataclass
from typing import Callable, Dict, Iterator, List, Optional, Sequence, Tuple, TypeVar


@dataclass(frozen=True)
class NarrativeVariant:
    """Instructions that nudge the story prompt toward a specific angle."""

    style_id: str
    description: str
    intro_instruction: str
    body_instruction: str
    outro_instruction: str
    filler_words: str


@dataclass(frozen=True)
class DeliveryVariant:
    """Guidance to adjust the delivery of the TTS narration."""

    style_id: str
    description: str
    delivery_instruction: str
    pace_instruction: str
    additional_prompts: str


NARRATIVE_VARIANTS: Sequence[NarrativeVariant] = (
    NarrativeVariant(
        style_id="warm-anecdote",
        description="Arranque cálido con recuerdo personal y giro anecdótico ligero.",
        intro_instruction="Arrancá como si recordarás una tarde de radio en que descubriste la canción, con un saludo cercano.",
        body_instruction="Sumá un detalle concreto de la historia del tema, contado como anécdota que escuchaste entre colegas o en la producción.",
        outro_instruction="Cerrá destacando por qué combina bien con la próxima canción, enlazando sensaciones.",
        filler_words="meté muletillas suaves como 'mirá', 'sabés que', 'posta' para mantener la charla viva.",
    ),
    NarrativeVariant(
        style_id="curious-fact",
        description="Giro curiosidad, resaltando un dato poco conocido con tono cómplice.",
        intro_instruction="Empezá sorprendiendo con un 'vos sabés que...' como si recién hubieras descubierto un dato escondido.",
        body_instruction="Contá un detalle curioso o poco difundido del proceso creativo o una colaboración inesperada.",
        outro_instruction="Invitá al oyente a quedarse para escuchar el próximo tema porque complementa esa curiosidad.",
        filler_words="usá coletillas como 'te juro', 'de hecho', 'lo loco es que' para reforzar complicidad.",
    ),
    NarrativeVariant(
        style_id="listener-memory",
        description="Se enfoca en recuerdos compartidos y escenas cotidianas con la audiencia.",
        intro_instruction="Abrí evocando una escena cotidiana en la que esta canción suele aparecer, como un viaje en auto o una sobremesa.",
        body_instruction="Conectá la letra o la melodía con una sensación compartida, como mates entre amigas o un paseo por la ciudad.",
        outro_instruction="Presentá la próxima canción como continuidad de ese momento compartido.",
        filler_words="sumá expresiones como 'viste', 'no sé si te pasa', 'me encanta cuando' para generar cercanía.",
    ),
    NarrativeVariant(
        style_id="studio-behind-scenes",
        description="Relato detrás de escena desde la cabina del estudio.",
        intro_instruction="Presentá la canción desde la perspectiva del estudio, como si estuvieras mostrando la consola a alguien.",
        body_instruction="Compartí un detalle técnico o de producción que hayas comentado al aire con el equipo.",
        outro_instruction="Dale paso al próximo tema como si fuera parte de la programación cuidada del estudio.",
        filler_words="incorporá frases como 'acá entre nosotros', 'te cuento', 'en cabina' para pintar el ambiente.",
    ),
    NarrativeVariant(
        style_id="city-walk",
        description="Paseo por Buenos Aires mientras suena la canción.",
        intro_instruction="Invitá a caminar por un rincón de Buenos Aires evocando sonidos de la ciudad que combinan con la canción.",
        body_instruction="Mencioná detalles sensoriales —olor a café, tranvías imaginarios, alguna plaza— enlazados con el tema.",
        outro_instruction="Cerrá sugiriendo que la próxima canción acompaña la misma caminata con otro ánimo.",
        filler_words="sumá frases como 'ponele', 'me pasa que', 'medio así' para mantener ritmo relajado.",
    ),
)

DELIVERY_VARIANTS: Sequence[DeliveryVariant] = (
    DeliveryVariant(
        style_id="calm-late-night",
        description="Entonación calma, respiraciones amplias, clima nocturno.",
        delivery_instruction="Pedile a la voz que suene como al final de la noche en cabina, con un susurro amable al presentar el dato clave.",
        pace_instruction="Indicá que se tome microsilencios antes de cada frase importante, manteniendo tempo lento.",
        additional_prompts="Aclarar que las sonrisas son apenas insinuadas, casi como quien charla con luces bajas.",
    ),
    DeliveryVariant(
        style_id="bright-morning",
        description="Toque matinal con energía contenida y ritmo dinámico.",
        delivery_instruction="Sugerí que deje entrar un poquito más de brillo al mencionar el artista, como café de la mañana.",
        pace_instruction="Pedí un ritmo apenas más ágil, con pausas cortas y marcadas para sostener claridad.",
        additional_prompts="Recordá que no se convierta en euforia: solo un entusiasmo suave y confiable.",
    ),
    DeliveryVariant(
        style_id="storyteller-intimate",
        description="Narración íntima, como confidencia uno a uno.",
        delivery_instruction="Explicá que debe sonar como quien comparte un secreto con un oyente en particular.",
        pace_instruction="Invitá a dejar un silencio notable entre el clímax de la anécdota y el anticipo del siguiente tema.",
        additional_prompts="Reforzá que las respiraciones se oigan naturales y que cierre con un suspiro apenas perceptible.",
    ),
    DeliveryVariant(
        style_id="rhythmic-groove",
        description="Cadencia con leve swing, acompañando el beat de la canción anterior.",
        delivery_instruction="Solicitá que marque un pulso suave con la voz, como si siguiera golpeando el pie al ritmo del tema.",
        pace_instruction="Pedí pausas sincronizadas con compases de cuatro tiempos, dando sensación de groove.",
        additional_prompts="Mencioná que resalte palabras clave con una micro-subida de energía y vuelva al tono cálido inmediatamente.",
    ),
    DeliveryVariant(
        style_id="sunset-reflection",
        description="Reflexivo, evocando atardecer y cierre de jornada.",
        delivery_instruction="Indicá que se escuche como quien mira el atardecer desde la ventana del estudio.",
        pace_instruction="Solicitá un tempo medio con caídas suaves al final de cada frase.",
        additional_prompts="Sumá que al presentar la canción siguiente deje una sonrisa audible y un 'quedate ahí' muy sutil.",
    ),
)


T = TypeVar("T")


def _hash_to_int(seed: str) -> int:
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    return int(digest[:16], 16)


def compute_story_seed(
    station: str,
    artist: str,
    title: str,
    next_artist: str,
    next_title: str,
) -> str:
    """Build a stable seed string from story context values."""

    parts = [
        station.strip().lower(),
        artist.strip().lower(),
        title.strip().lower(),
        next_artist.strip().lower(),
        next_title.strip().lower(),
    ]
    combined = "|".join(parts)
    digest = hashlib.sha256(combined.encode("utf-8")).hexdigest()
    return digest


def deterministic_variant_choice(
    seed: str,
    variants: Sequence[T],
    recent_ids: Sequence[str],
    avoid_window: int,
    id_getter: Optional[Callable[[T], str]] = None,
) -> Tuple[int, T]:
    """Choose a variant deterministically while avoiding the most recent IDs.

    Example:
        >>> seed = compute_story_seed("neuralcast", "Artista", "Tema", "Proximo", "Cancion")
        >>> idx, variant = deterministic_variant_choice(seed, NARRATIVE_VARIANTS, [], 2)
        >>> variant.style_id in {item.style_id for item in NARRATIVE_VARIANTS}
        True
    """

    if not variants:
        raise ValueError("variants must not be empty")

    if id_getter is None:
        id_getter = lambda item: getattr(item, "style_id", "")

    rng = random.Random(_hash_to_int(seed))
    order = list(range(len(variants)))
    rng.shuffle(order)

    recent_list = list(recent_ids)
    avoided = set(recent_list[:avoid_window]) if avoid_window > 0 else set()
    for index in order:
        candidate = variants[index]
        candidate_id = id_getter(candidate)
        if candidate_id not in avoided:
            return index, candidate

    # Fallback: deterministic first element from shuffled order
    fallback_index = order[0]
    return fallback_index, variants[fallback_index]


StyleHistory = Dict[str, List[Dict[str, str]]]


def load_style_history(path: pathlib.Path) -> StyleHistory:
    """Load persisted style history; return empty mapping when missing or invalid."""

    if not path.exists():
        return {}

    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError as exc:
        warnings.warn(f"No se pudo leer el historial de estilos en {path}: {exc}")
        return {}

    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        warnings.warn(
            f"El historial de estilos en {path} está dañado ({exc}); se reiniciará."
        )
        return {}

    if not isinstance(payload, dict):
        warnings.warn(f"El historial de estilos en {path} tiene formato inesperado.")
        return {}

    history: StyleHistory = {}
    for station, entries in payload.items():
        if not isinstance(entries, list):
            continue
        normalized_entries: List[Dict[str, str]] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            seed = str(entry.get("seed") or "")
            narrative_id = str(entry.get("narrative_id") or "")
            delivery_id = str(entry.get("delivery_id") or "")
            timestamp = str(entry.get("timestamp") or "")
            normalized_entries.append(
                {
                    "seed": seed,
                    "narrative_id": narrative_id,
                    "delivery_id": delivery_id,
                    "timestamp": timestamp,
                }
            )
        if normalized_entries:
            history[str(station)] = normalized_entries
    return history


def update_style_history(
    history: StyleHistory,
    station: str,
    seed: str,
    narrative_id: str,
    delivery_id: str,
    max_entries: int,
) -> None:
    """Append a new record for the given station, trimming older entries."""

    station_key = station.strip().lower()
    station_entries = history.setdefault(station_key, [])
    station_entries.append(
        {
            "seed": seed,
            "narrative_id": narrative_id,
            "delivery_id": delivery_id,
            "timestamp": str(int(time.time())),
        }
    )
    if max_entries > 0 and len(station_entries) > max_entries:
        del station_entries[:-max_entries]


def save_style_history(path: pathlib.Path, history: StyleHistory) -> None:
    """Persist the history atomically."""

    tmp_path = path.with_suffix(".tmp")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path.write_text(json.dumps(history, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp_path.replace(path)


def iter_recent_ids(
    history: StyleHistory,
    station: str,
    key: str,
) -> Iterator[str]:
    """Yield recent narrative or delivery IDs for a station."""

    station_key = station.strip().lower()
    for entry in reversed(history.get(station_key, [])):
        value = entry.get(key)
        if value:
            yield value
