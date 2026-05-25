"""Unit tests for schedule template parsing helpers."""

from __future__ import annotations

import pytest

from neuralcast.pipelines.schedule_generator import template
from neuralcast.pipelines.schedule_generator.models import ScheduleValidationError


def test_parse_and_format_hhmm_round_trip_day_end() -> None:
    assert template.parse_hhmm("07:30") == 450
    assert template.parse_hhmm("24:00", allow_24=True) == 1440
    assert template.format_hhmm(1440) == "24:00"


def test_parse_hhmm_rejects_invalid_values() -> None:
    with pytest.raises(ScheduleValidationError):
        template.parse_hhmm("25:00")


def test_normalize_mode_accepts_aliases() -> None:
    assert template.normalize_mode("assigned") == "playlist"
    assert template.normalize_mode("weighted") == "open"


def test_normalize_genre_labels_accepts_list_and_csv_text() -> None:
    assert template.normalize_genre_labels([" prog ", "", "metal"]) == ["prog", "metal"]
    assert template.normalize_genre_labels("prog, metal") == ["prog", "metal"]
