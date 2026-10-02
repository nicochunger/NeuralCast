"""Conversational freedom, bounded broadcast context, and all prompt contracts."""

import random
from unittest.mock import Mock

import pytest

from neuralcast.pipelines.host_orchestrator import prompts, spontaneity
from neuralcast.pipelines.host_orchestrator.channels import get_channel_registry
from neuralcast.pipelines.host_orchestrator.models import (
    Archetype,
    QueueTrack,
    TrackMetadata,
)
from neuralcast.pipelines.host_orchestrator.state import default_state

NOW = 1_800_000_000.0


@pytest.mark.parametrize(
    "draw, mode, words",
    [
        (0, "direct", 0),
        (0.299, "direct", 0),
        (0.30, "tiny", 12),
        (0.849, "tiny", 12),
        (0.85, "playful", 25),
        (0.999, "playful", 25),
    ],
)
def test_random_draw_varies_permission_without_selecting_a_remark(draw, mode, words):
    allowance = spontaneity.sample_allowance(
        Archetype.SHORT_STORY, Mock(random=lambda: draw)
    )
    assert (allowance.mode, allowance.max_aside_words) == (mode, words)
    if words:
        assert "omitting the aside is always allowed" in allowance.prompt()
        assert "within the existing segment length" in allowance.prompt()


def test_short_formats_preserve_their_purpose():
    rng = Mock(random=lambda: 0.99)
    intro = spontaneity.sample_allowance(Archetype.BLOCK_INTRO, rng)
    assert intro.max_aside_words == 8
    ultra = spontaneity.sample_allowance(Archetype.ULTRA_MINIMAL, rng)
    assert ultra.max_aside_words == 0
    assert "do not add a standalone conversational aside" in ultra.prompt()


def test_recent_history_is_bounded_dated_deduplicated_and_read_only():
    state = default_state(NOW, random.Random(1))
    state.recent_scripts = ["Undated legacy script"]
    state.broadcast_memory = [
        {
            "id": str(i),
            "script": f"Earlier script {i}",
            "expected_play_at": NOW - i * 60,
        }
        for i in range(10)
    ] + [
        {"id": "old", "script": "Too old", "expected_play_at": NOW - 7201},
        {"id": "future", "script": "Not played yet", "expected_play_at": NOW + 1},
        {"id": "duplicate", "script": "Earlier script 0", "expected_play_at": NOW},
    ]
    original = list(state.broadcast_memory)
    assert spontaneity.recent_scripts_for_prompt(state, NOW) == [
        f"Earlier script {i}" for i in range(6)
    ]
    assert state.broadcast_memory == original


def test_long_history_keeps_opening_and_closing_with_a_size_limit():
    state = default_state(NOW, random.Random(1))
    state.recent_scripts = ["Opening " + "body " * 1000 + " Closing"]
    script = spontaneity.recent_scripts_for_prompt(state, NOW)[0]
    assert len(script) <= spontaneity.MAX_SCRIPT_CHARACTERS
    assert script.startswith("Opening")
    assert script.endswith("Closing")


def test_legacy_fallback_does_not_reintroduce_old_or_future_broadcasts():
    state = default_state(NOW, random.Random(1))
    state.recent_scripts = ["Legacy repetition context"]
    assert spontaneity.recent_scripts_for_prompt(state, NOW) == state.recent_scripts
    state.broadcast_memory = [
        {"id": "old", "script": "Dated old script", "expected_play_at": NOW - 7201}
    ]
    assert spontaneity.recent_scripts_for_prompt(state, NOW) == []


@pytest.mark.parametrize("locale_tag", ["en", "fr-CH", "es-AR"])
@pytest.mark.parametrize("archetype", list(Archetype))
def test_guidance_reaches_all_archetype_prompts_and_preserves_language(
    locale_tag, archetype
):
    locale = get_channel_registry().locales[locale_tag]
    track = QueueTrack("song", "1", "Artist", "Title", 240)
    guidance = spontaneity.ConversationalAllowance("tiny", 12).prompt()
    prompt = prompts.build_prompt(
        archetype=archetype,
        station_name="NeuralCast",
        personality=prompts.resolve_station_personality("neuralcast"),
        current=track,
        next_track=track,
        upcoming_tracks=[track],
        current_meta=TrackMetadata(),
        next_meta=TrackMetadata(),
        angle=None,
        hook="",
        banned_list=[],
        recent_scripts=["Earlier unusual joke."],
        schedule_context=None,
        recent_tracks=[track],
        locale=locale,
        spontaneity_guidance=guidance,
    )
    assert guidance in prompt
    assert "Earlier unusual joke." in prompt
    assert prompt.endswith(locale.script_guidance + "\n")
    assert "repeated conversational habits" in prompt
    if archetype in {Archetype.NEWS, Archetype.CONCERT_CHECK}:
        assert "META" in prompt
