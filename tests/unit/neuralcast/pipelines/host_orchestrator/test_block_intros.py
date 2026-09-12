"""Timing selection is independent of song playlist membership."""

import datetime as dt

import pytest

from neuralcast.pipelines.host_orchestrator.block_intros import plan_block_intro
from neuralcast.pipelines.host_orchestrator.models import QueueTrack

START = dt.datetime(2026, 9, 12, 18, tzinfo=dt.timezone.utc).timestamp()
SCHEDULE = {
    "timezone": "UTC",
    "expanded_blocks": [
        {
            "block_key": "2026-09-12|evening",
            "date_local": "2026-09-12",
            "start_time_local": "18:00",
            "end_time_local": "19:00",
            "section_label": "Evening",
            "mode": "playlist",
            "playlist_name": "New block",
        }
    ],
}


def track(name, timestamp):
    return QueueTrack(name, name, "Artist", name, 180, {"played_at": timestamp})


def test_selects_third_song_by_play_time_even_from_wrong_playlist():
    current = track("current", START - 600)
    queue = [
        track("one", START - 240),
        track("two", START - 60),
        track("three", START + 120),
    ]
    queue[-1].raw["playlist"] = "Old block"
    queue[-1].raw["cued_at"] = START - 500
    plan = plan_block_intro(SCHEDULE, START - 300, current, 60, queue, {})
    assert plan.target.title == "three"
    assert plan.predecessor.title == "two"
    assert plan.target_index == 2
    assert plan.context.mention_intent == "start"


def test_exact_start_is_eligible_and_mentioned_block_is_not():
    current = track("current", START - 180)
    queue = [track("target", START)]
    assert (
        plan_block_intro(SCHEDULE, START - 60, current, 60, queue, {}).target_index == 0
    )
    mentions = {"2026-09-12|evening": {"start": True}}
    assert plan_block_intro(SCHEDULE, START - 60, current, 60, queue, mentions) is None


def test_queue_does_not_yet_reach_boundary():
    assert (
        plan_block_intro(
            SCHEDULE,
            START - 300,
            track("current", START - 500),
            60,
            [track("one", START - 240)],
            {},
        )
        is None
    )


@pytest.mark.parametrize("value", [None, "invalid", float("nan"), float("inf"), 0])
def test_missing_or_invalid_play_time_waits(value):
    assert (
        plan_block_intro(
            SCHEDULE,
            START - 60,
            track("current", START - 180),
            60,
            [track("target", value)],
            {},
        )
        is None
    )


def test_skips_boundary_that_already_passed_instead_of_introducing_late():
    assert (
        plan_block_intro(
            SCHEDULE,
            START + 120,
            track("already in block", START + 30),
            90,
            [track("second", START + 210)],
            {},
        )
        is None
    )


def test_song_straddling_start_can_still_receive_intro_at_its_end():
    plan = plan_block_intro(
        SCHEDULE,
        START + 30,
        track("old song", START - 120),
        60,
        [track("target", START + 90)],
        {},
    )
    assert plan.target_index == 0


def test_timezone_and_midnight_boundary():
    state = {
        "timezone": "Europe/Zurich",
        "expanded_blocks": [
            {
                **SCHEDULE["expanded_blocks"][0],
                "date_local": "2026-09-13",
                "start_time_local": "00:00",
                "end_time_local": "01:00",
            }
        ],
    }
    midnight = START + 4 * 3600
    plan = plan_block_intro(
        state,
        midnight - 60,
        track("old", midnight - 180),
        90,
        [track("new", midnight + 30)],
        {},
    )
    assert plan.context.start_local_iso == "2026-09-13T00:00:00+02:00"


def test_only_prepare_within_lookahead():
    assert (
        plan_block_intro(
            SCHEDULE,
            START - 700,
            track("old", START - 800),
            750,
            [track("target", START + 50)],
            {},
        )
        is None
    )
