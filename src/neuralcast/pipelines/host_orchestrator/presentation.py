"""Deterministic listener-facing presentation for generated host segments."""

from __future__ import annotations

from typing import Iterable, Optional, Sequence

from .channels import HostLocale, get_channel_registry
from .models import (
    Archetype,
    GeneratedSegmentMetadata,
    QueueTrack,
    ScheduleContext,
    TrackFocus,
    TrackMetadata,
)

MAX_SEGMENT_TITLE_LENGTH = 100


def _resolved_locale(locale: Optional[HostLocale]) -> HostLocale:
    return locale or get_channel_registry().locales["es-AR"]

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


def _focus_position(focus: TrackFocus, locale: HostLocale) -> str:
    key = "current" if focus == TrackFocus.CURRENT else "next"
    return str(locale.presentation[key])


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


def _join_names(names: Iterable[str], conjunction: str = "y") -> str:
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
        return f"{unique[0]} {conjunction} {unique[1]}"
    return f"{', '.join(unique[:-1])} {conjunction} {unique[-1]}"


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
    locale: HostLocale,
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
    position = _focus_position(effective_focus, locale)
    unknown_track = str(locale.presentation["unknown_track"])
    return f"{label}: {position} - {subject or unknown_track}"


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
    locale: Optional[HostLocale] = None,
) -> str:
    """Build a short, factual title without another model or network call."""

    current_meta = current_meta or TrackMetadata()
    next_meta = next_meta or TrackMetadata()
    metadata = segment_metadata or GeneratedSegmentMetadata()
    locale = _resolved_locale(locale)
    presentation = locale.presentation
    label = str(presentation.get(archetype.value) or presentation["default"])
    conjunction = str(presentation["and"])

    if archetype == Archetype.BACK_SELL:
        current_label = _track_label(current_track) or str(presentation["previous_track"])
        next_label = _track_label(next_track) or str(presentation["next_track"])
        title = f"{label}: {current_label} -> {next_label}"
    elif archetype == Archetype.UP_NEXT_TEASE:
        artists = _join_names(
            (track.artist for track in upcoming_tracks[:4]), conjunction
        )
        subject = artists or _track_label(next_track) or str(presentation["next_track"])
        title = f"{label}: {subject}"
    elif archetype == Archetype.SHORT_STORY:
        title = _track_focus_title(
            label,
            metadata.track_focus,
            current_track,
            next_track,
            current_meta,
            next_meta,
            locale=locale,
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
            locale=locale,
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
            locale=locale,
        )
    elif archetype == Archetype.DEEP_DIVE:
        title = _track_focus_title(
            label,
            metadata.track_focus,
            current_track,
            next_track,
            current_meta,
            next_meta,
            locale=locale,
        )
    elif archetype == Archetype.NEWS:
        topics = _join_names(
            (
                story.topic
                for story in (
                metadata.news_segment.stories if metadata.news_segment else []
                )
            ),
            conjunction,
        )
        title = f"{label}: {topics or presentation['current_affairs']}"
    elif archetype == Archetype.CONCERT_CHECK:
        events = metadata.concert_segment.events if metadata.concert_segment else []
        artists = _join_names((event.artist for event in events), conjunction)
        city = _clean(events[0].city) if events else ""
        subject = artists or str(presentation["concerts"])
        if city:
            subject = f"{subject} - {city}"
        title = f"{label}: {subject}"
    elif archetype == Archetype.BLOCK_INTRO:
        subject = _clean(schedule_context.section_label) if schedule_context else ""
        if not subject and schedule_context:
            subject = _join_names(schedule_context.genre_labels[:2], conjunction)
        title = f"{label}: {subject or presentation['new_section']}"
    elif archetype == Archetype.ULTRA_MINIMAL:
        next_label = _track_label(next_track) or _track_label(current_track)
        title = f"{label}: {presentation['next']} - {next_label or presentation['music']}"
    else:  # pragma: no cover - defensive fallback for future archetypes
        title = f"{label}: {_track_label(current_track) or presentation['music']}"

    return _truncate(title)
