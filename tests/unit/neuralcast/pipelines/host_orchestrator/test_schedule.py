"""Unit tests for host orchestrator schedule helpers."""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

from neuralcast.pipelines.host_orchestrator import schedule
from neuralcast.pipelines.host_orchestrator.models import QueueTrack


def _queue_track(
    queue_id: str,
    artist: str,
    title: str,
    playlist_name: str,
) -> QueueTrack:
    return QueueTrack(
        queue_id=queue_id,
        song_id=queue_id,
        artist=artist,
        title=title,
        duration=240,
        raw={"playlist": {"name": playlist_name}},
    )


def _open_to_aspen_schedule(date_local: str) -> dict[str, object]:
    return {
        "timezone": "Europe/Berlin",
        "expanded_blocks": [
            {
                "block_key": f"{date_local}|0|00:00|19:30|open|open",
                "date_local": date_local,
                "start_time_local": "00:00",
                "end_time_local": "19:30",
                "mode": "open",
                "section_label": "Bloque libre",
                "genre_labels": ["mixed"],
            },
            {
                "block_key": f"{date_local}|1|19:30|21:00|playlist|20",
                "date_local": date_local,
                "start_time_local": "19:30",
                "end_time_local": "21:00",
                "mode": "playlist",
                "section_label": "Acoustic Singer-Songwriter + Aspen Vibes",
                "genre_labels": ["acoustic", "singer-songwriter"],
                "playlist_id": "20",
                "playlist_name": "Aspen Vibes",
            },
        ],
    }


def test_resolve_station_metadata_file_prefers_metadata_then_legacy(tmp_path) -> None:
    station_dir = tmp_path / "Station"
    metadata_dir = station_dir / "metadata"
    playlists_dir = station_dir / "playlists"
    metadata_dir.mkdir(parents=True)
    playlists_dir.mkdir()
    legacy = playlists_dir / "state.json"
    legacy.write_text("{}", encoding="utf-8")

    assert schedule.resolve_station_metadata_file(station_dir, "state.json") == legacy

    current = metadata_dir / "state.json"
    current.write_text("{}", encoding="utf-8")
    assert schedule.resolve_station_metadata_file(station_dir, "state.json") == current


def test_early_block_intro_requires_three_consecutive_playlist_matches() -> None:
    timezone = ZoneInfo("Europe/Berlin")
    now_local = dt.datetime(2026, 8, 24, 19, 20, tzinfo=timezone)
    tracks = [
        _queue_track("1", "John Mayer", "Gravity", "Aspen Vibes"),
        _queue_track("2", "La K'onga", "Te Perdiste Mi Amor", "Cuarteto"),
        _queue_track("3", "Adele", "Skyfall", "Movie and TV Soundtracks"),
    ]

    context = schedule.resolve_schedule_context_for_upcoming_break(
        schedule_state=_open_to_aspen_schedule(now_local.date().isoformat()),
        ts_now=now_local.timestamp(),
        ts_break=(now_local + dt.timedelta(minutes=3)).timestamp(),
        mention_state={},
        next_track=tracks[0],
        upcoming_tracks=tracks,
    )

    assert context is not None
    assert context.section_label == "Bloque libre"
    assert context.mention_intent is None


def test_early_block_intro_accepts_three_consecutive_playlist_matches() -> None:
    timezone = ZoneInfo("Europe/Berlin")
    now_local = dt.datetime(2026, 8, 24, 19, 20, tzinfo=timezone)
    tracks = [
        _queue_track("1", "John Mayer", "Gravity", "Aspen Vibes"),
        _queue_track("2", "Ed Sheeran", "Photograph", "Aspen Vibes"),
        _queue_track("3", "Adele", "Make You Feel My Love", "Aspen Vibes"),
    ]

    context = schedule.resolve_schedule_context_for_upcoming_break(
        schedule_state=_open_to_aspen_schedule(now_local.date().isoformat()),
        ts_now=now_local.timestamp(),
        ts_break=(now_local + dt.timedelta(minutes=3)).timestamp(),
        mention_state={},
        next_track=tracks[0],
        upcoming_tracks=tracks,
    )

    assert context is not None
    assert context.section_label == "Acoustic Singer-Songwriter + Aspen Vibes"
    assert context.mention_intent == "start"


def test_block_intro_after_scheduled_start_does_not_require_three_tracks() -> None:
    timezone = ZoneInfo("Europe/Berlin")
    now_local = dt.datetime(2026, 8, 24, 19, 31, tzinfo=timezone)
    track = _queue_track("1", "John Mayer", "Gravity", "Aspen Vibes")

    context = schedule.resolve_schedule_context_for_upcoming_break(
        schedule_state=_open_to_aspen_schedule(now_local.date().isoformat()),
        ts_now=now_local.timestamp(),
        ts_break=(now_local + dt.timedelta(minutes=3)).timestamp(),
        mention_state={},
        next_track=track,
        upcoming_tracks=[track],
    )

    assert context is not None
    assert context.section_label == "Acoustic Singer-Songwriter + Aspen Vibes"
    assert context.mention_intent == "start"
