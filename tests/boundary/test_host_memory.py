"""Host memory across generation, transport, and persistent state boundaries."""

import random
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from neuralcast.pipelines.host_orchestrator import generation, main, memory
from neuralcast.pipelines.host_orchestrator.channels import get_channel_registry
from neuralcast.pipelines.host_orchestrator.models import (
    Archetype,
    QueueTrack,
    StoryAssets,
    TrackFocus,
    TrackMetadata,
)
from neuralcast.pipelines.host_orchestrator.state import default_state

NOW = 1_800_000_000.0


@pytest.mark.parametrize(
    "response, expected_callback",
    [("A natural continuation.", True), ("NO_SCRIPT", False)],
)
def test_callback_uses_resolved_subject_and_does_not_survive_fallback(
    monkeypatch, response, expected_callback
):
    state = default_state(NOW, random.Random(1))
    state.broadcast_memory = [
        {
            "id": "earlier",
            "script": "We discussed this album.",
            "expected_play_at": NOW - 600,
        }
    ]
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    provider = Mock(
        return_value={
            "answers": {
                "callback_0": {
                    "type": "choice",
                    "choice": "yes",
                    "probabilities": {"yes": 0.9, "no": 0.1},
                }
            }
        }
    )
    monkeypatch.setattr(memory.typesafe, "evaluate_questions", provider)
    monkeypatch.setattr(generation, "now_ts", lambda: NOW)
    writer = Mock(side_effect=[response, "A simple transition."])
    monkeypatch.setattr(generation, "gemini_generate_text", writer)
    _, metadata, archetype = generation.generate_archetype_script(
        archetype=Archetype.ALBUM_SPOTLIGHT,
        station_name="NeuralForge",
        personality=generation.resolve_station_personality("neuralforge"),
        current_track=QueueTrack("a", "1", "Artist A", "Song A", 240),
        next_track=QueueTrack("b", "2", "Artist B", "Song B", 240),
        upcoming_tracks=[],
        current_meta=TrackMetadata(album="Album A"),
        next_meta=TrackMetadata(album="Album B"),
        angle=None,
        hook="",
        banned_list=[],
        schedule_context=None,
        state=state,
        rng=random.Random(2),
        forced_mode=False,
        forced_track_focus=TrackFocus.NEXT,
        locale=get_channel_registry().locales["en"],
    )
    context = provider.call_args.args[0]["upcoming"]
    assert context["subject"] == {"artist": "Artist B", "title": "Song B"}
    assert context["focus"] == "next"
    assert "We discussed this album." in writer.call_args_list[0].kwargs["prompt"]
    assert metadata.callback_source_id == ("earlier" if expected_callback else None)
    assert archetype == (
        Archetype.ALBUM_SPOTLIGHT if expected_callback else Archetype.ULTRA_MINIMAL
    )
    if not expected_callback:
        assert "BROADCAST MEMORY" not in writer.call_args_list[1].kwargs["prompt"]
    assert len(state.broadcast_memory) == 1


@pytest.mark.parametrize("failure", [None, "upload", "queue"])
def test_only_successful_queue_insertion_commits_memory(monkeypatch, tmp_path, failure):
    state = default_state(NOW, random.Random(1))
    upload = Mock(return_value={})
    queue = Mock(return_value="request")
    if failure == "upload":
        upload.side_effect = RuntimeError("upload failed")
    if failure == "queue":
        queue.side_effect = RuntimeError("queue failed")
    client = SimpleNamespace(upload_media=upload, send_telnet_command=queue)
    channel = SimpleNamespace(
        key="test-channel",
        liquidsoap_media_root="/media",
        cadence_profile="neuralforge",
        archetype_policy=None,
        remote_prefix="stories",
    )
    runtime = SimpleNamespace(client=client, channel=channel, station_id=1)
    current = QueueTrack("a", "1", "Artist", "Song", 240)
    playback = main.PlaybackContext(current, 120, "artist|song", 5)
    queue_context = main.QueueContext([], current, None, NOW)
    generation_context = SimpleNamespace(hook="", angle=None)
    assets = StoryAssets(
        tmp_path / "script.txt",
        tmp_path / "audio.mp3",
        "Plain script",
        "stories/audio.mp3",
        "earlier",
    )
    monkeypatch.setattr(main, "now_ts", lambda: NOW)
    monkeypatch.setattr(main, "run_with_retries", lambda label, func: func())
    for name, value in {
        "extract_upload_storage_path": "stories/audio.mp3",
        "extract_upload_duration": 20,
        "extract_upload_media_id": 42,
        "extract_upload_song_id": "song-id",
        "extract_telnet_request_id": "request-id",
    }.items():
        monkeypatch.setattr(main, name, lambda _, value=value: value)
    monkeypatch.setattr(main, "build_request_command", lambda **_: "queue")
    monkeypatch.setattr(main, "log_segment_event", lambda **_: None)
    monkeypatch.setattr(main, "cleanup_local_stories", lambda *_: None)
    monkeypatch.setattr(main, "cleanup_remote_stories", lambda *_, **__: None)

    def publish():
        return main._publish_segment(
            SimpleNamespace(
                station="neuralforge", keep_local_days=7, keep_remote_days=7
            ),
            runtime,
            playback,
            queue_context,
            generation_context,
            Archetype.BACK_SELL,
            "Title",
            None,
            assets.story_text,
            assets,
            state,
            random.Random(1),
        )

    if failure:
        with pytest.raises(RuntimeError):
            publish()
        assert state.broadcast_memory == []
    else:
        publish()
        assert len(state.broadcast_memory) == 1
        assert state.broadcast_memory[0]["media_id"] == "42"
        assert state.broadcast_memory[0]["callback_source_id"] == "earlier"
        assert state.broadcast_memory[0]["expected_play_at"] == NOW + 120
