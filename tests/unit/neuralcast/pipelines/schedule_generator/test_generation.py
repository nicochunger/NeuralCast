#!/usr/bin/env python3
"""Unit tests for weekly schedule generator helpers."""

from __future__ import annotations

import datetime as dt
import random
import unittest
from collections import Counter
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

    def assertQuarterHourBoundary(self, value: str) -> None:
        hour_text, minute_text = value.split(":", 1)
        minutes = (int(hour_text) * 60) + int(minute_text)
        self.assertEqual(minutes % 15, 0, value)

    def neuralcast_reserved_playlists(self) -> list[StationPlaylist]:
        names = [
            "Reggae Argentino",
            "Reggae Rock",
            "Deep House",
            "Acoustic Singer-Songwriter",
            "Classic Rock",
            "Indie Vibes",
            "Latin Pop",
            "Rock Nacional",
            "Movie and TV Soundtracks",
            "International Heritage",
            "The Modern Frontier",
            "Global Mid-Century Foundations",
            "Romanticismo Argentino",
        ]
        return [
            StationPlaylist(
                id=str(index),
                name=name,
                is_enabled=True,
                weight=1.0,
                schedule_items=[],
                raw={},
            )
            for index, name in enumerate(names, start=1)
        ]

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

    def test_build_arg_parser_defers_block_duration_defaults_to_runtime(self) -> None:
        args = build_arg_parser().parse_args(["--base-url", "https://example.test"])
        self.assertIsNone(args.min_block_minutes)
        self.assertIsNone(args.max_block_minutes)
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
                station_slug="teststation",
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

    def test_randomized_scaffold_spreads_open_blocks_and_keeps_them_longer(self) -> None:
        raw_blocks = schedule_generation._build_randomized_scaffold(
            open_ratio_min=0.20,
            open_ratio_max=0.40,
            min_open_slots=3,
            max_open_slots=6,
            min_block_minutes=30,
            max_block_minutes=90,
            playlist_capacity=12,
            rng=random.Random(7),
        )

        open_durations = [
            int(block["_duration_minutes"])
            for block in raw_blocks
            if block["mode"] == "open"
        ]
        playlist_durations = [
            int(block["_duration_minutes"])
            for block in raw_blocks
            if block["mode"] == "playlist"
        ]

        self.assertTrue(open_durations)
        self.assertTrue(playlist_durations)
        self.assertGreater(
            sum(open_durations) / len(open_durations),
            sum(playlist_durations) / len(playlist_durations),
        )

        cursor = 0
        open_centers = []
        for block in raw_blocks:
            self.assertQuarterHourBoundary(str(block["start_time_local"]))
            self.assertQuarterHourBoundary(str(block["end_time_local"]))
            duration = int(block["_duration_minutes"])
            self.assertEqual(duration % 15, 0)
            if block["mode"] == "open":
                open_centers.append(cursor + (duration / 2.0))
            cursor += duration

        self.assertEqual(cursor, 24 * 60)
        self.assertGreaterEqual(len(open_centers), 3)
        ideal_centers = [
            ((index + 0.5) * (24 * 60) / len(open_centers))
            for index in range(len(open_centers))
        ]
        for actual, ideal in zip(open_centers, ideal_centers):
            self.assertLess(abs(actual - ideal), 180)

        for previous, current in zip(raw_blocks, raw_blocks[1:]):
            self.assertFalse(
                previous["mode"] == "open" and current["mode"] == "open"
            )

    def test_build_weekly_plan_with_code_uses_quarter_hour_boundaries(self) -> None:
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

        plan = schedule_generation.build_weekly_plan_with_code(
            station_slug="teststation",
            station_name="NeuralCast",
            timezone_name="UTC",
            week_start=week_start,
            week_end=week_start + dt.timedelta(days=6),
            playlists=playlists,
            open_ratio_min=0.20,
            open_ratio_max=0.40,
            min_open_slots=3,
            max_open_slots=6,
            min_block_minutes=30,
            max_block_minutes=90,
            seed_mode=schedule_generation.SCHEDULE_SEED_MODE_CUSTOM,
            seed_salt="quarter-hour",
        )

        self.assertTrue(plan.daily_template)
        for block in plan.daily_template:
            self.assertEqual(block.start_minute % 15, 0, block.start_time_local)
            self.assertEqual(block.end_minute % 15, 0, block.end_time_local)
            self.assertEqual((block.end_minute - block.start_minute) % 15, 0)

    def test_neuralcast_reserves_morning_and_evening_playlist_windows(self) -> None:
        week_start = dt.date(2026, 2, 16)
        plan = schedule_generation.build_weekly_plan_with_code(
            station_slug="neuralcast",
            station_name="NeuralCast",
            timezone_name="UTC",
            week_start=week_start,
            week_end=week_start + dt.timedelta(days=6),
            playlists=self.neuralcast_reserved_playlists(),
            open_ratio_min=0.30,
            open_ratio_max=0.45,
            min_open_slots=3,
            max_open_slots=6,
            min_block_minutes=30,
            max_block_minutes=90,
            seed_mode=schedule_generation.SCHEDULE_SEED_MODE_CUSTOM,
            seed_salt="reserved-neuralcast",
        )

        morning_blocks = [
            block
            for block in plan.daily_template
            if block.start_minute >= 7 * 60 and block.end_minute <= 9 * 60
        ]
        evening_blocks = [
            block
            for block in plan.daily_template
            if block.start_minute >= (19 * 60) + 30 and block.end_minute <= 22 * 60
        ]
        self.assertEqual(len(morning_blocks), 1)
        self.assertEqual(len(evening_blocks), 1)
        morning_block = morning_blocks[0]
        evening_block = evening_blocks[0]
        self.assertEqual(morning_block.start_time_local, "07:00")
        self.assertEqual(morning_block.end_time_local, "09:00")
        self.assertEqual(evening_block.start_time_local, "19:30")
        self.assertEqual(evening_block.end_time_local, "22:00")

        self.assertEqual(morning_block.mode, "playlist")
        self.assertEqual(
            set(morning_block.playlist_names),
            {"Reggae Argentino", "Reggae Rock"},
        )
        self.assertEqual(evening_block.mode, "playlist")
        self.assertEqual(
            set(evening_block.playlist_names),
            {"Deep House"},
        )

        open_minutes = sum(
            block.end_minute - block.start_minute
            for block in plan.daily_template
            if block.mode == "open"
        )
        self.assertGreaterEqual(open_minutes / (24 * 60), 0.30)

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

    def test_neuralforge_melodic_death_can_repeat_once_per_day(self) -> None:
        melodic = StationPlaylist(
            id="21",
            name="Melodic Death Metal",
            is_enabled=True,
            weight=1.0,
            schedule_items=[],
            raw={},
        )
        prog = StationPlaylist(
            id="22",
            name="Prog Metal",
            is_enabled=True,
            weight=1.0,
            schedule_items=[],
            raw={},
        )
        symphonic = StationPlaylist(
            id="23",
            name="Symphonic Metal",
            is_enabled=True,
            weight=1.0,
            schedule_items=[],
            raw={},
        )
        raw_blocks = [
            {
                "start_time_local": "00:00",
                "end_time_local": "01:30",
                "mode": "playlist",
                "_duration_minutes": 90,
            },
            {
                "start_time_local": "01:30",
                "end_time_local": "02:30",
                "mode": "playlist",
                "_duration_minutes": 60,
            },
            {
                "start_time_local": "02:30",
                "end_time_local": "04:00",
                "mode": "playlist",
                "_duration_minutes": 90,
            },
            {
                "start_time_local": "04:00",
                "end_time_local": "05:00",
                "mode": "playlist",
                "_duration_minutes": 60,
            },
        ]

        schedule_generation._assign_playlists_to_scaffold(
            station_slug="neuralforge",
            playlists=[melodic, prog, symphonic],
            raw_blocks=raw_blocks,
            rng=random.Random(9),
            allow_combo_presets=False,
        )

        playlist_names = [str(block["playlist_name"]) for block in raw_blocks]
        self.assertEqual(playlist_names.count("Melodic Death Metal"), 2)

    def test_non_neuralforge_playlist_repeat_limit_stays_one(self) -> None:
        playlists = [
            StationPlaylist(
                id=str(index),
                name=f"Playlist {index}",
                is_enabled=True,
                weight=1.0,
                schedule_items=[],
                raw={},
            )
            for index in range(1, 4)
        ]
        raw_blocks = [
            {
                "start_time_local": f"0{index}:00",
                "end_time_local": f"0{index}:30",
                "mode": "playlist",
                "_duration_minutes": 30,
            }
            for index in range(4)
        ]

        with self.assertRaises(ValueError):
            schedule_generation._assign_playlists_to_scaffold(
                station_slug="neuralcast",
                playlists=playlists,
                raw_blocks=raw_blocks,
                rng=random.Random(9),
                allow_combo_presets=False,
            )

    def test_neuralforge_melodic_death_weight_prefers_long_blocks(self) -> None:
        melodic = StationPlaylist(
            id="21",
            name="Melodic Death Metal",
            is_enabled=True,
            weight=1.0,
            schedule_items=[],
            raw={},
        )
        candidate = schedule_generation._solo_candidate(
            melodic,
            "neuralforge",
            schedule_generation._station_label_map("neuralforge"),
        )

        short_weight = schedule_generation._candidate_selection_weight(
            candidate=candidate,
            station_slug="neuralforge",
            duration_minutes=30,
            usage_counts=Counter(),
            usage_minutes=Counter(),
            previous_playlist_ids=set(),
            previous_signatures=[],
        )
        long_weight = schedule_generation._candidate_selection_weight(
            candidate=candidate,
            station_slug="neuralforge",
            duration_minutes=90,
            usage_counts=Counter(),
            usage_minutes=Counter(),
            previous_playlist_ids=set(),
            previous_signatures=[],
        )

        self.assertGreater(long_weight, short_weight * 2)

    def test_neuralcast_niche_playlist_weight_is_downweighted(self) -> None:
        mainstream = StationPlaylist(
            id="31",
            name="Classic Rock",
            is_enabled=True,
            weight=1.0,
            schedule_items=[],
            raw={},
        )
        niche = StationPlaylist(
            id="32",
            name="Tango",
            is_enabled=True,
            weight=1.0,
            schedule_items=[],
            raw={},
        )
        label_map = schedule_generation._station_label_map("neuralcast")
        mainstream_candidate = schedule_generation._solo_candidate(
            mainstream,
            "neuralcast",
            label_map,
        )
        niche_candidate = schedule_generation._solo_candidate(
            niche,
            "neuralcast",
            label_map,
        )

        mainstream_weight = schedule_generation._candidate_selection_weight(
            candidate=mainstream_candidate,
            station_slug="neuralcast",
            duration_minutes=60,
            usage_counts=Counter(),
            usage_minutes=Counter(),
            previous_playlist_ids=set(),
            previous_signatures=[],
        )
        niche_weight = schedule_generation._candidate_selection_weight(
            candidate=niche_candidate,
            station_slug="neuralcast",
            duration_minutes=60,
            usage_counts=Counter(),
            usage_minutes=Counter(),
            previous_playlist_ids=set(),
            previous_signatures=[],
        )

        self.assertAlmostEqual(niche_weight, mainstream_weight * 0.35)

    def test_neuralcast_niche_weight_does_not_affect_neuralforge(self) -> None:
        tango = StationPlaylist(
            id="32",
            name="Tango",
            is_enabled=True,
            weight=1.0,
            schedule_items=[],
            raw={},
        )
        candidate = schedule_generation._solo_candidate(
            tango,
            "neuralforge",
            schedule_generation._station_label_map("neuralforge"),
        )

        weight = schedule_generation._candidate_selection_weight(
            candidate=candidate,
            station_slug="neuralforge",
            duration_minutes=60,
            usage_counts=Counter(),
            usage_minutes=Counter(),
            previous_playlist_ids=set(),
            previous_signatures=[],
        )

        self.assertEqual(weight, 1.0)


if __name__ == "__main__":
    unittest.main()
