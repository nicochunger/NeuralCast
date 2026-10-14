"""Listener summaries and estimated-airtime card selection."""

import json
import random
from unittest.mock import Mock

import pytest

from neuralcast.pipelines.host_orchestrator import companion, generation
from neuralcast.pipelines.host_orchestrator.models import (
    Archetype,
    GeneratedSegmentMetadata,
    QueueTrack,
    TrackMetadata,
    TrackFocus,
    NewsSegment,
    NewsStoryMeta,
    ConcertSegment,
    ConcertEventMeta,
)
from neuralcast.pipelines.host_orchestrator.state import default_state

SUMMARY = {
    "title": "A spare recording",
    "summary": "The stripped-back production highlights the voice.",
    "key_points": ["The guitar was removed."],
}
TRACK = QueueTrack("1", "a", "Artist", "Song", 240)


@pytest.mark.parametrize(
    "metadata",
    [
        "bad json",
        "{}",
        '{"title": "x"}',
        json.dumps({**SUMMARY, "key_points": ["x"] * 4}),
    ],
)
def test_invalid_companion_never_enters_spoken_script(metadata):
    script, card = companion.split_companion_output(
        f"A spoken story. <COMPANION>{metadata}</COMPANION>"
    )
    assert script == "A spoken story."
    assert card is None


def test_truncated_companion_keeps_speech():
    assert companion.split_companion_output('Story. <COMPANION>{"title":') == (
        "Story.",
        None,
    )
    assert companion.split_companion_output("Legacy story.") == ("Legacy story.", None)


def test_same_generation_call_produces_summary_and_script(monkeypatch):
    writer = Mock(
        return_value=f"The guitar was removed. <COMPANION>{json.dumps(SUMMARY)}</COMPANION>"
    )
    monkeypatch.setattr(generation, "gemini_generate_text", writer)
    monkeypatch.setattr(generation, "select_callback", lambda *a, **kw: None)
    monkeypatch.setattr(
        generation, "edit_conversational_draft", lambda draft, **kw: draft
    )
    script, metadata, archetype = generation.generate_archetype_script(
        archetype=Archetype.ALBUM_SPOTLIGHT,
        station_name="NeuralForge",
        personality=generation.resolve_station_personality("neuralforge"),
        current_track=TRACK,
        next_track=TRACK,
        upcoming_tracks=[],
        current_meta=TrackMetadata(album="Album"),
        next_meta=TrackMetadata(album="Album"),
        angle=None,
        hook="",
        banned_list=[],
        schedule_context=None,
        state=default_state(1800000000, random.Random(1)),
        rng=random.Random(2),
        forced_mode=False,
        forced_track_focus=TrackFocus.NEXT,
    )
    assert script == "The guitar was removed."
    assert metadata.companion == SUMMARY
    assert metadata.track_focus == TrackFocus.NEXT
    assert archetype == Archetype.ALBUM_SPOTLIGHT
    writer.assert_called_once()
    assert "LISTENER COMPANION OUTPUT" in writer.call_args.kwargs["prompt"]


def test_card_uses_focused_recording_and_ignores_minimal():
    metadata = GeneratedSegmentMetadata(companion=SUMMARY, track_focus=TrackFocus.NEXT)
    next_track = QueueTrack("2", "b", "Next Artist", "Next Song", 200)
    args = (
        metadata,
        "Title",
        TRACK,
        next_track,
        TrackMetadata(album="Old"),
        TrackMetadata(album="Next"),
    )
    assert (
        companion.build_companion(Archetype.ALBUM_SPOTLIGHT, *args)["subject"]["album"]
        == "Next"
    )
    assert companion.build_companion(Archetype.BACK_SELL, *args) is None


def test_structured_news_and_concert_content_preserves_facts_filters_unsafe_links():
    news = GeneratedSegmentMetadata(
        news_segment=NewsSegment(
            "script", 1, [NewsStoryMeta("topic", "Headline", "javascript:bad")]
        )
    )
    card = companion.build_companion(
        Archetype.NEWS, news, "News", TRACK, TRACK, TrackMetadata(), TrackMetadata()
    )
    assert card["items"] == [{"headline": "Headline", "source_url": None}]
    concert = GeneratedSegmentMetadata(
        concert_segment=ConcertSegment(
            "script",
            [
                ConcertEventMeta(
                    "Artist",
                    "CH",
                    "Bern",
                    "Venue",
                    "2026-11-01",
                    "https://example.com/event",
                )
            ],
        )
    )
    card = companion.build_companion(
        Archetype.CONCERT_CHECK,
        concert,
        "Concerts",
        TRACK,
        TRACK,
        TrackMetadata(),
        TrackMetadata(),
    )
    assert card["items"][0]["event_date"] == "2026-11-01"
    assert card["items"][0]["source_url"] == "https://example.com/event"


def publish(
    path,
    start,
    media_id="first",
    channel="neuralforge-es",
    archetype=Archetype.SHORT_STORY,
):
    companion.publish_companion(
        channel,
        archetype=archetype,
        content={**SUMMARY, "items": []},
        transcript="A <chuckle> story.",
        media_id=media_id,
        expected_start=start,
        duration=60,
        now=start - 120,
        path=path,
    )


def test_airtime_expiry_future_replacement_and_channel_isolation(tmp_path):
    path = tmp_path / "companion.json"
    publish(path, 1000)
    assert companion.current_companion("neuralforge-es", 999, path=path) is None
    assert (
        companion.current_companion("neuralforge-es", 1000, path=path)["transcript"]
        == "A story."
    )
    assert companion.current_companion("neuralforge-fr", 1000, path=path) is None
    publish(path, 1500, "second")
    assert (
        companion.current_companion("neuralforge-es", 1499, path=path)["id"] == "first"
    )
    assert (
        companion.current_companion("neuralforge-es", 1500, path=path)["id"] == "second"
    )
    assert (
        companion.current_companion("neuralforge-es", 2759, path=path)["id"] == "second"
    )
    assert companion.current_companion("neuralforge-es", 2760, path=path) is None


def test_ordinary_segments_do_not_replace_card(tmp_path):
    path = tmp_path / "companion.json"
    publish(path, 1000)
    before = path.read_bytes()
    publish(path, 1100, "back-sell", archetype=Archetype.BACK_SELL)
    assert path.read_bytes() == before


def test_absent_and_corrupt_storage_are_empty(tmp_path):
    path = tmp_path / "companion.json"
    assert companion.current_companion("neuralforge-es", 1000, path=path) is None
    path.write_text("not json")
    assert companion.current_companion("neuralforge-es", 1000, path=path) is None
    publish(path, 1000)
    assert json.loads(path.read_text())["version"] == 1


def test_failed_atomic_replace_preserves_previous_card(tmp_path, monkeypatch):
    path = tmp_path / "companion.json"
    publish(path, 1000)
    original = path.read_bytes()
    monkeypatch.setattr(
        companion.os, "replace", Mock(side_effect=OSError("disk unavailable"))
    )
    with pytest.raises(OSError):
        publish(path, 1500, "second")
    assert path.read_bytes() == original
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize("field", ["expected_start", "expected_end", "expires_at"])
def test_corrupt_timing_record_is_ignored(tmp_path, field):
    path = tmp_path / "companion.json"
    publish(path, 1000)
    payload = json.loads(path.read_text())
    payload["records"][0][field] = "invalid"
    path.write_text(json.dumps(payload))
    assert companion.current_companion("neuralforge-es", 1000, path=path) is None
