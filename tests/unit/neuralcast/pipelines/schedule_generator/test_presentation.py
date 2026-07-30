"""Tests for schedule presentation metadata."""

from __future__ import annotations

from neuralcast.pipelines.schedule_generator.models import DailyTemplateBlock, WeeklySchedulePlan
from neuralcast.pipelines.schedule_generator.presentation import build_schedule_presentation


def _plan() -> WeeklySchedulePlan:
    return WeeklySchedulePlan(
        station="neuralforge",
        station_name="NeuralForge",
        timezone="Europe/Zurich",
        week_start_local_date="2026-07-27",
        week_end_local_date="2026-08-02",
        generated_at_utc="2026-07-27T00:00:00+00:00",
        seed_mode="stable_week",
        seed_salt=None,
        resolved_seed=1,
        open_ratio_min=0.2,
        open_ratio_max=0.4,
        daily_template=[
            DailyTemplateBlock("06:00", "07:00", 360, 420, "playlist", "", ["Power Metal"], ["25"], ["Power Metal"], "25", "Power Metal"),
            DailyTemplateBlock("07:00", "08:00", 420, 480, "playlist", "", ["Folk Rock", "Folk Metal"], ["37", "28"], ["Folk Rock", "Folk Metal"], "37", "Folk Rock"),
        ],
        expanded_blocks=[],
        rationale="test",
        plan_hash="plan-hash",
    )


def test_build_schedule_presentation_has_one_block_per_playlist_set(monkeypatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    presentation = build_schedule_presentation(_plan())

    assert presentation["plan_hash"] == "plan-hash"
    assert len(presentation["blocks"]) == 2
    single = next(block for block in presentation["blocks"] if block["kind"] == "single")
    combo = next(block for block in presentation["blocks"] if block["kind"] == "combo")
    assert "title" not in single["translations"]["en"]
    assert combo["translations"]["en"]["title"] == "Folk Metal Mix"
    assert 5 <= len(single["translations"]["en"]["description"].split()) <= 7
