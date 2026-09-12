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
        "timezone": "Europe/Zurich",
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


def test_early_break_does_not_introduce_upcoming_block() -> None:
    timezone = ZoneInfo("Europe/Zurich")
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


def test_early_break_ignores_even_three_matching_playlists() -> None:
    timezone = ZoneInfo("Europe/Zurich")
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
    assert context.section_label == "Bloque libre"
    assert context.mention_intent is None


def test_block_intro_after_scheduled_start_does_not_require_three_tracks() -> None:
    timezone = ZoneInfo("Europe/Zurich")
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


def test_editorial_combo_title_reaches_localized_host_prompt() -> None:
    from neuralcast.pipelines.host_orchestrator.channels import get_channel_registry
    from neuralcast.pipelines.host_orchestrator.models import (
        Archetype, StationPersonality, TrackMetadata,
    )
    from neuralcast.pipelines.host_orchestrator.prompts import format_shared_input

    state = _open_to_aspen_schedule("2026-08-24")
    state["plan_hash"] = "current"
    entry = state["expanded_blocks"][1]
    entry["playlist_ids"] = ["20", "21"]
    entry["playlist_names"] = ["Aspen Vibes", "Acoustic Singer-Songwriter"]
    state["presentation"] = {
        "plan_hash": "current",
        "blocks": [{
            "kind": "combo", "playlist_ids": ["21", "20"],
            "translations": {
                "es": {"title": "Refugio Acustico"},
                "en": {"title": "Acoustic Retreat"},
            },
        }],
    }
    timestamp = dt.datetime(2026, 8, 24, 19, 30, tzinfo=ZoneInfo("Europe/Zurich")).timestamp()
    context = schedule.resolve_schedule_context(state, timestamp, {})
    assert context.official_titles["es"] == "Refugio Acustico"
    assert context.playlist_names == entry["playlist_names"]
    track = _queue_track("1", "Artist", "Song", "Aspen Vibes")
    for tag, title, rule in [
        ("es-AR", "Refugio Acustico", "No enumerar ni recitar"),
        ("fr-CH", "Acoustic Retreat", "Ne pas énumérer ni réciter"),
    ]:
        prompt = format_shared_input(
            archetype=Archetype.BLOCK_INTRO, station_name="NeuralForge",
            personality=StationPersonality("warm", "warm"),
            current=track, next_track=track, upcoming_tracks=[],
            current_meta=TrackMetadata(), next_meta=TrackMetadata(),
            angle=None, hook="", banned_list=[], recent_scripts=[],
            schedule_context=context, locale=get_channel_registry().locales[tag],
        )
        assert title in prompt
        assert rule in prompt
        assert "Acoustic Singer-Songwriter + Aspen Vibes" not in prompt

    before = schedule.resolve_schedule_context(state, timestamp - 60, {})
    assert before.official_titles == {}
    assert before.next_official_titles == context.official_titles

    state["presentation"]["plan_hash"] = "outdated"
    assert schedule.resolve_schedule_context(state, timestamp, {}).official_titles == {}
    state["presentation"]["plan_hash"] = "current"
    state["presentation"]["blocks"][0]["playlist_ids"] = ["20", "99"]
    assert schedule.resolve_schedule_context(state, timestamp, {}).official_titles == {}
