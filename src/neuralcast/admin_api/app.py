"""FastAPI application for the authenticated NeuralCast admin service."""

from __future__ import annotations

import hmac
import os
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .jobs import (
    JobConflictError,
    JobManager,
    JobNotFoundError,
    SUPPORTED_ARCHETYPES,
    SUPPORTED_STATIONS,
)

BEARER_PREFIX = "Bearer "


class OptionsResponse(BaseModel):
    """Response model for `/admin/options`."""

    stations: list[str]
    archetypes: list[str]


class ForceArchetypeRequest(BaseModel):
    """Validated request body for `POST /admin/force-archetype`."""

    model_config = ConfigDict(extra="forbid")

    station: str
    archetype: str
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


class AcceptedJobResponse(BaseModel):
    """Immediate response after a new admin job is accepted."""

    job_id: str
    status: str = Field(default="accepted")


class JobStatusResponse(BaseModel):
    """External response model for persisted admin job status."""

    job_id: str
    station: str
    archetype: str
    dry_run: bool
    status: str
    accepted_at: str
    started_at: str | None
    finished_at: str | None
    exit_code: int | None
    log_path: str
    log_tail: str


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


def create_app(job_manager: JobManager | None = None) -> FastAPI:
    """Create the FastAPI application for the admin HTTP service."""

    app = FastAPI(title="NeuralCast Admin API", version="0.1.0")
    app.state.job_manager = job_manager or JobManager()

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
