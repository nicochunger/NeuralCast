"""FastAPI application for the authenticated NeuralCast admin service."""

from __future__ import annotations

import datetime as dt
import hmac
import os
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from neuralcast.pipelines.host_orchestrator.models import Archetype, supports_track_focus

from .jobs import (
    DEFAULT_ADMIN_SCHEDULE_SEED_MODE,
    JOB_OPERATION_SCHEDULE_GENERATOR,
    JobConflictError,
    JobManager,
    JobNotFoundError,
    SUPPORTED_ARCHETYPES,
    SUPPORTED_SCHEDULE_SEED_MODES,
    SUPPORTED_STATIONS,
    SUPPORTED_TRACK_FOCUSES,
)
from .stations import AdminStationService, StationServiceConfigError

BEARER_PREFIX = "Bearer "


class OptionsResponse(BaseModel):
    """Response model for `/admin/options`."""

    stations: list[str]
    archetypes: list[str]


class OperationCapabilityResponse(BaseModel):
    """Response model for one supported admin operation."""

    dry_run_supported: bool
    track_focus_supported: bool
    force_apply_supported: bool = False
    week_start_date_supported: bool = False
    supported_seed_modes: list[str] = Field(default_factory=list)
    default_seed_mode: str | None = None
    supported_tuning_fields: list[str] = Field(default_factory=list)


class CapabilitiesResponse(BaseModel):
    """Response model for `/admin/capabilities`."""

    stations: list[str]
    archetypes: list[str]
    track_focus_values: list[str]
    track_focus_archetypes: list[str]
    operations: dict[str, OperationCapabilityResponse]


class ForceArchetypeRequest(BaseModel):
    """Validated request body for `POST /admin/force-archetype`."""

    model_config = ConfigDict(extra="forbid")

    station: str
    archetype: str
    track_focus: str | None = None
    dry_run: bool = False

    @field_validator("station")
    @classmethod
    def validate_station(cls, value: str) -> str:
        if value not in SUPPORTED_STATIONS:
            raise ValueError(
                f"Unsupported station '{value}'. Allowed values: {SUPPORTED_STATIONS}."
            )
        return value

    @field_validator("archetype")
    @classmethod
    def validate_archetype(cls, value: str) -> str:
        if value not in SUPPORTED_ARCHETYPES:
            raise ValueError(
                "Unsupported archetype "
                f"'{value}'. Allowed values: {SUPPORTED_ARCHETYPES}."
            )
        return value

    @field_validator("track_focus")
    @classmethod
    def validate_track_focus(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if value not in SUPPORTED_TRACK_FOCUSES:
            raise ValueError(
                "Unsupported track_focus "
                f"'{value}'. Allowed values: {SUPPORTED_TRACK_FOCUSES}."
            )
        return value

    @model_validator(mode="after")
    def validate_track_focus_for_archetype(self) -> "ForceArchetypeRequest":
        if self.track_focus and not supports_track_focus(Archetype(self.archetype)):
            raise ValueError(
                "track_focus is only supported for short_story, album_spotlight, "
                "era_snapshot, and deep_dive."
            )
        return self


class ScheduleGeneratorRequest(BaseModel):
    """Validated request body for `POST /admin/run-schedule-generator`."""

    model_config = ConfigDict(extra="forbid")

    station: str
    dry_run: bool = False
    force_apply: bool = False
    week_start_date: str | None = None
    seed_mode: str = DEFAULT_ADMIN_SCHEDULE_SEED_MODE
    seed_salt: str | None = None
    open_ratio_min: float | None = None
    open_ratio_max: float | None = None
    min_open_slots: int | None = None
    max_open_slots: int | None = None
    min_block_minutes: int | None = None
    max_block_minutes: int | None = None

    @field_validator("station")
    @classmethod
    def validate_station(cls, value: str) -> str:
        if value not in SUPPORTED_STATIONS:
            raise ValueError(
                f"Unsupported station '{value}'. Allowed values: {SUPPORTED_STATIONS}."
            )
        return value

    @field_validator("week_start_date")
    @classmethod
    def validate_week_start_date(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            return None
        dt.date.fromisoformat(normalized)
        return normalized

    @field_validator("seed_mode")
    @classmethod
    def validate_seed_mode(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in SUPPORTED_SCHEDULE_SEED_MODES:
            raise ValueError(
                "Unsupported seed_mode "
                f"'{value}'. Allowed values: {SUPPORTED_SCHEDULE_SEED_MODES}."
            )
        return normalized

    @field_validator("seed_salt")
    @classmethod
    def validate_seed_salt(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @model_validator(mode="after")
    def validate_scheduler_controls(self) -> "ScheduleGeneratorRequest":
        if self.seed_mode == "stable_week" and self.seed_salt is not None:
            raise ValueError(
                "seed_salt is only supported when seed_mode is 'fresh' or 'custom'."
            )
        if self.seed_mode == "custom" and self.seed_salt is None:
            raise ValueError("seed_salt is required when seed_mode is 'custom'.")

        if self.open_ratio_min is not None or self.open_ratio_max is not None:
            min_ratio = self.open_ratio_min if self.open_ratio_min is not None else 0.0
            max_ratio = self.open_ratio_max if self.open_ratio_max is not None else 1.0
            if not (0.0 <= min_ratio <= max_ratio <= 1.0):
                raise ValueError(
                    "Open-slot ratio bounds must satisfy 0 <= min <= max <= 1."
                )

        if self.min_open_slots is not None or self.max_open_slots is not None:
            min_slots = self.min_open_slots if self.min_open_slots is not None else 0
            max_slots = (
                self.max_open_slots
                if self.max_open_slots is not None
                else min_slots
            )
            if min_slots < 0 or max_slots < 0:
                raise ValueError("Open-slot count bounds must be non-negative.")
            if min_slots > max_slots:
                raise ValueError("min_open_slots cannot exceed max_open_slots.")

        if (
            self.min_block_minutes is not None
            and self.max_block_minutes is not None
            and self.min_block_minutes > self.max_block_minutes
        ):
            raise ValueError("min_block_minutes cannot exceed max_block_minutes.")

        return self


class AcceptedJobResponse(BaseModel):
    """Immediate response after a new admin job is accepted."""

    job_id: str
    status: str = Field(default="accepted")


class JobStatusResponse(BaseModel):
    """External response model for persisted admin job status."""

    job_id: str
    operation: str
    station: str
    archetype: str | None
    track_focus: str | None
    dry_run: bool
    schedule_options: dict[str, Any] | None
    status: str
    accepted_at: str
    started_at: str | None
    finished_at: str | None
    exit_code: int | None
    log_path: str
    log_tail: str


class TrackResponse(BaseModel):
    """Serialized queue-track payload used by station read endpoints."""

    queue_id: str
    song_id: str | None
    artist: str
    title: str
    duration_seconds: int | None


class NowPlayingResponse(BaseModel):
    """Response model for `/admin/stations/{station}/now-playing`."""

    station: str
    current_track: TrackResponse
    remaining_seconds: int | None
    listener_count: int | None


class QueueResponse(BaseModel):
    """Response model for `/admin/stations/{station}/queue`."""

    station: str
    items: list[TrackResponse]
    next_track: TrackResponse | None


def require_admin_token(
    authorization: str | None = Header(default=None),
) -> None:
    """Authenticate an admin request with a bearer token from the environment."""

    expected_token = os.getenv("NEURALCAST_ADMIN_HTTP_TOKEN")
    if not expected_token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="NEURALCAST_ADMIN_HTTP_TOKEN is not configured.",
        )

    if not authorization or not authorization.startswith(BEARER_PREFIX):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    provided_token = authorization[len(BEARER_PREFIX) :].strip()
    if not provided_token or not hmac.compare_digest(provided_token, expected_token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid bearer token.",
            headers={"WWW-Authenticate": "Bearer"},
        )


def get_job_manager(request: Request) -> JobManager:
    """Read the shared job manager from the FastAPI app state."""

    return request.app.state.job_manager


def get_station_service(request: Request) -> AdminStationService:
    """Read the shared station service from the FastAPI app state."""

    return request.app.state.station_service


def create_app(
    job_manager: JobManager | None = None,
    station_service: AdminStationService | None = None,
) -> FastAPI:
    """Create the FastAPI application for the admin HTTP service."""

    app = FastAPI(title="NeuralCast Admin API", version="0.2.0")
    app.state.job_manager = job_manager or JobManager()
    app.state.station_service = station_service or AdminStationService()

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get(
        "/admin/options",
        response_model=OptionsResponse,
        dependencies=[Depends(require_admin_token)],
    )
    def admin_options(
        manager: JobManager = Depends(get_job_manager),
    ) -> OptionsResponse:
        return OptionsResponse(**manager.options())

    @app.get(
        "/admin/capabilities",
        response_model=CapabilitiesResponse,
        dependencies=[Depends(require_admin_token)],
    )
    def admin_capabilities(
        manager: JobManager = Depends(get_job_manager),
    ) -> CapabilitiesResponse:
        return CapabilitiesResponse(**manager.capabilities())

    @app.get(
        "/admin/stations/{station}/now-playing",
        response_model=NowPlayingResponse,
        dependencies=[Depends(require_admin_token)],
    )
    def station_now_playing(
        station: str,
        service: AdminStationService = Depends(get_station_service),
    ) -> NowPlayingResponse:
        try:
            payload = service.now_playing(station)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=str(exc),
            ) from exc
        except StationServiceConfigError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=str(exc),
            ) from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Failed to fetch now-playing state: {exc}",
            ) from exc

        return NowPlayingResponse(**payload)

    @app.get(
        "/admin/stations/{station}/queue",
        response_model=QueueResponse,
        dependencies=[Depends(require_admin_token)],
    )
    def station_queue(
        station: str,
        limit: int = Query(default=4, ge=1, le=10),
        service: AdminStationService = Depends(get_station_service),
    ) -> QueueResponse:
        try:
            payload = service.queue(station, limit=limit)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=str(exc),
            ) from exc
        except StationServiceConfigError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=str(exc),
            ) from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Failed to fetch queue state: {exc}",
            ) from exc

        return QueueResponse(**payload)

    @app.post(
        "/admin/force-archetype",
        response_model=AcceptedJobResponse,
        status_code=status.HTTP_202_ACCEPTED,
        dependencies=[Depends(require_admin_token)],
    )
    def force_archetype(
        payload: ForceArchetypeRequest,
        manager: JobManager = Depends(get_job_manager),
    ) -> AcceptedJobResponse:
        try:
            job = manager.enqueue_force_archetype(
                station=payload.station,
                archetype=payload.archetype,
                track_focus=payload.track_focus,
                dry_run=payload.dry_run,
            )
        except JobConflictError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "message": str(exc),
                    "job_id": exc.job_id,
                    "station": exc.station,
                },
            ) from exc

        return AcceptedJobResponse(job_id=job.job_id)

    @app.post(
        "/admin/run-schedule-generator",
        response_model=AcceptedJobResponse,
        status_code=status.HTTP_202_ACCEPTED,
        dependencies=[Depends(require_admin_token)],
    )
    def run_schedule_generator(
        payload: ScheduleGeneratorRequest,
        manager: JobManager = Depends(get_job_manager),
    ) -> AcceptedJobResponse:
        try:
            job = manager.enqueue_schedule_generator(
                station=payload.station,
                dry_run=payload.dry_run,
                force_apply=payload.force_apply,
                week_start_date=payload.week_start_date,
                seed_mode=payload.seed_mode,
                seed_salt=payload.seed_salt,
                open_ratio_min=payload.open_ratio_min,
                open_ratio_max=payload.open_ratio_max,
                min_open_slots=payload.min_open_slots,
                max_open_slots=payload.max_open_slots,
                min_block_minutes=payload.min_block_minutes,
                max_block_minutes=payload.max_block_minutes,
            )
        except JobConflictError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "message": str(exc),
                    "job_id": exc.job_id,
                    "station": exc.station,
                    "operation": JOB_OPERATION_SCHEDULE_GENERATOR,
                },
            ) from exc

        return AcceptedJobResponse(job_id=job.job_id)

    @app.get(
        "/admin/jobs/{job_id}",
        response_model=JobStatusResponse,
        dependencies=[Depends(require_admin_token)],
    )
    def get_job(
        job_id: str,
        manager: JobManager = Depends(get_job_manager),
    ) -> JobStatusResponse:
        try:
            payload: dict[str, Any] = manager.job_status_payload(job_id)
        except JobNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(exc),
            ) from exc
        return JobStatusResponse(**payload)

    return app


app = create_app()
