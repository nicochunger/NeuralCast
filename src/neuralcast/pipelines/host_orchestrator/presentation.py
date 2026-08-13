"""Deterministic listener-facing presentation for generated host segments."""

from __future__ import annotations

from typing import Iterable, Optional, Sequence

from .models import (
    Archetype,
    GeneratedSegmentMetadata,
    QueueTrack,
    ScheduleContext,
    TrackFocus,
    TrackMetadata,
)

MAX_SEGMENT_TITLE_LENGTH = 100

_ARCHETYPE_LABELS = {
    Archetype.BACK_SELL: "Puente musical",
    Archetype.UP_NEXT_TEASE: "Lo que viene",
    Archetype.SHORT_STORY: "Historia del tema",
    Archetype.ALBUM_SPOTLIGHT: "Álbum en foco",
    Archetype.ERA_SNAPSHOT: "Postal de época",
    Archetype.DEEP_DIVE: "Contexto a fondo",
    Archetype.NEWS: "Panorama de noticias",
    Archetype.CONCERT_CHECK: "Agenda en vivo",
    Archetype.BLOCK_INTRO: "Inicio de bloque",
    Archetype.ULTRA_MINIMAL: "Pase musical",
}

def _clean(value: object) -> str:
    return " ".join(str(value or "").split()).strip()


def _truncate(value: str, limit: int = MAX_SEGMENT_TITLE_LENGTH) -> str:
    text = _clean(value)
    if len(text) <= limit:
        return text
    return f"{text[: max(1, limit - 3)].rstrip(' :->-')}..."


def _track_label(track: Optional[QueueTrack]) -> str:
    if track is None:
        return ""
    artist = _clean(track.artist)
    title = _clean(track.title)
    if artist and title:
        return f"{artist} - {title}"
    return artist or title


def _focus_position(focus: TrackFocus) -> str:
    return "Recién sonó" if focus == TrackFocus.CURRENT else "Ahora viene"


def _focused_track(
    focus: Optional[TrackFocus],
    current_track: QueueTrack,
    next_track: QueueTrack,
    current_meta: TrackMetadata,
    next_meta: TrackMetadata,
) -> tuple[TrackFocus, QueueTrack, TrackMetadata]:
    # Rich archetypes always provide an explicit focus in production. Defaulting
    # to CURRENT keeps titles useful for older/custom generator dependencies.
    effective_focus = focus or TrackFocus.CURRENT
    if effective_focus == TrackFocus.NEXT:
        return effective_focus, next_track, next_meta
    return effective_focus, current_track, current_meta


def _join_names(names: Iterable[str]) -> str:
    unique: list[str] = []
    seen: set[str] = set()
    for value in names:
        name = _clean(value)
        key = name.casefold()
        if name and key not in seen:
            unique.append(name)
            seen.add(key)

    if len(unique) <= 1:
        return unique[0] if unique else ""
    if len(unique) == 2:
        return f"{unique[0]} y {unique[1]}"
    return f"{', '.join(unique[:-1])} y {unique[-1]}"


def _track_focus_title(
    label: str,
    focus: Optional[TrackFocus],
    current_track: QueueTrack,
    next_track: QueueTrack,
    current_meta: TrackMetadata,
    next_meta: TrackMetadata,
    *,
    include_album: bool = False,
    include_year: bool = False,
) -> str:
    effective_focus, track, metadata = _focused_track(
        focus,
        current_track,
        next_track,
        current_meta,
        next_meta,
    )
    subject = _track_label(track)
    if include_album and _clean(metadata.album):
        subject = f"{subject} ({_clean(metadata.album)})"
    if include_year and _clean(metadata.year):
        subject = f"{subject} - {_clean(metadata.year)}"
    position = _focus_position(effective_focus)
    return f"{label}: {position} - {subject or 'tema sin identificar'}"


def build_segment_title(
    *,
    archetype: Archetype,
    current_track: QueueTrack,
    next_track: QueueTrack,
    upcoming_tracks: Sequence[QueueTrack] = (),
    current_meta: Optional[TrackMetadata] = None,
    next_meta: Optional[TrackMetadata] = None,
    segment_metadata: Optional[GeneratedSegmentMetadata] = None,
    schedule_context: Optional[ScheduleContext] = None,
) -> str:
    """Build a short, factual title without another model or network call."""

    current_meta = current_meta or TrackMetadata()
    next_meta = next_meta or TrackMetadata()
    metadata = segment_metadata or GeneratedSegmentMetadata()
    label = _ARCHETYPE_LABELS.get(archetype, "Segmento musical")

    if archetype == Archetype.BACK_SELL:
        current_label = _track_label(current_track) or "tema anterior"
        next_label = _track_label(next_track) or "próximo tema"
        title = f"{label}: {current_label} -> {next_label}"
    elif archetype == Archetype.UP_NEXT_TEASE:
        artists = _join_names(track.artist for track in upcoming_tracks[:4])
        subject = artists or _track_label(next_track) or "próximo tema"
        title = f"{label}: {subject}"
    elif archetype == Archetype.SHORT_STORY:
        title = _track_focus_title(
            label,
            metadata.track_focus,
            current_track,
            next_track,
            current_meta,
            next_meta,
        )
    elif archetype == Archetype.ALBUM_SPOTLIGHT:
        title = _track_focus_title(
            label,
            metadata.track_focus,
            current_track,
            next_track,
            current_meta,
            next_meta,
            include_album=True,
        )
    elif archetype == Archetype.ERA_SNAPSHOT:
        title = _track_focus_title(
            label,
            metadata.track_focus,
            current_track,
            next_track,
            current_meta,
            next_meta,
            include_year=True,
        )
    elif archetype == Archetype.DEEP_DIVE:
        title = _track_focus_title(
            label,
            metadata.track_focus,
            current_track,
            next_track,
            current_meta,
            next_meta,
        )
    elif archetype == Archetype.NEWS:
        topics = _join_names(
            story.topic
            for story in (
                metadata.news_segment.stories if metadata.news_segment else []
            )
        )
        title = f"{label}: {topics or 'actualidad'}"
    elif archetype == Archetype.CONCERT_CHECK:
        events = metadata.concert_segment.events if metadata.concert_segment else []
        artists = _join_names(event.artist for event in events)
        city = _clean(events[0].city) if events else ""
        subject = artists or "agenda de shows"
        if city:
            subject = f"{subject} - {city}"
        title = f"{label}: {subject}"
    elif archetype == Archetype.BLOCK_INTRO:
        subject = _clean(schedule_context.section_label) if schedule_context else ""
        if not subject and schedule_context:
            subject = _join_names(schedule_context.genre_labels[:2])
        title = f"{label}: {subject or 'nueva sección'}"
    elif archetype == Archetype.ULTRA_MINIMAL:
        next_label = _track_label(next_track) or _track_label(current_track)
        title = f"{label}: Ahora viene - {next_label or 'música'}"
    else:  # pragma: no cover - defensive fallback for future archetypes
        title = f"{label}: {_track_label(current_track) or 'música'}"

    return _truncate(title)
