"""Open-only playlists stay in rotation through planning and API conversion."""

import datetime as dt
from collections import Counter

import pytest

from neuralcast.pipelines.schedule_generator.client import (
    build_schedule_items_by_playlist,
)
from neuralcast.pipelines.schedule_generator.generation import (
    build_weekly_plan_with_code,
)
from neuralcast.pipelines.schedule_generator.models import StationPlaylist


@pytest.mark.parametrize("week_offset", [0, 1, 2, 3])
@pytest.mark.parametrize(
    "station,eligible_names,open_only_names,open_bounds,max_minutes",
    [
        (
            "neuralcast",
            [
                "Classic Reggae",
                "Modern Reggae",
                "Reggae Argentino",
                "Reggae Rock",
                "Aspen Vibes",
                "Acoustic Singer-Songwriter",
                "Irish Folk",
                "Movie and TV Soundtracks",
                "Neo Prog",
            ]
            + [f"Other Playlist {index}" for index in range(14)],
            [
                "Bossa Nova",
                "Cuarteto",
                "Cumbia Villera",
                "Eclectic Discovery",
                "Evening Jazz",
                "Folklore Argentino and Chamame",
                "Funk & Soul",
                "Golden HipHop",
                "Mid-Century Popular Foundations",
                "Punk Rock",
                "Romanticismo Argentino",
                "Symphonic Classic",
                "Tango",
            ],
            (0.30, 0.45),
            75,
        ),
        (
            "neuralforge",
            [
                "Classic Metal",
                "Fantasy Metal",
                "Folk Metal",
                "Folk Rock",
                "Hard Rock",
                "Melodic Death Metal",
                "New Releases",
                "Power Metal",
                "Prog Metal",
                "Symphonic Metal",
            ],
            ["NWOBHM", "Neo Classical Metal", "Celtic Metal"],
            (0.20, 0.40),
            90,
        ),
    ],
)
def test_open_only_playlists_never_receive_themed_schedule_items(
    station, eligible_names, open_only_names, open_bounds, max_minutes, week_offset
) -> None:
    playlists = [
        StationPlaylist(str(index), name, True, 1.0, [], {})
        for index, name in enumerate(eligible_names + open_only_names)
    ]
    disabled = StationPlaylist("disabled", "Disabled Playlist", False, 1.0, [], {})
    playlists.append(disabled)
    week_start = dt.date(2026, 10, 5) + dt.timedelta(weeks=week_offset)
    plan = build_weekly_plan_with_code(
        station,
        station,
        "Europe/Zurich",
        week_start,
        week_start + dt.timedelta(days=6),
        playlists,
        *open_bounds,
        3,
        6,
        30,
        max_minutes,
    )
    scheduled_names = Counter(
        name for block in plan.daily_template for name in block.playlist_names
    )
    assert not set(open_only_names).intersection(scheduled_names)
    if station == "neuralcast":
        for name in ("Aspen Vibes", "Acoustic Singer-Songwriter"):
            appearances = [
                block for block in plan.daily_template if name in block.playlist_names
            ]
            assert len(appearances) == 1
            assert appearances[0].start_time_local == "19:30"
            assert appearances[0].end_time_local == "22:00"
    if station == "neuralforge":
        for name, count in scheduled_names.items():
            assert count <= (2 if name == "Melodic Death Metal" else 1)

    items = build_schedule_items_by_playlist(
        playlists, plan.daily_template, [1, 2, 3, 4, 5, 6, 7]
    )
    open_blocks = [block for block in plan.daily_template if block.mode == "open"]
    assert open_blocks
    open_items = items[playlists[len(eligible_names)].id]
    assert len(open_items) == len(open_blocks)
    for playlist in playlists[len(eligible_names) : -1]:
        assert playlist.is_enabled
        assert items[playlist.id] == open_items
    assert items[disabled.id] == []
    for block in plan.daily_template:
        if block.mode == "playlist":
            for playlist_id in block.playlist_ids:
                assert len(items[playlist_id]) > len(open_items)
