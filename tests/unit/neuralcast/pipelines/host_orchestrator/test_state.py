"""Unit tests for host orchestrator state persistence helpers."""

from __future__ import annotations

import random
import time

from neuralcast.pipelines.host_orchestrator.config import cadence_settings_for_station
from neuralcast.pipelines.host_orchestrator.models import Archetype
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

    assert 5 <= result.songs_until_next_speak <= 10
    assert result.next_speak_deadline_ts == now + 90 * 60


def test_neuralcast_migration_clamps_existing_short_wait_roll() -> None:
    now = time.time()
    settings = cadence_settings_for_station("neuralcast")

    result = state.migrate_state(
        {"songs_until_next_speak": 2},
        now,
        random.Random(1),
        settings,
    )

    assert result.songs_until_next_speak == 5


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

    assert 5 <= result.songs_until_next_speak <= 10
    assert result.next_speak_deadline_ts == now + 90 * 60
    assert result.cooldown_until[Archetype.SHORT_STORY.value] == now + 2 * 60 * 60


def test_build_news_dedup_key_uses_url_when_present() -> None:
    assert (
        state.build_news_dedup_key("AI", "Headline", "https://www.example.com/x")
        == "ai|headline|example.com"
    )
