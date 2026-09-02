"""Unit tests for host orchestrator runtime entrypoint validation."""

from __future__ import annotations

import argparse
from types import SimpleNamespace

import pytest

from neuralcast.pipelines.host_orchestrator import main as host_main
from neuralcast.pipelines.host_orchestrator.models import Archetype, QueueTrack, TrackFocus
from neuralcast.pipelines.host_orchestrator.state import default_state
import random


def test_validate_runtime_args_accepts_empty_focus() -> None:
    assert host_main.validate_runtime_args(
        argparse.Namespace(force_archetype=None, force_track_focus=None)
    ) is None


def test_validate_runtime_args_rejects_invalid_focus_pairing() -> None:
    with pytest.raises(host_main.ArgumentValidationError):
        host_main.validate_runtime_args(
            argparse.Namespace(
                force_archetype=Archetype.BACK_SELL.value,
                force_track_focus=TrackFocus.NEXT.value,
            )
        )


def test_validate_runtime_args_rejects_schedule_only_with_forced_archetype() -> None:
    with pytest.raises(
        host_main.ArgumentValidationError,
        match="cannot be combined with --force-archetype",
    ):
        host_main.validate_runtime_args(
            argparse.Namespace(
                force_archetype=Archetype.BLOCK_INTRO.value,
                force_track_focus=None,
                scheduled_block_intros_only=True,
            )
        )


def test_schedule_only_parser_flag_defaults_false_and_can_be_enabled() -> None:
    parser = host_main.build_arg_parser()

    assert parser.parse_args([]).scheduled_block_intros_only is False
    assert (
        parser.parse_args(["--scheduled-block-intros-only"]).scheduled_block_intros_only
        is True
    )


def _now_playing(*, remaining: int = 120, listeners: int | None = 4) -> dict:
    payload = {
        "now_playing": {
            "song": {"id": "1", "artist": "Ghost", "title": "Rats", "length": 240},
            "remaining": remaining,
        }
    }
    if listeners is not None:
        payload["listeners"] = {"current": listeners}
    return payload


def test_fetch_playback_context_applies_listener_and_lead_time_gates(monkeypatch) -> None:
    args = SimpleNamespace(station="neuralforge", min_listeners=3)
    state = default_state(0, random.Random(1))
    monkeypatch.setattr(host_main, "now_ts", lambda: 100.0)

    low_listener_client = SimpleNamespace(get_now_playing=lambda _station: _now_playing(listeners=2))
    short_track_client = SimpleNamespace(get_now_playing=lambda _station: _now_playing(remaining=10))
    good_client = SimpleNamespace(get_now_playing=lambda _station: _now_playing())

    assert host_main._fetch_playback_context(args, low_listener_client, state) is None
    assert host_main._fetch_playback_context(args, short_track_client, state) is None
    result = host_main._fetch_playback_context(args, good_client, state)

    assert result is not None
    assert result.current_key == "ghost|rats"
    assert result.listener_count == 4
    assert state.last_seen_track_key == "ghost|rats"


def test_fetch_queue_context_and_forced_archetype_resolution(monkeypatch) -> None:
    args = SimpleNamespace(station="neuralforge")
    current = QueueTrack("current", "1", "Ghost", "Rats", 240)
    next_track = QueueTrack("next", "2", "Gojira", "Mea Culpa", 240)
    playback = host_main.PlaybackContext(current, 120, "ghost|rats", 4)
    client = SimpleNamespace(get_upcoming_queue=lambda _station: {"queue": []})
    monkeypatch.setattr(host_main, "parse_queue_tracks", lambda _payload: [current, next_track])
    monkeypatch.setattr(host_main, "now_ts", lambda: 100.0)
    monkeypatch.setattr(host_main, "resolve_schedule_context_for_upcoming_break", lambda **_kwargs: None)

    queue_context = host_main._fetch_queue_context(args, client, playback, None, {})

    assert queue_context is not None
    assert queue_context.next_track == next_track
    forced, automatic = host_main._resolve_effective_forced_archetype(
        SimpleNamespace(force_archetype=Archetype.NEWS.value), None
    )
    assert (forced, automatic) == (Archetype.NEWS, False)


def test_select_archetype_respects_closed_gate_and_short_forced_lead_time(monkeypatch) -> None:
    args = SimpleNamespace(archetype_profile="default")
    current = QueueTrack("current", "1", "Ghost", "Rats", 240)
    playback = host_main.PlaybackContext(current, 5, "ghost|rats", 4)
    queue_context = host_main.QueueContext([], current, None, 100)
    state = default_state(100, random.Random(1))

    assert host_main._select_archetype(
        args, state, playback, queue_context, Archetype.SHORT_STORY, False, None, random.Random(1)
    ) is None
    monkeypatch.setattr(host_main, "should_speak_now", lambda *_args: (False, "wait"))
    assert host_main._select_archetype(
        args, state, playback, queue_context, None, False, None, random.Random(1)
    ) is None
