"""Unit tests for host orchestrator state persistence helpers."""

from __future__ import annotations

import random
import time

from neuralcast.pipelines.host_orchestrator.config import cadence_settings_for_station
from neuralcast.pipelines.host_orchestrator.models import (
    Archetype,
    NewsSegment,
    NewsStoryMeta,
    ScheduleContext,
)
from neuralcast.pipelines.host_orchestrator import state


def test_default_state_sets_future_speak_deadline() -> None:
    now = time.time()

    result = state.default_state(now, random.Random(1))

    assert result.songs_since_last_spoken == 0
    assert result.next_speak_deadline_ts > now


def test_neuralcast_default_state_uses_longer_wait_range_and_deadline() -> None:
    now = time.time()
    settings = cadence_settings_for_station("neuralcast")

    result = state.default_state(now, random.Random(1), settings)

    assert 7 <= result.songs_until_next_speak <= 12
    assert result.next_speak_deadline_ts == now + 120 * 60


def test_neuralcast_migration_clamps_existing_short_wait_roll() -> None:
    now = time.time()
    settings = cadence_settings_for_station("neuralcast")

    result = state.migrate_state(
        {"songs_until_next_speak": 2},
        now,
        random.Random(1),
        settings,
    )

    assert result.songs_until_next_speak == 7


def test_neuralcast_success_update_rolls_longer_wait_and_scaled_cooldown() -> None:
    now = time.time()
    settings = cadence_settings_for_station("neuralcast")
    result = state.default_state(now, random.Random(1), settings)

    state.apply_success_state_update(
        state=result,
        ts=now,
        current_track_key="artist|title",
        current_remaining=120,
        archetype_used=Archetype.SHORT_STORY,
        hook="hook",
        angle=None,
        news_segment=None,
        script_text="script",
        schedule_context=None,
        rng=random.Random(2),
        cadence_settings=settings,
    )

    assert 7 <= result.songs_until_next_speak <= 12
    assert result.next_speak_deadline_ts == now + 120 * 60
    assert result.cooldown_until[Archetype.SHORT_STORY.value] == now + 2 * 60 * 60


def test_build_news_dedup_key_uses_url_when_present() -> None:
    assert (
        state.build_news_dedup_key("AI", "Headline", "https://www.example.com/x")
        == "ai|headline|example.com"
    )


def test_migrate_state_normalizes_legacy_and_invalid_persisted_values() -> None:
    now = 1_700_000_000.0

    result = state.migrate_state(
        {
            "state_version": 1,
            "last_seen_track_key": "artist|title",
            "last_seen_ts": "12.5",
            "songs_since_last_spoken": "-4",
            "songs_until_next_speak": 999,
            "cooldown_until": {"deep_dive": "45", "not-real": 7},
            "recent_archetypes": ["deep_dive", "invalid"],
            "recent_hooks": ["first", "second"],
            "last_angle_by_archetype": {"back_sell": "Minimalist", "bad": "x"},
            "recent_news_dedup": [{"key": "news", "ts": "20", "headline": "Head"}, {}],
            "recent_scripts": ["  first   script ", "second"],
            "schedule_block_mentions": {
                "block": {"start": True, "speak_count": "2", "updated_at": "30"},
                "empty": {},
            },
        },
        now,
        random.Random(1),
    )

    assert result.last_seen_ts == 12.5
    assert result.songs_since_last_spoken == 0
    assert result.songs_until_next_speak == 5
    assert result.cooldown_until[Archetype.SHORT_STORY.value] == 45.0
    assert result.recent_archetypes == [Archetype.SHORT_STORY.value]
    assert result.recent_hooks == ["first"]
    assert result.last_angle_by_archetype == {"back_sell": "Minimalist"}
    assert result.recent_news_dedup == [
        {"key": "news", "ts": 20.0, "topic": "", "headline": "Head", "source_domain": ""}
    ]
    assert result.recent_scripts == ["first script", "second"]
    assert result.schedule_block_mentions["block"]["speak_count"] == 2


def test_lock_recovers_stale_file_and_state_load_recovers_invalid_json(tmp_path) -> None:
    lock_path = tmp_path / "state.lock"
    lock_path.write_text('{"created_at": 0}', encoding="utf-8")
    lock = state.StationLock(lock_path, stale_seconds=1)

    assert lock.acquire() is True
    assert lock_path.exists()
    lock.release()
    assert not lock_path.exists()

    state_path = tmp_path / "state.json"
    state_path.write_text("not json", encoding="utf-8")
    loaded = state.load_state(state_path, 1_700_000_000, random.Random(1))

    assert loaded.state_version > 0
    assert not state_path.exists()
    assert list(tmp_path.glob("ai_host_orchestrator_state.corrupt.*.json"))


def test_cadence_selection_and_track_updates_cover_edge_cases() -> None:
    result = state.default_state(100, random.Random(1))
    result.songs_since_last_spoken = result.songs_until_next_speak
    assert state.should_speak_now(result, "new", 101) == (True, "song cadence reached")

    result.last_spoken_track_key = "same"
    result.last_spoken_expected_end_ts = 200
    assert state.should_speak_now(result, "same", 150) == (
        False,
        "current track already consumed by previous successful segment",
    )

    state.update_track_seen_state(result, "AI Host - Drop", 160)
    assert result.last_seen_track_key is None
    state.update_track_seen_state(result, "artist|one", 161)
    state.update_track_seen_state(result, "artist|two", 162)
    assert result.songs_since_last_spoken == result.songs_until_next_speak + 1


def test_success_update_records_news_script_and_schedule_mentions() -> None:
    result = state.default_state(100, random.Random(1))
    context = ScheduleContext(
        block_key="block", section_label="Metal", genre_labels=[], mode="normal",
        playlist_name=None, progress_ratio=0.5, phase="mid", mention_intent="mid",
        next_section_label=None, start_local_iso="", end_local_iso="",
    )
    news = NewsSegment(
        script="news", story_count=1,
        stories=[NewsStoryMeta("Science", "Discovery", "https://www.example.test/news")],
    )

    state.apply_success_state_update(
        result, 100, "artist|title", 30, Archetype.SHORT_STORY, "hook", "artist_origin",
        news, "  a script ", context, random.Random(2),
    )

    assert result.recent_scripts == ["a script"]
    assert result.recent_news_dedup[0]["source_domain"] == "example.test"
    assert result.schedule_block_mentions["block"] == {
        "speak_count": 1, "mid": True, "mid_mention_count": 1,
        "last_mid_speak_count": 1, "updated_at": 100,
    }
