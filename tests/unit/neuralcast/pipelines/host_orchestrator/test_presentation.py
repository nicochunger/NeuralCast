"""Unit tests for listener-facing AI host segment titles."""

from __future__ import annotations

from neuralcast.pipelines.host_orchestrator.models import (
    Archetype,
    ConcertEventMeta,
    ConcertSegment,
    GeneratedSegmentMetadata,
    NewsSegment,
    NewsStoryMeta,
    QueueTrack,
    TrackFocus,
    TrackMetadata,
)
from neuralcast.pipelines.host_orchestrator.presentation import build_segment_title


def _track(artist: str, title: str) -> QueueTrack:
    return QueueTrack(
        queue_id=f"{artist}-{title}",
        song_id=None,
        artist=artist,
        title=title,
        duration=240,
    )


def test_track_focused_titles_identify_current_or_next_track() -> None:
    current = _track("Ghost", "Rats")
    upcoming = _track("Opeth", "Harvest")
    metadata = GeneratedSegmentMetadata(track_focus=TrackFocus.NEXT)

    title = build_segment_title(
        archetype=Archetype.SHORT_STORY,
        current_track=current,
        next_track=upcoming,
        current_meta=TrackMetadata(album="Prequelle"),
        next_meta=TrackMetadata(album="Blackwater Park"),
        segment_metadata=metadata,
    )

    assert title == "Historia del tema: Ahora viene - Opeth - Harvest"

    current_title = build_segment_title(
        archetype=Archetype.DEEP_DIVE,
        current_track=current,
        next_track=upcoming,
        segment_metadata=GeneratedSegmentMetadata(track_focus=TrackFocus.CURRENT),
    )
    assert current_title == "Contexto a fondo: Recién sonó - Ghost - Rats"


def test_album_and_era_titles_include_relevant_track_metadata() -> None:
    current = _track("Ghost", "Rats")
    upcoming = _track("Opeth", "Harvest")

    album_title = build_segment_title(
        archetype=Archetype.ALBUM_SPOTLIGHT,
        current_track=current,
        next_track=upcoming,
        current_meta=TrackMetadata(album="Prequelle"),
        next_meta=TrackMetadata(album="Blackwater Park"),
        segment_metadata=GeneratedSegmentMetadata(track_focus=TrackFocus.NEXT),
    )
    era_title = build_segment_title(
        archetype=Archetype.ERA_SNAPSHOT,
        current_track=current,
        next_track=upcoming,
        next_meta=TrackMetadata(year="2011"),
        segment_metadata=GeneratedSegmentMetadata(track_focus=TrackFocus.NEXT),
    )

    assert album_title == (
        "Álbum en foco: Ahora viene - Opeth - Harvest (Blackwater Park)"
    )
    assert era_title == "Postal de época: Ahora viene - Opeth - Harvest - 2011"


def test_news_and_concert_titles_use_validated_segment_facts() -> None:
    current = _track("Ghost", "Rats")
    upcoming = _track("Opeth", "Harvest")
    news = GeneratedSegmentMetadata(
        news_segment=NewsSegment(
            script="script",
            story_count=2,
            stories=[
                NewsStoryMeta("Science", "Science headline", "https://example.com/1"),
                NewsStoryMeta("Tech/AI", "AI headline", "https://example.com/2"),
            ],
        )
    )
    concert = GeneratedSegmentMetadata(
        concert_segment=ConcertSegment(
            script="script",
            events=[
                ConcertEventMeta(
                    artist="Opeth",
                    country="Argentina",
                    city="Buenos Aires",
                    venue="Luna Park",
                    event_date="2030-01-01",
                    source_url="https://example.com/show",
                )
            ],
        )
    )

    assert build_segment_title(
        archetype=Archetype.NEWS,
        current_track=current,
        next_track=upcoming,
        segment_metadata=news,
    ) == "Panorama de noticias: Science y Tech/AI"
    assert build_segment_title(
        archetype=Archetype.CONCERT_CHECK,
        current_track=current,
        next_track=upcoming,
        segment_metadata=concert,
    ) == "Agenda en vivo: Opeth - Buenos Aires"


def test_bridge_and_fallback_titles_are_track_accurate() -> None:
    current = _track("Ghost", "Rats")
    upcoming = _track("Opeth", "Harvest")

    assert build_segment_title(
        archetype=Archetype.BACK_SELL,
        current_track=current,
        next_track=upcoming,
    ) == "Puente musical: Ghost - Rats -> Opeth - Harvest"
    assert build_segment_title(
        archetype=Archetype.ULTRA_MINIMAL,
        current_track=current,
        next_track=upcoming,
    ) == "Pase musical: Ahora viene - Opeth - Harvest"
