"""Authenticated batch review and private ranged audition audio."""

from typing import Callable

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse

from neuralcast.pipelines.music_discovery.models import FeedbackDraft, Identifier
from neuralcast.pipelines.music_discovery.service import DiscoveryService
from neuralcast.pipelines.music_discovery.storage import ConflictError
from neuralcast.pipelines.music_discovery.media import artwork_type
from pydantic import Field
from neuralcast.pipelines.music_discovery.models import Document

from .jobs import JobConflictError


class Submission(Document):
    batch_id: Identifier
    revision: int = Field(ge=0)


def discovery_router(auth: Callable) -> APIRouter:
    router = APIRouter(prefix="/admin/discovery", dependencies=[Depends(auth)])

    def service(request: Request, station: str) -> DiscoveryService:
        try:
            return request.app.state.discovery_factory(station)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="Unknown station") from exc

    def error(exc: Exception) -> HTTPException:
        if isinstance(exc, (ConflictError, JobConflictError)):
            return HTTPException(status_code=409, detail=str(exc))
        if isinstance(exc, FileNotFoundError):
            return HTTPException(
                status_code=404, detail="Discovery batch or audio not found"
            )
        return HTTPException(status_code=422, detail=str(exc))

    @router.get("/{station}")
    def view(request: Request, station: str, response: Response):
        response.headers["Cache-Control"] = "no-store"
        try:
            result = service(request, station).view()
            result["job"] = request.app.state.job_manager.latest_discovery_job(station)
            return result
        except (ValueError, OSError) as exc:
            raise error(exc) from exc

    @router.post("/{station}/generate", status_code=202)
    def generate(request: Request, station: str):
        current_service = service(request, station)
        try:
            batch = current_service.store.current()
            if batch and batch.status in {"review", "submitted"}:
                raise ConflictError(
                    "Review and process the current batch before generating more suggestions"
                )
            job = request.app.state.job_manager.enqueue_discovery(
                station=station, action="generate"
            )
            return {"job_id": job.job_id}
        except (ValueError, ConflictError, JobConflictError, OSError) as exc:
            raise error(exc) from exc

    @router.put("/{station}/feedback")
    def save_feedback(request: Request, station: str, payload: FeedbackDraft):
        try:
            batch = service(request, station).save_feedback(payload)
            return {"batch": batch.model_dump()}
        except (ValueError, ConflictError, OSError) as exc:
            raise error(exc) from exc

    @router.post("/{station}/process", status_code=202)
    def process(request: Request, station: str, payload: Submission):
        try:
            batch = service(request, station).submit(payload.batch_id, payload.revision)
            if batch.status == "processed":
                return {"already_processed": True}
            job = request.app.state.job_manager.enqueue_discovery(
                station=station, action="process", batch_id=batch.id
            )
            return {"job_id": job.job_id}
        except (ValueError, ConflictError, JobConflictError, OSError) as exc:
            raise error(exc) from exc

    @router.get("/{station}/batches/{batch_id}/audio/{candidate_id}")
    @router.head(
        "/{station}/batches/{batch_id}/audio/{candidate_id}", include_in_schema=False
    )
    def audio(
        request: Request, station: str, batch_id: Identifier, candidate_id: Identifier
    ):
        try:
            store = service(request, station).store
            batch = store.load_batch(batch_id)
            path = store.verified_audio(batch, candidate_id)
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

    @router.get("/{station}/batches/{batch_id}/artwork/{candidate_id}")
    @router.head(
        "/{station}/batches/{batch_id}/artwork/{candidate_id}", include_in_schema=False
    )
    def artwork(
        request: Request, station: str, batch_id: Identifier, candidate_id: Identifier
    ):
        try:
            store = service(request, station).store
            batch = store.load_batch(batch_id)
            path = store.artwork_path(batch, candidate_id)
            if path.is_symlink() or not path.is_file():
                raise FileNotFoundError("Album artwork not found")
            if path.stat().st_size > 10 * 1024 * 1024:
                raise ValueError("Album artwork exceeds size limit")
            data = path.read_bytes()
            media_type = artwork_type(data)
            if media_type is None:
                raise FileNotFoundError("Supported album artwork not found")
        except (ValueError, OSError) as exc:
            raise error(exc) from exc
        return Response(
            content=data if request.method != "HEAD" else b"",
            media_type=media_type,
            headers={
                "Content-Length": str(len(data)),
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    return router
