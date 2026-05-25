"""Unit tests for host-orchestrator AzuraCast transport helpers."""

from __future__ import annotations

import pytest

from neuralcast.pipelines.host_orchestrator import transport


def test_parse_queue_tracks_accepts_nested_and_flat_payloads() -> None:
    tracks = transport.parse_queue_tracks(
        [
            {
                "id": "queue-1",
                "duration": "240",
                "song": {"id": 10, "artist": "Ghost", "title": "Rats"},
            },
            {"queue_id": "queue-2", "artist": "Opeth", "title": "Harvest", "length": 360},
            {"id": "skip", "song": {"artist": "No Title"}},
        ]
    )

    assert [(track.queue_id, track.song_id, track.artist, track.title, track.duration) for track in tracks] == [
        ("queue-1", "10", "Ghost", "Rats", 240),
        ("queue-2", None, "Opeth", "Harvest", 360),
    ]


def test_extract_current_track_uses_remaining_and_duration_candidates() -> None:
    track, remaining = transport.extract_current_track(
        {
            "now_playing": {
                "remaining": "42",
                "song": {
                    "id": 99,
                    "artist": "Amorphis",
                    "title": "Black Winter Day",
                    "length": "244",
                },
            }
        }
    )

    assert track.queue_id == "99"
    assert track.duration == 244
    assert remaining == 42


def test_extract_current_track_rejects_missing_title() -> None:
    with pytest.raises(RuntimeError, match="current song title"):
        transport.extract_current_track({"now_playing": {"song": {"artist": "Ghost"}}})


def test_listener_and_upcoming_helpers_ignore_current_track() -> None:
    current, _remaining = transport.extract_current_track(
        {"listeners": {"current": "7"}, "now_playing": {"song": {"id": 1, "artist": "Ghost", "title": "Rats"}}}
    )
    queue = transport.parse_queue_tracks(
        [
            {"id": "same", "song": {"id": 1, "artist": "Ghost", "title": "Rats"}},
            {"id": "next", "song": {"id": 2, "artist": "Opeth", "title": "Harvest"}},
        ]
    )

    assert transport.extract_current_listeners({"listeners": {"current": "7"}}) == 7
    assert transport.choose_next_track(current, queue).title == "Harvest"
    assert transport.choose_upcoming_tracks(current, queue, limit=0) == []


def test_station_and_upload_response_helpers() -> None:
    station = transport.choose_station_payload(
        [{"shortcode": "neuralcast", "name": "NeuralCast"}],
        "NeuralCast",
    )

    assert transport.derive_station_display_name(station, "fallback") == "NeuralCast"
    assert transport.extract_upload_storage_path({"data": {"storage_location": "AI Stories/x.mp3"}}) == "AI Stories/x.mp3"
    assert transport.extract_upload_duration({"data": {"length": "12.8"}}) == 12
    assert transport.extract_telnet_request_id({"logs": [{"context": {"response": ["ok", "request-1"]}}]}) == "request-1"


def test_build_request_command_escapes_annotation_values() -> None:
    command = transport.build_request_command(
        "AI Stories/item.mp3",
        'A "quoted" title',
        12,
    )

    assert command == (
        'requests.push annotate:title="A \\"quoted\\" title",'
        'artist="NeuralCast AI",duration="12":AI Stories/item.mp3'
    )
