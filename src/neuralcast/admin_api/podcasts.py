"""Anonymous RSS and cloud chapter delivery; no production or station mutations."""

from __future__ import annotations

import hashlib
import json

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import FileResponse

from neuralcast.pipelines.episode_producer import rss


def _document(request: Request, content: bytes, media_type: str) -> Response:
    etag = '"' + hashlib.sha256(content).hexdigest() + '"'
    headers = {
        "Cache-Control": "public, max-age=60",
        "ETag": etag,
        "X-Content-Type-Options": "nosniff",
        "Access-Control-Allow-Origin": "*",
    }
    candidates = request.headers.get("if-none-match", "").split(",")
    if any(value.strip().removeprefix("W/") in (etag, "*") for value in candidates):
        return Response(status_code=304, headers=headers)
    headers["Content-Length"] = str(len(content))
    return Response(
        b"" if request.method == "HEAD" else content,
        media_type=media_type,
        headers=headers,
    )


def podcast_router() -> APIRouter:
    router = APIRouter()

    @router.get("/public/podcasts/{station}/feed.xml")
    @router.head("/public/podcasts/{station}/feed.xml", include_in_schema=False)
    def feed(station: str, request: Request) -> Response:
        try:
            content = rss.feed(station)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="Unknown podcast") from exc
        return _document(request, content, "application/rss+xml; charset=utf-8")

    @router.get("/public/podcasts/{station}/cover.png")
    @router.head("/public/podcasts/{station}/cover.png", include_in_schema=False)
    def cover(station: str) -> FileResponse:
        try:
            path = rss.cover_path(station)
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=404, detail="No podcast cover") from exc
        return FileResponse(
            path,
            media_type="image/png",
            headers={"Cache-Control": "public, max-age=3600"},
        )

    @router.get("/public/episodes/{station}/{episode_id}/chapters.json")
    @router.head(
        "/public/episodes/{station}/{episode_id}/chapters.json", include_in_schema=False
    )
    def chapters(station: str, episode_id: str, request: Request) -> Response:
        try:
            content = json.dumps(
                rss.chapters(station, episode_id), ensure_ascii=False, allow_nan=False
            ).encode("utf-8")
        except (OSError, ValueError, KeyError) as exc:
            raise HTTPException(
                status_code=404, detail="No published chapters"
            ) from exc
        return _document(request, content, rss.CHAPTER_MEDIA_TYPE)

    return router
