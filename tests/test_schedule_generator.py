#!/usr/bin/env python3
"""Unit tests for weekly schedule generator helpers."""

from __future__ import annotations

import datetime as dt
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from neuralcast.pipelines.schedule_generator import (  # noqa: E402
    StationPlaylist,
    build_schedule_items_by_playlist,
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
                "end_time_local": "08:00",
                "mode": "playlist",
                "playlist_id": "10",
                "playlist_name": "Prog Metal",
                "section_label": "Prog Night",
                "genre_labels": ["prog", "metal"],
            },
            {
                "start_time_local": "08:00",
                "end_time_local": "14:00",
                "mode": "open",
                "section_label": "Open Rotation",
                "genre_labels": ["mixed"],
            },
            {
                "start_time_local": "14:00",
                "end_time_local": "24:00",
                "mode": "playlist",
                "playlist_id": "11",
                "playlist_name": "Symphonic Metal",
                "section_label": "Symphonic Day",
                "genre_labels": ["symphonic"],
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
        self.assertEqual(len(blocks), 3)
        self.assertEqual(blocks[1].mode, "open")

    def test_validate_daily_template_rejects_gap(self) -> None:
        raw_blocks = [
            {
                "start_time_local": "00:00",
                "end_time_local": "08:00",
                "mode": "playlist",
                "playlist_id": "10",
                "section_label": "Prog Night",
                "genre_labels": ["prog"],
            },
            {
                "start_time_local": "09:00",
                "end_time_local": "24:00",
                "mode": "playlist",
                "playlist_id": "11",
                "section_label": "Symphonic Day",
                "genre_labels": ["symphonic"],
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
                "end_time_local": "12:00",
                "mode": "open",
                "section_label": "Open Rotation",
                "genre_labels": ["mixed"],
            },
            {
                "start_time_local": "12:00",
                "end_time_local": "24:00",
                "mode": "playlist",
                "playlist_id": "11",
                "section_label": "Symphonic Day",
                "genre_labels": ["symphonic"],
            },
        ]

        with self.assertRaises(ValueError):
            validate_daily_template(
                raw_blocks=raw_blocks,
                playlist_by_id=self.playlist_map,
                open_ratio_min=0.20,
                open_ratio_max=0.40,
                min_block_minutes=30,
                max_block_minutes=720,
            )

    def test_expand_daily_template_to_week_repeats_daily_layout(self) -> None:
        raw_blocks = [
            {
                "start_time_local": "00:00",
                "end_time_local": "12:00",
                "mode": "playlist",
                "playlist_id": "10",
                "section_label": "Morning",
                "genre_labels": ["prog"],
            },
            {
                "start_time_local": "12:00",
                "end_time_local": "24:00",
                "mode": "playlist",
                "playlist_id": "11",
                "section_label": "Afternoon",
                "genre_labels": ["symphonic"],
            },
        ]

        daily = validate_daily_template(
            raw_blocks=raw_blocks,
            playlist_by_id=self.playlist_map,
            open_ratio_min=0.0,
            open_ratio_max=0.0,
            min_block_minutes=30,
            max_block_minutes=720,
        )
        expanded = expand_daily_template_to_week(daily, week_start=dt.date(2026, 2, 16))
        self.assertEqual(len(expanded), 14)
        self.assertEqual(expanded[0].date_local, "2026-02-16")
        self.assertEqual(expanded[-1].date_local, "2026-02-22")

    def test_build_schedule_items_by_playlist_skips_open_blocks(self) -> None:
        raw_blocks = [
            {
                "start_time_local": "00:00",
                "end_time_local": "08:00",
                "mode": "playlist",
                "playlist_id": "10",
                "section_label": "Night",
                "genre_labels": ["prog"],
            },
            {
                "start_time_local": "08:00",
                "end_time_local": "12:00",
                "mode": "open",
                "section_label": "Open",
                "genre_labels": ["mixed"],
            },
            {
                "start_time_local": "12:00",
                "end_time_local": "24:00",
                "mode": "playlist",
                "playlist_id": "11",
                "section_label": "Day",
                "genre_labels": ["symphonic"],
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
        self.assertEqual(len(items["10"]), 1)
        self.assertEqual(len(items["11"]), 1)
        self.assertEqual(items["11"][0]["end_time"], "23:59")

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


if __name__ == "__main__":
    unittest.main()
