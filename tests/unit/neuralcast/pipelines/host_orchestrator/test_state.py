"""Unit tests for host orchestrator state persistence helpers."""

from __future__ import annotations

import random
import time

from neuralcast.pipelines.host_orchestrator import state


def test_default_state_sets_future_speak_deadline() -> None:
    now = time.time()

    result = state.default_state(now, random.Random(1))

    assert result.songs_since_last_spoken == 0
    assert result.next_speak_deadline_ts > now


def test_build_news_dedup_key_uses_url_when_present() -> None:
    assert (
        state.build_news_dedup_key("AI", "Headline", "https://www.example.com/x")
        == "ai|headline|example.com"
    )
