"""Verify drafting, history, callback selection, and optional editing order."""

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


DRAFT = (
    "That was Artist playing Title, with a bass line that leaves plenty of space "
    "for the voice to move around. The arrangement keeps its steady pace while "
    "the instruments gradually drop away. Next comes Artist with Title."
)
EDITED = DRAFT.replace("around.", "around. Someone clearly trusted the volume knob.")


def generate_music(state):
    track = QueueTrack("song", "1", "Artist", "Title", 240)
    return generation.generate_archetype_script(
        archetype=Archetype.BACK_SELL,
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
        locale=get_channel_registry().locales["en"],
    )


@pytest.fixture
def music_pipeline(monkeypatch):
    from neuralcast.pipelines.host_orchestrator.spontaneity import (
        ConversationalAllowance,
    )

    state = default_state(NOW, random.Random(1))
    state.broadcast_memory = [
        {"id": "earlier", "script": "Earlier joke.", "expected_play_at": NOW - 600}
    ]
    monkeypatch.setattr(generation, "now_ts", lambda: NOW)
    monkeypatch.setattr(
        generation,
        "prepare_conversational_allowance",
        lambda *args: ConversationalAllowance("tiny", 12),
    )
    writer = Mock(side_effect=[DRAFT, EDITED])
    monkeypatch.setattr(generation, "gemini_generate_text", writer)
    decision = Mock(return_value=None)
    monkeypatch.setattr(generation, "select_callback", decision)
    return state, writer, decision


def test_editor_receives_completed_draft_after_callback_decision(music_pipeline):
    state, writer, decision = music_pipeline
    events = []
    writer.side_effect = lambda **kwargs: events.append(
        "edit" if events else "draft"
    ) or (DRAFT if len(events) == 1 else EDITED)
    decision.side_effect = lambda *args, **kwargs: events.append("decision") or None
    script, metadata, used = generate_music(state)
    assert events == ["draft", "decision", "edit"]
    assert script == EDITED
    assert used == Archetype.BACK_SELL
    assert metadata.callback_source_id is None
    assert decision.call_args.args[1]["draft"] == DRAFT
    draft_call, editor_call = writer.call_args_list
    assert "standalone" in draft_call.kwargs["prompt"]
    assert DRAFT in editor_call.kwargs["prompt"]
    assert "Earlier joke." in editor_call.kwargs["prompt"]
    assert editor_call.kwargs["with_search"] is False
    assert len(state.broadcast_memory) == 1


@pytest.mark.parametrize("output", ["NO_CALLBACK", DRAFT, EDITED, "Invalid rewrite."])
def test_selected_callback_owns_only_optional_edit_even_if_omitted(
    music_pipeline, output
):
    state, writer, decision = music_pipeline
    decision.return_value = state.broadcast_memory[0]
    writer.side_effect = [DRAFT, output]
    script, metadata, _ = generate_music(state)
    assert writer.call_count == 2
    assert "BROADCAST MEMORY" in writer.call_args.kwargs["prompt"]
    assert script == (EDITED if output == EDITED else DRAFT)
    assert metadata.callback_source_id == ("earlier" if output == EDITED else None)


def test_failed_editor_keeps_draft_without_regeneration(music_pipeline):
    state, writer, _ = music_pipeline
    writer.side_effect = [DRAFT, RuntimeError("unavailable")]
    assert generate_music(state)[0] == DRAFT
    assert writer.call_count == 2
