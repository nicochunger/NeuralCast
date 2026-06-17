"""Boundary-style tests for the schedule-generator runtime."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any, Mapping, Sequence

import pytest

from neuralcast.pipelines.schedule_generator.models import (
    DailyTemplateBlock,
    StationPlaylist,
    WeeklySchedulePlan,
)
from neuralcast.pipelines.schedule_generator.runtime import (
    ScheduleGeneratorRuntime,
    ScheduleRunRequest,
    ScheduleStateStore,
    StationScheduleContext,
)
from tests.factories import station_playlist_factory


def daily_block_factory() -> DailyTemplateBlock:
    return DailyTemplateBlock(
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
    )


def weekly_plan_factory(
    *,
    plan_hash: str = "hash-new",
    station: str = "neuralforge",
    station_name: str = "NeuralForge",
    timezone: str = "Europe/Zurich",
    week_start: dt.date = dt.date(2026, 6, 15),
    open_ratio_min: float = 0.1,
    open_ratio_max: float = 0.2,
    min_block_minutes: int = 30,
) -> WeeklySchedulePlan:
    block = daily_block_factory()
    return WeeklySchedulePlan(
        station=station,
        station_name=station_name,
        timezone=timezone,
        week_start_local_date=week_start.isoformat(),
        week_end_local_date=(week_start + dt.timedelta(days=6)).isoformat(),
        generated_at_utc="2026-06-17T00:00:00Z",
        seed_mode="stable_week",
        seed_salt=None,
        resolved_seed=123,
        open_ratio_min=open_ratio_min,
        open_ratio_max=open_ratio_max,
        daily_template=[block],
        expanded_blocks=[],
        rationale=f"min block {min_block_minutes}",
        plan_hash=plan_hash,
    )


class FakeRemote:
    def __init__(self) -> None:
        self.playlists = [station_playlist_factory()]
        self.apply_calls: list[tuple[str, Sequence[StationPlaylist]]] = []

    def load_station_context(self, station_slug: str) -> StationScheduleContext:
        return StationScheduleContext(
            station_name="NeuralForge",
            timezone_name="Europe/Zurich",
            playlists=list(self.playlists),
        )

    def apply_weekly_schedule(
        self,
        *,
        station_slug: str,
        playlists: Sequence[StationPlaylist],
        daily_template: Sequence[DailyTemplateBlock],
    ) -> tuple[int, int]:
        self.apply_calls.append((station_slug, list(playlists)))
        return len(playlists), len(daily_template)


class MemoryStateStore(ScheduleStateStore):
    def __init__(
        self,
        *,
        path: Path,
        payload: Mapping[str, Any] | None = None,
    ) -> None:
        self.path = path
        self.payload = dict(payload) if payload else None
        self.saved_payloads: list[Mapping[str, Any]] = []

    def path_for(self, station_slug: str) -> Path:
        return self.path

    def load(self, station_slug: str) -> Mapping[str, Any] | None:
        return self.payload

    def save(self, station_slug: str, plan: WeeklySchedulePlan) -> None:
        payload = plan.to_dict()
        self.saved_payloads.append(payload)
        self.payload = payload


def request_factory(tmp_path: Path, **overrides: object) -> ScheduleRunRequest:
    values: dict[str, object] = {
        "station": "neuralforge",
        "base_url": "https://azuracast.test",
        "api_key": "api-key",
        "dry_run": False,
        "project_root": tmp_path,
        "allowed_apply_root": tmp_path.resolve(),
    }
    values.update(overrides)
    return ScheduleRunRequest(**values)


def test_runtime_dry_run_builds_plan_without_save_or_apply(tmp_path) -> None:
    remote = FakeRemote()
    state = MemoryStateStore(path=tmp_path / "state.json")
    runtime = ScheduleGeneratorRuntime(
        remote=remote,
        state_store=state,
        planner=lambda **kwargs: weekly_plan_factory(plan_hash="hash-dry"),
    )

    result = runtime.run(request_factory(tmp_path, dry_run=True))

    assert result.status == "dry_run"
    assert result.plan.plan_hash == "hash-dry"
    assert result.playlist_count == 1
    assert remote.apply_calls == []
    assert state.saved_payloads == []


def test_runtime_blocks_apply_outside_allowed_project_root(tmp_path) -> None:
    remote = FakeRemote()
    runtime = ScheduleGeneratorRuntime(
        remote=remote,
        state_store=MemoryStateStore(path=tmp_path / "state.json"),
        planner=lambda **kwargs: pytest.fail("planner should not run"),
    )

    with pytest.raises(RuntimeError, match="outside the VPS deployment root"):
        runtime.run(
            request_factory(
                tmp_path,
                project_root=tmp_path / "local",
                allowed_apply_root=tmp_path / "vps",
            )
        )

    assert remote.apply_calls == []


def test_runtime_skips_unchanged_plan_but_saves_state(tmp_path) -> None:
    remote = FakeRemote()
    state = MemoryStateStore(
        path=tmp_path / "state.json",
        payload={"plan_hash": "hash-same"},
    )
    runtime = ScheduleGeneratorRuntime(
        remote=remote,
        state_store=state,
        planner=lambda **kwargs: weekly_plan_factory(plan_hash="hash-same"),
    )

    result = runtime.run(request_factory(tmp_path))

    assert result.status == "skipped_unchanged"
    assert result.previous_hash == "hash-same"
    assert remote.apply_calls == []
    assert state.saved_payloads[-1]["plan_hash"] == "hash-same"


def test_runtime_applies_changed_plan_then_saves_state(tmp_path) -> None:
    remote = FakeRemote()
    state = MemoryStateStore(
        path=tmp_path / "state.json",
        payload={"plan_hash": "hash-old"},
    )
    runtime = ScheduleGeneratorRuntime(
        remote=remote,
        state_store=state,
        planner=lambda **kwargs: weekly_plan_factory(plan_hash="hash-new"),
    )

    result = runtime.run(request_factory(tmp_path))

    assert result.status == "applied"
    assert result.updated_playlists == 1
    assert result.updated_items == 1
    assert remote.apply_calls == [("neuralforge", remote.playlists)]
    assert state.saved_payloads[-1]["plan_hash"] == "hash-new"


def test_runtime_force_apply_overrides_unchanged_hash(tmp_path) -> None:
    remote = FakeRemote()
    state = MemoryStateStore(
        path=tmp_path / "state.json",
        payload={"plan_hash": "hash-same"},
    )
    runtime = ScheduleGeneratorRuntime(
        remote=remote,
        state_store=state,
        planner=lambda **kwargs: weekly_plan_factory(plan_hash="hash-same"),
    )

    result = runtime.run(request_factory(tmp_path, force_apply=True))

    assert result.status == "applied"
    assert remote.apply_calls


def test_runtime_resolves_week_and_station_defaults_for_planner(tmp_path) -> None:
    seen: dict[str, object] = {}

    def planner(**kwargs) -> WeeklySchedulePlan:
        seen.update(kwargs)
        return weekly_plan_factory(
            station="neuralcast",
            station_name=str(kwargs["station_name"]),
            timezone=str(kwargs["timezone_name"]),
            week_start=kwargs["week_start"],
            open_ratio_min=kwargs["open_ratio_min"],
            open_ratio_max=kwargs["open_ratio_max"],
            min_block_minutes=kwargs["min_block_minutes"],
        )

    runtime = ScheduleGeneratorRuntime(
        remote=FakeRemote(),
        state_store=MemoryStateStore(path=tmp_path / "state.json"),
        planner=planner,
        now=lambda timezone: dt.datetime(2026, 6, 17, 12, 0, tzinfo=timezone),
    )

    result = runtime.run(
        request_factory(
            tmp_path,
            station="neuralcast",
            dry_run=True,
        )
    )

    assert seen["week_start"] == dt.date(2026, 6, 15)
    assert seen["open_ratio_min"] == pytest.approx(0.30)
    assert seen["open_ratio_max"] == pytest.approx(0.45)
    assert seen["min_block_minutes"] == 30
    assert seen["max_block_minutes"] == 75
    assert result.plan.week_start_local_date == "2026-06-15"
