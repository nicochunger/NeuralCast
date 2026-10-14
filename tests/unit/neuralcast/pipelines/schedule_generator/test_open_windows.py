"""NeuralCast open-window construction and enforcement."""

import random

import pytest

from neuralcast.pipelines.schedule_generator.models import (
    ScheduleValidationError,
    StationPlaylist,
)
from neuralcast.pipelines.schedule_generator.scaffold import _build_station_scaffold
from neuralcast.pipelines.schedule_generator.assignment import (
    _assign_playlists_to_scaffold,
)
from neuralcast.pipelines.schedule_generator.template import validate_daily_template
from neuralcast.pipelines.schedule_generator.runtime import (
    ScheduleRunRequest,
    resolve_open_slot_bounds,
)


def scaffold(min_slots=6, max_slots=8):
    names = ["Classic Reggae", "Aspen Vibes", "Acoustic Singer-Songwriter"]
    names += [f"Playlist {index}" for index in range(20)]
    playlists = [
        StationPlaylist(str(index), name, True, 1.0, [], {})
        for index, name in enumerate(names)
    ]
    rng = random.Random(42)
    blocks = _build_station_scaffold(
        station_slug="neuralcast",
        playlists=playlists,
        open_ratio_min=0.4,
        open_ratio_max=0.5,
        min_open_slots=min_slots,
        max_open_slots=max_slots,
        min_block_minutes=30,
        max_block_minutes=75,
        playlist_capacity=len(playlists),
        rng=rng,
    )
    _assign_playlists_to_scaffold(
        station_slug="neuralcast", playlists=playlists, raw_blocks=blocks, rng=rng
    )
    return blocks, {p.id: p for p in playlists}


def test_explicit_window_count_is_honored():
    blocks, _ = scaffold(7, 7)
    assert sum(block["mode"] == "open" for block in blocks) == 7


def test_impossible_window_count_fails_instead_of_relaxing_constraints():
    with pytest.raises(ScheduleValidationError, match="No feasible NeuralCast"):
        scaffold(2, 2)


@pytest.mark.parametrize(
    "station,expected", [("neuralcast", (6, 8)), ("neuralforge", (3, 6))]
)
def test_station_defaults_and_explicit_count_overrides(station, expected):
    request = ScheduleRunRequest(station=station, base_url="", api_key="")
    assert resolve_open_slot_bounds(request) == expected
    explicit = ScheduleRunRequest(
        station=station, base_url="", api_key="", min_open_slots=3, max_open_slots=6
    )
    assert resolve_open_slot_bounds(explicit) == (3, 6)


def test_validation_rejects_wrong_open_count_and_duration():
    blocks, playlists = scaffold()
    arguments = dict(
        raw_blocks=blocks,
        playlist_by_id=playlists,
        open_ratio_min=0.4,
        open_ratio_max=0.5,
        min_block_minutes=30,
        max_block_minutes=75,
        enforce_unscheduled_window=False,
        min_open_block_minutes=75,
        max_open_block_minutes=120,
    )
    with pytest.raises(ScheduleValidationError, match="Open-window count"):
        validate_daily_template(**arguments, min_open_slots=9)
    arguments["max_open_block_minutes"] = 74
    with pytest.raises(ScheduleValidationError, match="Block duration"):
        validate_daily_template(**arguments)


def test_validation_rejects_adjacent_open_windows():
    blocks, playlists = scaffold()
    blocks[1]["mode"] = "open"
    with pytest.raises(ScheduleValidationError, match="separated"):
        validate_daily_template(
            raw_blocks=blocks,
            playlist_by_id=playlists,
            open_ratio_min=0,
            open_ratio_max=1,
            min_block_minutes=30,
            max_block_minutes=75,
            min_open_block_minutes=30,
            max_open_block_minutes=120,
            enforce_unscheduled_window=False,
            require_separate_open_blocks=True,
        )
