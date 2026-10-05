"""Playlist eligibility policy tests."""

import pytest

from neuralcast.pipelines.schedule_generator.models import StationPlaylist
from neuralcast.pipelines.schedule_generator.policy import themed_playlists


def test_themed_eligibility_normalizes_names_and_preserves_enabled_status() -> None:
    playlists = [
        StationPlaylist("1", "  BOSSA   NOVA ", True, 1.0, [], {}),
        StationPlaylist("2", "Classic Rock", True, 1.0, [], {}),
        StationPlaylist("3", "Indie Vibes", False, 1.0, [], {}),
    ]
    assert themed_playlists(" NeuralCast ", playlists) == [playlists[1]]
    assert themed_playlists("neuralforge", playlists) == playlists[:2]
    assert playlists[0].is_enabled


def test_planner_rejects_inventory_with_only_open_rotation_playlists() -> None:
    import datetime as dt

    from neuralcast.pipelines.schedule_generator.generation import (
        build_weekly_plan_with_code,
    )

    with pytest.raises(RuntimeError, match="eligible for themed"):
        build_weekly_plan_with_code(
            "neuralforge",
            "NeuralForge",
            "UTC",
            dt.date(2026, 10, 5),
            dt.date(2026, 10, 11),
            [StationPlaylist("1", "NWOBHM", True, 1.0, [], {})],
            0.20,
            0.40,
            3,
            6,
            30,
            90,
        )
