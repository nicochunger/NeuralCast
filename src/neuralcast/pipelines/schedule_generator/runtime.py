"""Runtime boundary for weekly schedule generation and application."""

from __future__ import annotations

import datetime as dt
import pathlib
from dataclasses import dataclass
from typing import Any, Callable, Literal, Mapping, Protocol, Sequence
from zoneinfo import ZoneInfo

from neuralcast.config import PROJECT_ROOT

from .client import (
    AzuraCastClient,
    apply_weekly_schedule,
    choose_station_payload,
    derive_station_name,
    derive_station_timezone,
    extract_station_playlists,
)
from .config import (
    DEFAULT_MAX_BLOCK_MINUTES,
    DEFAULT_MIN_BLOCK_MINUTES,
    DEFAULT_MAX_OPEN_SLOTS,
    DEFAULT_MIN_OPEN_SLOTS,
    DEFAULT_OPEN_RATIO_MAX,
    DEFAULT_OPEN_RATIO_MIN,
    NEURALCAST_DEFAULT_MAX_BLOCK_MINUTES,
    NEURALCAST_DEFAULT_MIN_BLOCK_MINUTES,
    NEURALCAST_DEFAULT_OPEN_RATIO_MAX,
    NEURALCAST_DEFAULT_OPEN_RATIO_MIN,
)
from .generation import DEFAULT_SCHEDULE_SEED_MODE, build_weekly_plan_with_code
from .models import DailyTemplateBlock, StationPlaylist, WeeklySchedulePlan
from .state import (
    load_schedule_state,
    run_with_retries,
    save_schedule_state_atomic,
    schedule_state_path,
)
from .template import compute_week_start

VPS_SCHEDULER_PROJECT_ROOT = "/root/radio_host_orchestrator"
ScheduleRunStatus = Literal["dry_run", "skipped_unchanged", "applied"]
PlanBuilder = Callable[..., WeeklySchedulePlan]


@dataclass(frozen=True)
class ScheduleRunRequest:
    station: str
    base_url: str
    api_key: str
    dry_run: bool = False
    force_apply: bool = False
    verify_tls: bool = False
    week_start_date: dt.date | None = None
    seed_mode: str = DEFAULT_SCHEDULE_SEED_MODE
    seed_salt: str | None = None
    open_ratio_min: float = DEFAULT_OPEN_RATIO_MIN
    open_ratio_max: float = DEFAULT_OPEN_RATIO_MAX
    min_open_slots: int = DEFAULT_MIN_OPEN_SLOTS
    max_open_slots: int = DEFAULT_MAX_OPEN_SLOTS
    min_block_minutes: int | None = None
    max_block_minutes: int | None = None
    project_root: pathlib.Path = PROJECT_ROOT
    allowed_apply_root: pathlib.Path = pathlib.Path(VPS_SCHEDULER_PROJECT_ROOT)


@dataclass(frozen=True)
class ScheduleRunResult:
    status: ScheduleRunStatus
    plan: WeeklySchedulePlan
    state_path: pathlib.Path
    previous_hash: str | None
    playlist_count: int
    updated_playlists: int = 0
    updated_items: int = 0


@dataclass(frozen=True)
class StationScheduleContext:
    station_name: str
    timezone_name: str
    playlists: list[StationPlaylist]


class ScheduleRemote(Protocol):
    def load_station_context(self, station_slug: str) -> StationScheduleContext:
        ...

    def apply_weekly_schedule(
        self,
        *,
        station_slug: str,
        playlists: Sequence[StationPlaylist],
        daily_template: Sequence[DailyTemplateBlock],
    ) -> tuple[int, int]:
        ...


class ScheduleStateStore(Protocol):
    def path_for(self, station_slug: str) -> pathlib.Path:
        ...

    def load(self, station_slug: str) -> Mapping[str, Any] | None:
        ...

    def save(self, station_slug: str, plan: WeeklySchedulePlan) -> None:
        ...


class AzuraCastScheduleRemote:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        verify_tls: bool = False,
        client_factory: Callable[[str, str, bool], AzuraCastClient] = AzuraCastClient,
    ) -> None:
        self._client = client_factory(base_url.rstrip("/"), api_key, verify_tls)

    def load_station_context(self, station_slug: str) -> StationScheduleContext:
        stations = run_with_retries("Fetch stations", self._client.get_stations)
        station_payload = choose_station_payload(stations, station_slug)
        station_name = derive_station_name(station_payload, station_slug)
        timezone_name = derive_station_timezone(station_payload)

        raw_playlists = run_with_retries(
            "Fetch station playlists",
            lambda: self._client.get_station_playlists(station_slug),
        )
        playlists = extract_station_playlists(raw_playlists)
        if not playlists:
            raise RuntimeError(
                f"No playlists returned by AzuraCast for station '{station_slug}'."
            )

        return StationScheduleContext(
            station_name=station_name,
            timezone_name=timezone_name,
            playlists=playlists,
        )

    def apply_weekly_schedule(
        self,
        *,
        station_slug: str,
        playlists: Sequence[StationPlaylist],
        daily_template: Sequence[DailyTemplateBlock],
    ) -> tuple[int, int]:
        return apply_weekly_schedule(
            client=self._client,
            station_slug=station_slug,
            playlists=playlists,
            daily_template=daily_template,
        )


class FileScheduleStateStore:
    def path_for(self, station_slug: str) -> pathlib.Path:
        return schedule_state_path(station_slug)

    def load(self, station_slug: str) -> Mapping[str, Any] | None:
        return load_schedule_state(self.path_for(station_slug))

    def save(self, station_slug: str, plan: WeeklySchedulePlan) -> None:
        save_schedule_state_atomic(self.path_for(station_slug), plan.to_dict())


def resolve_block_duration_bounds(request: ScheduleRunRequest) -> tuple[int, int]:
    min_block_minutes = (
        DEFAULT_MIN_BLOCK_MINUTES
        if request.min_block_minutes is None
        else int(request.min_block_minutes)
    )
    max_block_minutes = (
        DEFAULT_MAX_BLOCK_MINUTES
        if request.max_block_minutes is None
        else int(request.max_block_minutes)
    )
    if request.station.strip().lower() == "neuralcast":
        if request.min_block_minutes is None:
            min_block_minutes = NEURALCAST_DEFAULT_MIN_BLOCK_MINUTES
        if request.max_block_minutes is None:
            max_block_minutes = NEURALCAST_DEFAULT_MAX_BLOCK_MINUTES
    return min_block_minutes, max_block_minutes


def resolve_open_ratio_bounds(request: ScheduleRunRequest) -> tuple[float, float]:
    open_ratio_min = float(request.open_ratio_min)
    open_ratio_max = float(request.open_ratio_max)
    if (
        request.station.strip().lower() == "neuralcast"
        and open_ratio_min == DEFAULT_OPEN_RATIO_MIN
        and open_ratio_max == DEFAULT_OPEN_RATIO_MAX
    ):
        open_ratio_min = NEURALCAST_DEFAULT_OPEN_RATIO_MIN
        open_ratio_max = NEURALCAST_DEFAULT_OPEN_RATIO_MAX
    return open_ratio_min, open_ratio_max


class ScheduleGeneratorRuntime:
    def __init__(
        self,
        *,
        remote: ScheduleRemote,
        state_store: ScheduleStateStore | None = None,
        planner: PlanBuilder = build_weekly_plan_with_code,
        now: Callable[[ZoneInfo], dt.datetime] | None = None,
    ) -> None:
        self._remote = remote
        self._state_store = state_store or FileScheduleStateStore()
        self._planner = planner
        self._now = now or (lambda timezone: dt.datetime.now(timezone))

    def run(self, request: ScheduleRunRequest) -> ScheduleRunResult:
        self._validate_request(request)

        if not request.dry_run:
            current_project_root = str(request.project_root.resolve())
            allowed_root = str(request.allowed_apply_root)
            if current_project_root != allowed_root:
                raise RuntimeError(
                    "Refusing non-dry-run schedule generation outside the VPS deployment root. "
                    f"Current PROJECT_ROOT={current_project_root!r}; expected "
                    f"{allowed_root!r}. Use --dry-run locally, or run on the VPS."
                )

        context = self._remote.load_station_context(request.station)
        station_tz = ZoneInfo(context.timezone_name)
        now_local = self._now(station_tz)
        week_start = request.week_start_date or compute_week_start(now_local.date())
        week_end = week_start + dt.timedelta(days=6)

        open_ratio_min, open_ratio_max = resolve_open_ratio_bounds(request)
        min_block_minutes, max_block_minutes = resolve_block_duration_bounds(request)
        plan = self._planner(
            station_slug=request.station,
            station_name=context.station_name,
            timezone_name=context.timezone_name,
            week_start=week_start,
            week_end=week_end,
            playlists=context.playlists,
            open_ratio_min=open_ratio_min,
            open_ratio_max=open_ratio_max,
            min_open_slots=int(request.min_open_slots),
            max_open_slots=int(request.max_open_slots),
            min_block_minutes=min_block_minutes,
            max_block_minutes=max_block_minutes,
            seed_mode=request.seed_mode,
            seed_salt=request.seed_salt,
        )

        state_path = self._state_store.path_for(request.station)
        existing_state = self._state_store.load(request.station)
        previous_hash = (
            str(existing_state.get("plan_hash"))
            if isinstance(existing_state, Mapping)
            else None
        )

        if request.dry_run:
            return ScheduleRunResult(
                status="dry_run",
                plan=plan,
                state_path=state_path,
                previous_hash=previous_hash,
                playlist_count=len(context.playlists),
            )

        if previous_hash and previous_hash == plan.plan_hash and not request.force_apply:
            self._state_store.save(request.station, plan)
            return ScheduleRunResult(
                status="skipped_unchanged",
                plan=plan,
                state_path=state_path,
                previous_hash=previous_hash,
                playlist_count=len(context.playlists),
            )

        updated_playlists, updated_items = self._remote.apply_weekly_schedule(
            station_slug=request.station,
            playlists=context.playlists,
            daily_template=plan.daily_template,
        )
        self._state_store.save(request.station, plan)
        return ScheduleRunResult(
            status="applied",
            plan=plan,
            state_path=state_path,
            previous_hash=previous_hash,
            playlist_count=len(context.playlists),
            updated_playlists=updated_playlists,
            updated_items=updated_items,
        )

    @staticmethod
    def _validate_request(request: ScheduleRunRequest) -> None:
        open_ratio_min, open_ratio_max = resolve_open_ratio_bounds(request)
        if not (0.0 <= open_ratio_min <= open_ratio_max <= 1.0):
            raise ValueError("Open-slot ratio bounds must satisfy 0 <= min <= max <= 1.")

        min_open_slots = int(request.min_open_slots)
        max_open_slots = int(request.max_open_slots)
        if min_open_slots < 0 or max_open_slots < 0:
            raise ValueError("Open-slot count bounds must be non-negative.")
        if min_open_slots > max_open_slots:
            raise ValueError("min-open-slots cannot exceed max-open-slots.")

        min_block_minutes, max_block_minutes = resolve_block_duration_bounds(request)
        if min_block_minutes > max_block_minutes:
            raise ValueError("min-block-minutes cannot exceed max-block-minutes.")
        if request.seed_mode == "stable_week" and request.seed_salt:
            raise ValueError(
                "seed_salt is only supported when --seed-mode is 'fresh' or 'custom'."
            )
        if request.seed_mode == "custom" and not request.seed_salt:
            raise ValueError("seed_salt is required when --seed-mode custom is used.")
