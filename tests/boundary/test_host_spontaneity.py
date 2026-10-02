"""Verify conversational guidance reaches writing without an extra model call."""

import random
from unittest.mock import Mock

import pytest

from neuralcast.pipelines.host_orchestrator import generation
from neuralcast.pipelines.host_orchestrator.channels import get_channel_registry
from neuralcast.pipelines.host_orchestrator.models import (
    Archetype,
    QueueTrack,
    TrackMetadata,
)
from neuralcast.pipelines.host_orchestrator.state import default_state

NOW = 1_800_000_000.0


@pytest.mark.parametrize(
    "archetype",
    [
        Archetype.SHORT_STORY,
        Archetype.RECENTLY_PLAYED,
        Archetype.BLOCK_INTRO,
        Archetype.ULTRA_MINIMAL,
    ],
)
def test_existing_writer_receives_broadcast_history_and_guidance_once(
    monkeypatch, archetype, caplog
):
    state = default_state(NOW, random.Random(1))
    state.broadcast_memory = [
        {
            "id": "earlier",
            "script": "Earlier unusual joke.",
            "expected_play_at": NOW - 600,
        },
        {
            "id": "future",
            "script": "Unplayed future joke.",
            "expected_play_at": NOW + 600,
        },
    ]
    writer = Mock(return_value="A natural transition to the next song.")
    monkeypatch.setattr(generation, "gemini_generate_text", writer)
    monkeypatch.setattr(generation, "select_callback", lambda *args, **kwargs: None)
    monkeypatch.setattr(generation, "now_ts", lambda: NOW)
    track = QueueTrack("song", "1", "Artist", "Title", 240)
    with caplog.at_level("INFO", logger="host_orchestrator"):
        script, metadata, used = generation.generate_archetype_script(
            archetype=archetype,
            station_name="NeuralCast",
            personality=generation.resolve_station_personality("neuralcast"),
            current_track=track,
            next_track=track,
            upcoming_tracks=[track],
            current_meta=TrackMetadata(),
            next_meta=TrackMetadata(),
            angle=None,
            hook="",
            banned_list=[],
            schedule_context=None,
            state=state,
            rng=random.Random(2),
            forced_mode=False,
            recent_tracks=[track],
            locale=get_channel_registry().locales["en"],
        )
    writer.assert_called_once()
    prompt = writer.call_args.kwargs["prompt"]
    assert "CONVERSATIONAL FREEDOM" in prompt
    assert "Earlier unusual joke." in prompt
    assert "Unplayed future joke." not in prompt
    assert "[spontaneity]" in caplog.text
    assert script == writer.return_value
    assert used == archetype
    assert metadata.callback_source_id is None
    assert len(state.broadcast_memory) == 2
