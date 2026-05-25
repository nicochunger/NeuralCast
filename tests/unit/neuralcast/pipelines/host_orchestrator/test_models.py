"""Unit tests for host orchestrator dataclasses."""

from __future__ import annotations

from neuralcast.pipelines.host_orchestrator.models import QueueTrack


def test_queue_track_keeps_raw_payload_default_isolated() -> None:
    first = QueueTrack(queue_id="1", song_id=None, artist="Ghost", title="Rats", duration=None)
    second = QueueTrack(queue_id="2", song_id=None, artist="Opeth", title="Harvest", duration=None)
    first.raw["key"] = "value"

    assert second.raw == {}
