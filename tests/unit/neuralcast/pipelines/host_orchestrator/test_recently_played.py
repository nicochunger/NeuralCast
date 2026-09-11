"""Eligibility and source boundaries for retrospective host segments."""

import argparse
import random

import pytest

from neuralcast.pipelines.host_orchestrator.archetype_policies import (
    load_archetype_policy_registry,
)
from neuralcast.pipelines.host_orchestrator.channels import get_channel_registry
from neuralcast.pipelines.host_orchestrator.main import (
    PlaybackContext,
    _select_archetype,
)
from neuralcast.pipelines.host_orchestrator.models import (
    Archetype,
    QueueTrack,
    TrackMetadata,
)
from neuralcast.pipelines.host_orchestrator.prompts import (
    build_prompt,
    resolve_station_personality,
)
from neuralcast.pipelines.host_orchestrator.state import (
    default_state,
    legal_archetypes_for_remaining,
    meets_archetype_conditions,
)
from neuralcast.pipelines.host_orchestrator.transport import extract_recent_music


def entry(number, artist=None):
    return {
        "played_at": number * 100,
        "song": {
            "id": str(number),
            "artist": artist or f"Artist {number}",
            "title": f"Title {number}",
        },
    }


def history():
    return {"now_playing": entry(5), "song_history": [entry(n) for n in (4, 3, 2, 1)]}


def test_history_is_chronological_and_excludes_previous_host_boundary():
    tracks = extract_recent_music(history(), 150)
    assert [t.title for t in tracks] == ["Title 2", "Title 3", "Title 4", "Title 5"]
    assert extract_recent_music(history(), None) == []
    assert extract_recent_music({"now_playing": entry(5)}, 150) == []


@pytest.mark.parametrize(
    "bad_entry", [entry(3, "NueralHost"), entry(3, "AI Host"), {"song": {}}, entry(6)]
)
def test_history_stops_at_snippets_or_unreliable_entries(bad_entry):
    payload = history()
    payload["song_history"][1] = bad_entry
    assert len(extract_recent_music(payload, 150)) == 2


@pytest.mark.parametrize(
    "gap,history_size,eligible",
    [(3, 4, False), (4, 3, False), (4, 4, True), (5, 4, True)],
)
def test_recap_requires_both_cadence_gap_and_verified_history(
    gap, history_size, eligible
):
    state = default_state(1000, random.Random(1))
    state.songs_since_last_spoken = gap
    tracks = extract_recent_music(history(), 150)[-history_size:]
    assert (
        Archetype.RECENTLY_PLAYED
        in legal_archetypes_for_remaining(state, 1000, 300, recent_tracks=tracks)
    ) == eligible


def test_channel_override_inherits_and_can_raise_gap():
    registry = load_archetype_policy_registry()
    profile = registry.resolve(
        "neuralforge",
        {"recently_played": {"min_songs_since_host": 6}},
        resolved_name="test",
    )
    assert (
        registry.profiles["base"]
        .for_archetype(Archetype.RECENTLY_PLAYED)
        .min_songs_since_host
        == 4
    )
    assert profile.for_archetype(Archetype.RECENTLY_PLAYED).min_songs_since_host == 6
    state = default_state(1000, random.Random(1))
    state.songs_since_last_spoken = 5
    assert not meets_archetype_conditions(
        Archetype.RECENTLY_PLAYED, state, extract_recent_music(history(), 150), profile
    )
    with pytest.raises(ValueError):
        registry.resolve(
            "neuralforge",
            {"recently_played": {"min_songs_since_host": -1}},
            resolved_name="bad",
        )


def test_force_bypasses_recap_eligibility_gate():
    track = QueueTrack("1", None, "Artist", "Title", 300)
    state = default_state(1000, random.Random(1))
    state.songs_since_last_spoken = 0
    assert (
        _select_archetype(
            argparse.Namespace(),
            state,
            PlaybackContext(track, 300, "key", 1),
            None,
            Archetype.RECENTLY_PLAYED,
            False,
            None,
            random.Random(1),
        )
        == Archetype.RECENTLY_PLAYED
    )


@pytest.mark.parametrize("locale", ["es-AR", "en", "fr-CH"])
@pytest.mark.parametrize("count", [1, 2, 3, 4])
def test_prompt_only_receives_last_three_verified_songs(locale, count):
    tracks = extract_recent_music(history(), 150)[-count:]
    prompt = build_prompt(
        Archetype.RECENTLY_PLAYED,
        "NeuralForge",
        resolve_station_personality("neuralforge"),
        tracks[-1],
        QueueTrack("future", None, "Future artist", "Future title", 300),
        [],
        TrackMetadata(),
        TrackMetadata(),
        None,
        "",
        [],
        [],
        None,
        locale=get_channel_registry().locales[locale],
        recent_tracks=tracks,
    )
    assert "Artist 2" not in prompt
    assert "Future artist" not in prompt
    assert all(track.artist in prompt for track in tracks[-3:])
    assert get_channel_registry().locales[locale].script_guidance in prompt


@pytest.mark.parametrize(
    "previous,eligible",
    [("up_next_tease", False), ("back_sell", True), ("recently_played", True)],
)
def test_previous_forward_tease_blocks_only_automatic_recap(previous, eligible):
    state = default_state(1000, random.Random(1))
    state.songs_since_last_spoken = 8
    state.recent_archetypes = ["news", previous]
    tracks = extract_recent_music(history(), 150)
    assert (
        meets_archetype_conditions(Archetype.RECENTLY_PLAYED, state, tracks) == eligible
    )
    result = _select_archetype(
        argparse.Namespace(),
        state,
        PlaybackContext(tracks[-1], 300, "key", 1, tracks),
        None,
        Archetype.RECENTLY_PLAYED,
        False,
        None,
        random.Random(1),
    )
    assert result == Archetype.RECENTLY_PLAYED


def test_previous_tease_condition_is_inherited_and_validated():
    registry = load_archetype_policy_registry()
    assert (
        not registry.profiles["neuralforge"]
        .for_archetype(Archetype.RECENTLY_PLAYED)
        .allow_after_up_next_tease
    )
    profile = registry.resolve(
        "neuralforge",
        {"recently_played": {"allow_after_up_next_tease": True}},
        resolved_name="test",
    )
    assert profile.for_archetype(Archetype.RECENTLY_PLAYED).allow_after_up_next_tease
    with pytest.raises(ValueError):
        registry.resolve(
            "neuralforge",
            {"recently_played": {"allow_after_up_next_tease": "false"}},
            resolved_name="bad",
        )


def test_forced_history_crosses_host_boundary_and_allows_unknown_state():
    payload = history()
    payload["song_history"][1] = entry(3, "NueralHost")
    tracks = extract_recent_music(payload, None, ignore_host_boundary=True)
    assert [t.title for t in tracks] == ["Title 1", "Title 2", "Title 4", "Title 5"]
    assert len(extract_recent_music(payload, 450, ignore_host_boundary=True)) == 4


def test_forced_history_can_use_current_song_without_history():
    tracks = extract_recent_music({"now_playing": entry(5)}, None, ignore_host_boundary=True)
    assert [t.title for t in tracks] == ["Title 5"]
