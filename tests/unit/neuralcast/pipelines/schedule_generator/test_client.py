"""Unit tests for schedule-generator AzuraCast client helpers."""

from __future__ import annotations

import pytest

from neuralcast.pipelines.schedule_generator import client as schedule_client
from neuralcast.pipelines.schedule_generator.models import DailyTemplateBlock
from tests.factories import station_playlist_factory


def test_extract_station_playlists_filters_incomplete_entries() -> None:
    playlists = schedule_client.extract_station_playlists(
        [
            {"id": 1, "name": "Prog", "is_enabled": True, "weight": "2.5", "schedule_items": [{"days": []}]},
            {"id": None, "name": "Missing"},
            {"id": 2, "name": ""},
        ]
    )

    assert len(playlists) == 1
    assert playlists[0].id == "1"
    assert playlists[0].weight == 2.5
    assert playlists[0].schedule_items == [{"days": []}]


def test_choose_station_payload_reports_available_shortcodes() -> None:
    with pytest.raises(RuntimeError, match="Available: neuralcast"):
        schedule_client.choose_station_payload([{"shortcode": "neuralcast"}], "missing")


def test_derive_station_timezone_uses_valid_nested_timezone_or_fallback() -> None:
    assert schedule_client.derive_station_timezone({"station": {"timezone": "Europe/Zurich"}}) == "Europe/Zurich"
    assert schedule_client.derive_station_timezone({"timezone": "Not/AZone"}) == schedule_client.FALLBACK_TIMEZONE


def test_schedule_state_helpers_load_invalid_as_none_and_save_atomically(tmp_path) -> None:
    path = tmp_path / "metadata" / "schedule.json"

    assert schedule_client.load_schedule_state(path) is None
    path.parent.mkdir()
    path.write_text("{not json", encoding="utf-8")
    assert schedule_client.load_schedule_state(path) is None

    schedule_client.save_schedule_state_atomic(path, {"timezone": "UTC"})

    assert schedule_client.load_schedule_state(path) == {"timezone": "UTC"}
    assert not path.with_suffix(".json.tmp").exists()


def test_apply_weekly_schedule_updates_every_playlist_with_built_items(monkeypatch) -> None:
    playlists = [
        station_playlist_factory(playlist_id="10", name="Prog"),
        station_playlist_factory(playlist_id="11", name="Open Target"),
    ]
    template = [
        DailyTemplateBlock(
            start_time_local="00:00",
            end_time_local="01:00",
            start_minute=0,
            end_minute=60,
            mode="playlist",
            section_label="Prog",
            genre_labels=["prog"],
            playlist_ids=["10"],
            playlist_names=["Prog"],
            playlist_id="10",
            playlist_name="Prog",
        ),
        DailyTemplateBlock(
            start_time_local="01:00",
            end_time_local="02:00",
            start_minute=60,
            end_minute=120,
            mode="open",
            section_label="Open",
            genre_labels=["mixed"],
            playlist_ids=[],
            playlist_names=[],
        ),
    ]
    calls: list[tuple[str, list[dict]]] = []

    class FakeClient:
        def update_playlist_schedule_items(self, *, station: str, playlist_id: str, schedule_items: list[dict]) -> dict:
            calls.append((playlist_id, schedule_items))
            return {"ok": True}

    monkeypatch.setattr(schedule_client, "run_with_retries", lambda _label, func: func())

    updated_playlists, updated_items = schedule_client.apply_weekly_schedule(
        FakeClient(),
        "neuralforge",
        playlists,
        template,
    )

    assert updated_playlists == 2
    assert updated_items == 3
    assert calls[0][0] == "10"
    assert len(calls[0][1]) == 2
    assert calls[1][0] == "11"
    assert len(calls[1][1]) == 1
