#!/usr/bin/env python3
"""Unit tests for weekly schedule generator helpers."""

from __future__ import annotations

import datetime as dt
import unittest
from unittest import mock

import neuralcast.pipelines.schedule_generator.generation as schedule_generation  # noqa: E402
from neuralcast.pipelines.schedule_generator import (  # noqa: E402
    StationPlaylist,
    azuracast_time_for_api,
    build_schedule_items_by_playlist,
    build_deterministic_daily_template,
    build_arg_parser,
    expand_daily_template_to_week,
    infer_azuracast_days,
    validate_daily_template,
)


class ScheduleGeneratorHelpersTest(unittest.TestCase):
    def setUp(self) -> None:
        self.playlist_a = StationPlaylist(
            id="10",
            name="Prog Metal",
            is_enabled=True,
            weight=3.0,
            schedule_items=[],
            raw={},
        )
        self.playlist_b = StationPlaylist(
            id="11",
            name="Symphonic Metal",
            is_enabled=True,
            weight=2.0,
            schedule_items=[],
            raw={},
        )
        self.playlist_map = {
            self.playlist_a.id: self.playlist_a,
            self.playlist_b.id: self.playlist_b,
        }

    def test_validate_daily_template_accepts_full_day_with_open_slots(self) -> None:
        raw_blocks = [
            {
                "start_time_local": "00:00",
                "end_time_local": "06:00",
                "mode": "open",
                "section_label": "Night Rotation",
                "genre_labels": ["mixed"],
            },
            {
                "start_time_local": "06:00",
                "end_time_local": "14:00",
                "mode": "playlist",
                "playlist_id": "10",
                "playlist_name": "Prog Metal",
                "section_label": "Prog Morning",
                "genre_labels": ["prog", "metal"],
            },
            {
                "start_time_local": "14:00",
                "end_time_local": "22:00",
                "mode": "playlist",
                "playlist_id": "11",
                "playlist_name": "Symphonic Metal",
                "section_label": "Symphonic Day",
                "genre_labels": ["symphonic"],
            },
            {
                "start_time_local": "22:00",
                "end_time_local": "24:00",
                "mode": "open",
                "section_label": "Late Night Rotation",
                "genre_labels": ["mixed"],
            },
        ]

        blocks = validate_daily_template(
            raw_blocks=raw_blocks,
            playlist_by_id=self.playlist_map,
            open_ratio_min=0.20,
            open_ratio_max=0.40,
            min_block_minutes=30,
            max_block_minutes=720,
        )
        self.assertEqual(len(blocks), 4)
        self.assertEqual(blocks[0].mode, "open")
        self.assertEqual(blocks[-1].mode, "open")

    def test_validate_daily_template_rejects_gap(self) -> None:
        raw_blocks = [
            {
                "start_time_local": "00:00",
                "end_time_local": "06:00",
                "mode": "open",
                "section_label": "Night Rotation",
                "genre_labels": ["mixed"],
            },
            {
                "start_time_local": "07:00",
                "end_time_local": "22:00",
                "mode": "playlist",
                "playlist_id": "11",
                "section_label": "Symphonic Day",
                "genre_labels": ["symphonic"],
            },
            {
                "start_time_local": "22:00",
                "end_time_local": "24:00",
                "mode": "open",
                "section_label": "Late Night Rotation",
                "genre_labels": ["mixed"],
            },
        ]

        with self.assertRaises(ValueError):
            validate_daily_template(
                raw_blocks=raw_blocks,
                playlist_by_id=self.playlist_map,
                open_ratio_min=0.0,
                open_ratio_max=0.5,
                min_block_minutes=30,
                max_block_minutes=720,
            )

    def test_validate_daily_template_rejects_open_ratio_out_of_bounds(self) -> None:
        raw_blocks = [
            {
                "start_time_local": "00:00",
                "end_time_local": "06:00",
                "mode": "open",
                "section_label": "Night Rotation",
                "genre_labels": ["mixed"],
            },
            {
                "start_time_local": "06:00",
                "end_time_local": "22:00",
                "mode": "playlist",
                "playlist_id": "11",
                "section_label": "Symphonic Day",
                "genre_labels": ["symphonic"],
            },
            {
                "start_time_local": "22:00",
                "end_time_local": "24:00",
                "mode": "open",
                "section_label": "Late Night Rotation",
                "genre_labels": ["mixed"],
            },
        ]

        with self.assertRaises(ValueError):
            validate_daily_template(
                raw_blocks=raw_blocks,
                playlist_by_id=self.playlist_map,
                open_ratio_min=0.00,
                open_ratio_max=0.20,
                min_block_minutes=30,
                max_block_minutes=720,
            )

    def test_expand_daily_template_to_week_repeats_daily_layout(self) -> None:
        raw_blocks = [
            {
                "start_time_local": "00:00",
                "end_time_local": "06:00",
                "mode": "open",
                "section_label": "Night Rotation",
                "genre_labels": ["mixed"],
            },
            {
                "start_time_local": "06:00",
                "end_time_local": "14:00",
                "mode": "playlist",
                "playlist_id": "10",
                "section_label": "Daytime Part 1",
                "genre_labels": ["prog"],
            },
            {
                "start_time_local": "14:00",
                "end_time_local": "22:00",
                "mode": "playlist",
                "playlist_id": "11",
                "section_label": "Daytime Part 2",
                "genre_labels": ["symphonic"],
            },
            {
                "start_time_local": "22:00",
                "end_time_local": "24:00",
                "mode": "open",
                "section_label": "Late Night Rotation",
                "genre_labels": ["mixed"],
            },
        ]

        daily = validate_daily_template(
            raw_blocks=raw_blocks,
            playlist_by_id=self.playlist_map,
            open_ratio_min=0.30,
            open_ratio_max=0.40,
            min_block_minutes=30,
            max_block_minutes=720,
        )
        expanded = expand_daily_template_to_week(daily, week_start=dt.date(2026, 2, 16))
        self.assertEqual(len(expanded), 28)
        self.assertEqual(expanded[0].date_local, "2026-02-16")
        self.assertEqual(expanded[-1].date_local, "2026-02-22")

    def test_build_schedule_items_by_playlist_applies_open_blocks_to_enabled_playlists(self) -> None:
        raw_blocks = [
            {
                "start_time_local": "00:00",
                "end_time_local": "06:00",
                "mode": "open",
                "section_label": "Night Rotation",
                "genre_labels": ["mixed"],
            },
            {
                "start_time_local": "06:00",
                "end_time_local": "14:00",
                "mode": "playlist",
                "playlist_id": "10",
                "section_label": "Day Part 1",
                "genre_labels": ["prog"],
            },
            {
                "start_time_local": "14:00",
                "end_time_local": "22:00",
                "mode": "playlist",
                "playlist_id": "11",
                "section_label": "Day Part 2",
                "genre_labels": ["symphonic"],
            },
            {
                "start_time_local": "22:00",
                "end_time_local": "24:00",
                "mode": "open",
                "section_label": "Late Night Rotation",
                "genre_labels": ["mixed"],
            },
        ]
        daily = validate_daily_template(
            raw_blocks=raw_blocks,
            playlist_by_id=self.playlist_map,
            open_ratio_min=0.15,
            open_ratio_max=0.35,
            min_block_minutes=30,
            max_block_minutes=720,
        )

        items = build_schedule_items_by_playlist(
            playlists=[self.playlist_a, self.playlist_b],
            daily_template=daily,
            day_values=[0, 1, 2, 3, 4, 5, 6],
        )
        self.assertEqual(len(items["10"]), 3)
        self.assertEqual(len(items["11"]), 3)

        # Open blocks are applied to all enabled playlists so AzuraCast can do
        # weighted random selection during those windows.
        self.assertEqual(items["10"][0]["start_time"], 0)
        self.assertEqual(items["10"][0]["end_time"], 600)
        self.assertEqual(items["10"][1]["start_time"], 600)
        self.assertEqual(items["10"][1]["end_time"], 1400)
        self.assertEqual(items["10"][2]["start_time"], 2200)
        self.assertEqual(items["10"][2]["end_time"], 2359)

        self.assertEqual(items["11"][0]["start_time"], 0)
        self.assertEqual(items["11"][0]["end_time"], 600)
        self.assertEqual(items["11"][1]["start_time"], 1400)
        self.assertEqual(items["11"][1]["end_time"], 2200)
        self.assertEqual(items["11"][2]["start_time"], 2200)
        self.assertEqual(items["11"][2]["end_time"], 2359)

    def test_infer_azuracast_days_prefers_existing_shape(self) -> None:
        playlist = StationPlaylist(
            id="1",
            name="Existing",
            is_enabled=True,
            weight=1.0,
            schedule_items=[{"start_time": "00:00", "end_time": "01:00", "days": [1, 2, 3, 4, 5, 6, 7]}],
            raw={},
        )
        days = infer_azuracast_days([playlist])
        self.assertEqual(days, [1, 2, 3, 4, 5, 6, 7])

    def test_infer_azuracast_days_preserves_empty_days_shape(self) -> None:
        playlist = StationPlaylist(
            id="2",
            name="All Days",
            is_enabled=True,
            weight=1.0,
            schedule_items=[{"start_time": 730, "end_time": 900, "days": []}],
            raw={},
        )
        days = infer_azuracast_days([playlist])
        self.assertEqual(days, [])

    def test_azuracast_time_for_api_uses_hhmm_integer(self) -> None:
        self.assertEqual(azuracast_time_for_api("04:00"), 400)
        self.assertEqual(azuracast_time_for_api("07:30"), 730)
        self.assertEqual(azuracast_time_for_api("24:00"), 2359)

    def test_build_arg_parser_defaults_max_block_minutes_to_ninety(self) -> None:
        args = build_arg_parser().parse_args(["--base-url", "https://example.test"])
        self.assertEqual(args.max_block_minutes, 90)
        self.assertEqual(args.seed_mode, schedule_generation.SCHEDULE_SEED_MODE_STABLE_WEEK)

    def test_resolve_schedule_seed_custom_mode_uses_supplied_salt(self) -> None:
        resolved_seed, seed_mode, seed_salt = schedule_generation.resolve_schedule_seed(
            station_slug="neuralcast",
            week_start=dt.date(2026, 2, 16),
            timezone_name="UTC",
            playlists=[self.playlist_a, self.playlist_b],
            open_ratio_min=0.20,
            open_ratio_max=0.40,
            min_open_slots=2,
            max_open_slots=4,
            min_block_minutes=60,
            max_block_minutes=180,
            seed_mode=schedule_generation.SCHEDULE_SEED_MODE_CUSTOM,
            seed_salt="reroll-a",
        )

        self.assertEqual(seed_mode, schedule_generation.SCHEDULE_SEED_MODE_CUSTOM)
        self.assertEqual(seed_salt, "reroll-a")
        self.assertIsInstance(resolved_seed, int)

        second_seed, _second_mode, _second_salt = schedule_generation.resolve_schedule_seed(
            station_slug="neuralcast",
            week_start=dt.date(2026, 2, 16),
            timezone_name="UTC",
            playlists=[self.playlist_a, self.playlist_b],
            open_ratio_min=0.20,
            open_ratio_max=0.40,
            min_open_slots=2,
            max_open_slots=4,
            min_block_minutes=60,
            max_block_minutes=180,
            seed_mode=schedule_generation.SCHEDULE_SEED_MODE_CUSTOM,
            seed_salt="reroll-b",
        )
        self.assertNotEqual(resolved_seed, second_seed)

    def test_build_weekly_plan_with_code_records_generated_fresh_seed_salt(self) -> None:
        week_start = dt.date(2026, 2, 16)
        playlists = [
            StationPlaylist(
                id=str(index),
                name=f"Playlist {index}",
                is_enabled=True,
                weight=1.0,
                schedule_items=[],
                raw={},
            )
            for index in range(1, 13)
        ]
        with mock.patch.object(
            schedule_generation.secrets,
            "token_hex",
            return_value="fresh-seed-01",
        ):
            plan = schedule_generation.build_weekly_plan_with_code(
                station_slug="neuralcast",
                station_name="NeuralCast",
                timezone_name="UTC",
                week_start=week_start,
                week_end=week_start + dt.timedelta(days=6),
                playlists=playlists,
                open_ratio_min=0.20,
                open_ratio_max=0.40,
                min_open_slots=2,
                max_open_slots=4,
                min_block_minutes=60,
                max_block_minutes=180,
                seed_mode=schedule_generation.SCHEDULE_SEED_MODE_FRESH,
            )

        self.assertEqual(plan.seed_mode, schedule_generation.SCHEDULE_SEED_MODE_FRESH)
        self.assertEqual(plan.seed_salt, "fresh-seed-01")
        self.assertIsInstance(plan.resolved_seed, int)
        self.assertTrue(plan.daily_template)

    def test_validate_daily_template_rejects_playlist_in_unscheduled_window(self) -> None:
        raw_blocks = [
            {
                "start_time_local": "00:00",
                "end_time_local": "05:00",
                "mode": "open",
                "section_label": "Night Rotation",
                "genre_labels": ["mixed"],
            },
            {
                "start_time_local": "05:00",
                "end_time_local": "08:00",
                "mode": "playlist",
                "playlist_id": "10",
                "section_label": "Too Early",
                "genre_labels": ["prog"],
            },
            {
                "start_time_local": "08:00",
                "end_time_local": "22:00",
                "mode": "playlist",
                "playlist_id": "11",
                "section_label": "Day",
                "genre_labels": ["symphonic"],
            },
            {
                "start_time_local": "22:00",
                "end_time_local": "24:00",
                "mode": "open",
                "section_label": "Late Night Rotation",
                "genre_labels": ["mixed"],
            },
        ]
        with self.assertRaises(ValueError):
            validate_daily_template(
                raw_blocks=raw_blocks,
                playlist_by_id=self.playlist_map,
                open_ratio_min=0.20,
                open_ratio_max=0.60,
                min_block_minutes=30,
                max_block_minutes=720,
            )

    def test_build_deterministic_daily_template_meets_constraints(self) -> None:
        template = build_deterministic_daily_template(
            playlist_by_id=self.playlist_map,
            open_ratio_min=0.20,
            open_ratio_max=0.40,
            min_block_minutes=30,
            max_block_minutes=90,
        )
        self.assertGreater(len(template), 0)
        self.assertEqual(template[0].start_time_local, "00:00")
        self.assertEqual(template[-1].end_time_local, "24:00")
        for block in template:
            self.assertLessEqual(block.end_minute - block.start_minute, 90)
            if block.start_minute < 360 or block.end_minute > 1320:
                self.assertEqual(block.mode, "open")

    def test_neuralforge_hard_rock_programming_metadata_is_wired(self) -> None:
        label_map = schedule_generation._station_label_map("neuralforge")
        self.assertEqual(
            label_map[schedule_generation._name_key("Hard Rock")],
            ("Hard rock", ("hard rock",)),
        )

        playlists = {
            schedule_generation._name_key("Hard Rock"): StationPlaylist(
                id="77",
                name="Hard Rock",
                is_enabled=True,
                weight=1.0,
                schedule_items=[],
                raw={},
            ),
            schedule_generation._name_key("Classic Metal"): StationPlaylist(
                id="23",
                name="Classic Metal",
                is_enabled=True,
                weight=1.0,
                schedule_items=[],
                raw={},
            ),
        }
        combos = schedule_generation._neuralforge_combo_presets(playlists)
        self.assertTrue(
            any(
                combo.playlist_names == ("Hard Rock", "Classic Metal")
                and combo.section_label == "Hard y heavy"
                and combo.genre_labels == ("hard rock", "metal clasico")
                for combo in combos
            )
        )


if __name__ == "__main__":
    unittest.main()
