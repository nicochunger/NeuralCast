"""Authenticated episode planning, review, approvals and private previews."""

from typing import Annotated, Callable, Literal
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse
from pydantic import Field
from neuralcast.pipelines.episode_producer.models import Document, Identifier
from neuralcast.pipelines.episode_producer.workspace import Workspace, ConflictError
from neuralcast.pipelines.episode_producer.workspace_models import Feedback
from .jobs import JobConflictError
from .episode_activity import episode_activity


class Action(Document):
    action: Literal["ideas", "develop", "queue", "write", "revise", "render", "publish"]
    episode_id: Identifier | None = None
    revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    topic: str = Field(default="", max_length=5000)
    target_minutes: int = Field(default=45, ge=10, le=120, strict=True)
    render_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    description: str = Field(default="", max_length=5000)


class Schedule(Document):
    revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    enabled: bool = Field(strict=True)
    target_minutes: int = Field(default=45, ge=10, le=120, strict=True)


class Retry(Document):
    request_id: Identifier


def episodes_router(auth: Callable) -> APIRouter:
    router = APIRouter(prefix="/admin/episodes", dependencies=[Depends(auth)])

    def workspace(request, station):
        try:
            return request.app.state.episodes_factory(station)
        except ValueError as exc:
            raise HTTPException(404, "Unknown station") from exc

    def error(exc):
        status = (
            409
            if isinstance(exc, (ConflictError, JobConflictError, RuntimeError))
            else 404 if isinstance(exc, FileNotFoundError) else 422
        )
        return HTTPException(status, str(exc))

    @router.get("/{station}")
    def view(
        request: Request,
        response: Response,
        station: str,
        episode_id: Identifier | None = None,
    ):
        response.headers["Cache-Control"] = "private, no-store"
        try:
            result = workspace(request, station).view(episode_id)
            result["job"] = request.app.state.job_manager.latest_episode_job(station)
            if result["job"]:
                from neuralcast.pipelines.episode_producer.storage import read_json

                request_id = (result["job"].get("episode_options") or {}).get(
                    "request_id"
                )
                if request_id:
                    path = (
                        workspace(request, station).root
                        / "requests"
                        / request_id
                        / "result.json"
                    )
                    if path.exists():
                        saved = read_json(path)
                        result["job"]["result"] = {
                            k: saved[k] for k in ("episode_id", "status") if k in saved
                        }
                    result["job"]["progress"] = episode_activity(
                        workspace(request, station), result["job"]
                    )
            return result
        except (ValueError, OSError, RuntimeError) as exc:
            raise error(exc) from exc

    @router.post("/{station}/actions", status_code=202)
    def action(request: Request, station: str, payload: Action):
        try:
            service = workspace(request, station)
            request_id = service.submit(**payload.model_dump())
            job = request.app.state.job_manager.enqueue_episode(
                station=station, request_id=request_id
            )
            return {"job_id": job.job_id, "request_id": request_id}
        except (ValueError, OSError, RuntimeError) as exc:
            raise error(exc) from exc

    @router.post("/{station}/retry", status_code=202)
    def retry(request: Request, station: str, payload: Retry):
        try:
            service = workspace(request, station)
            from neuralcast.pipelines.episode_producer.storage import read_json

            read_json(service.root / "requests" / payload.request_id / "request.json")
            job = request.app.state.job_manager.enqueue_episode(
                station=station, request_id=payload.request_id
            )
            return {"job_id": job.job_id, "request_id": payload.request_id}
        except (ValueError, OSError, RuntimeError) as exc:
            raise error(exc) from exc

    @router.put("/{station}/schedule")
    def schedule(request: Request, station: str, payload: Schedule):
        try:
            return workspace(request, station).save_schedule(
                payload.revision, payload.enabled, payload.target_minutes
            )
        except (ValueError, OSError, RuntimeError) as exc:
            raise error(exc) from exc

    @router.put("/{station}/{episode_id}/feedback")
    def feedback(
        request: Request, station: str, episode_id: Identifier, payload: Feedback
    ):
        try:
            return workspace(request, station).save_feedback(episode_id, payload)
        except (ValueError, OSError, RuntimeError) as exc:
            raise error(exc) from exc

    @router.get("/{station}/{episode_id}/audio/{revision}")
    @router.head("/{station}/{episode_id}/audio/{revision}", include_in_schema=False)
    def audio(request: Request, station: str, episode_id: Identifier, revision: str):
        try:
            path = workspace(request, station).preview_audio(episode_id, revision)
        except (ValueError, OSError) as exc:
            raise error(exc) from exc
        return FileResponse(
            path,
            media_type="audio/mpeg",
            headers={
                "Cache-Control": "private, no-store",
                "Content-Disposition": "inline",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @router.get("/{station}/{episode_id}/history/{kind}/{run_id}")
    def history(
        request: Request,
        station: str,
        episode_id: Identifier,
        kind: str,
        run_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")],
    ):
        if kind not in {"editorial", "delivery", "revisions"}:
            raise HTTPException(404, "Unknown manuscript snapshot")
        try:
            from neuralcast.pipelines.episode_producer.storage import read_json
            from neuralcast.pipelines.episode_producer.models import Plan

            p = workspace(request, station).project(episode_id)
            plan = Plan.model_validate(
                read_json(p.runtime / kind / run_id / "final_plan.json")
            )
            return {"segments": [s.model_dump() for s in plan.segments]}
        except (ValueError, OSError) as exc:
            raise error(exc) from exc

    return router
