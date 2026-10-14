"""Companion persistence follows queue success, never preparation or failure."""

import random
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from neuralcast.pipelines.host_orchestrator import companion, main
from neuralcast.pipelines.host_orchestrator.channels import get_channel_registry
from neuralcast.pipelines.host_orchestrator.models import (
    Archetype,
    QueueTrack,
    StoryAssets,
)
from neuralcast.pipelines.host_orchestrator.state import default_state


@pytest.mark.parametrize("failure", [None, "upload", "queue", "companion"])
def test_publication_boundary(tmp_path, monkeypatch, failure):
    now = 1800000000.0
    state = default_state(now, random.Random(1))
    track = QueueTrack("a", "1", "Artist", "Song", 240)
    client = SimpleNamespace(
        upload_media=Mock(return_value={}), send_telnet_command=Mock(return_value="ok")
    )
    if failure == "upload":
        client.upload_media.side_effect = RuntimeError("upload")
    if failure == "queue":
        client.send_telnet_command.side_effect = RuntimeError("queue")
    runtime = SimpleNamespace(
        client=client,
        station_id=2,
        channel=get_channel_registry().channels["neuralforge-es"],
    )
    assets = StoryAssets(
        tmp_path / "script",
        tmp_path / "audio",
        "A story.",
        "stories/audio.mp3",
        companion={
            "title": "Story",
            "summary": "Summary",
            "key_points": [],
            "items": [],
        },
    )
    path = tmp_path / "host_companion.json"
    monkeypatch.setattr(companion, "companion_path", lambda channel: path)
    if failure == "companion":
        monkeypatch.setattr(
            main, "publish_companion", Mock(side_effect=OSError("disk"))
        )
    monkeypatch.setattr(main, "now_ts", lambda: now)
    monkeypatch.setattr(main, "run_with_retries", lambda label, func: func())
    for name, value in {
        "extract_upload_storage_path": "audio.mp3",
        "extract_upload_duration": 30,
        "extract_upload_media_id": 42,
        "extract_upload_song_id": "song",
        "extract_telnet_request_id": "request",
    }.items():
        monkeypatch.setattr(main, name, lambda _, value=value: value)
    monkeypatch.setattr(main, "build_request_command", lambda **kw: "queue")
    monkeypatch.setattr(main, "log_segment_event", lambda **kw: None)
    monkeypatch.setattr(main, "cleanup_local_stories", lambda *a: None)
    monkeypatch.setattr(main, "cleanup_remote_stories", lambda *a, **kw: None)

    def publish():
        return main._publish_segment(
            SimpleNamespace(
                station="neuralforge", keep_local_days=7, keep_remote_days=7
            ),
            runtime,
            main.PlaybackContext(track, 120, "artist|song", 1),
            main.QueueContext([], track, None, now),
            SimpleNamespace(hook="", angle=None),
            Archetype.SHORT_STORY,
            "Story",
            None,
            assets.story_text,
            assets,
            state,
            random.Random(1),
        )

    if failure in {"upload", "queue"}:
        with pytest.raises(RuntimeError):
            publish()
        assert not path.exists()
    else:
        result = publish()
        assert result.queued_request_id == "request"
        if failure == "companion":
            assert not path.exists()
        else:
            card = companion.current_companion("neuralforge-es", now + 120, path=path)
            assert card["expected_end"] == now + 150
            assert card["expires_at"] == now + 1350
