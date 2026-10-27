"""Prepare and upload standalone podcast episodes for review."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from neuralcast.pipelines.host_orchestrator.channels import (
    get_channel_registry,
    HostChannel,
)
from neuralcast.pipelines.host_orchestrator.transport import (
    AzuraCastClient,
    extract_upload_media_id,
    extract_upload_song_id,
    extract_upload_storage_path,
    RequestsHTTPError,
)
from .models import Music, Plan, Proposal
from .storage import Project, digest, file_hash, read_json, write_json
from .library import resolve_recording, extract_artwork
from .narration import writing_settings, prepare_narration
from .rendering import render
from .manuscript import require_manuscript_approval


def channel_for(project: Project, proposal: Proposal) -> HostChannel:
    channel = get_channel_registry().channels.get(proposal.channel)
    if channel is None or channel.content_station != project.station:
        raise ValueError("Episode channel does not belong to this station")
    return channel


def validated_inputs(
    project: Project,
    *,
    tts_model: str | None = None,
) -> tuple[Proposal, Plan, HostChannel, dict[str, Any], str]:
    proposal, approved_hash = project.approved_proposal()
    channel = channel_for(project, proposal)
    plan = Plan.model_validate(read_json(project.documents / "plan.json"))
    if plan.proposal_hash != approved_hash:
        raise ValueError("Plan references a different approved proposal")
    if project.has_editorial_brief:
        project.editorial_brief().validate_selection(plan)
    recordings = {
        part.id: resolve_recording(project.station_root, part)
        for part in plan.segments
        if isinstance(part, Music)
    }
    settings = writing_settings(channel, tts_model=tts_model)
    fingerprint = digest(
        {
            "proposal": proposal.model_dump(),
            "plan": plan.model_dump(),
            "recordings": recordings,
            "settings": settings,
            "render_version": 1,
            "target": {
                "station": channel.azuracast_station,
                "station_id": channel.azuracast_station_id,
                "media_root": channel.liquidsoap_media_root,
            },
        }
    )
    return (
        proposal,
        plan,
        channel,
        {"recordings": recordings, "settings": settings},
        fingerprint,
    )


def prepare(
    project: Project,
    client: AzuraCastClient,
    *,
    tts_model: str | None = None,
) -> dict[str, Any]:
    """The caller holds project.lock(); no queue submission occurs here."""
    proposal, plan, channel, inputs, revision = validated_inputs(
        project, tts_model=tts_model
    )
    directory = project.runtime / revision
    receipt_path = directory / "production.json"
    if receipt_path.exists():
        receipt = read_json(receipt_path)
        if (
            receipt.get("revision") != revision
            or not (directory / "episode.mp3").is_file()
            or file_hash(directory / "episode.mp3") != receipt.get("render_hash")
        ):
            raise ValueError(
                "A finished render is missing or changed. Restore it or revise the plan; existing upload/queue receipts must not be reset."
            )
        return upload_render(project, channel, receipt, directory, client)
    # A previously finished immutable receipt may be inspected/recovered without
    # retroactive approval. Every new render requires the exact complete script.
    require_manuscript_approval(project, plan)
    directory.mkdir(parents=True, exist_ok=True)
    protected = tuple(
        value
        for recording in inputs["recordings"].values()
        for value in (recording["artist"], recording["title"], recording["album"])
    )
    parts = []
    for part in plan.segments:
        if isinstance(part, Music):
            recording = inputs["recordings"][part.id]
            art = directory / f"{part.id}.jpg"
            has_art = extract_artwork(Path(recording["path"]), art)
            parts.append(
                {
                    "kind": "music",
                    "id": part.id,
                    **recording,
                    "artwork": art.name if has_art else None,
                }
            )
        else:
            spoken = prepare_narration(
                part,
                channel,
                inputs["settings"],
                project.runtime,
                protected,
            )
            parts.append(
                {
                    "kind": "commentary",
                    "id": part.id,
                    "path": spoken["path"],
                    "title": spoken["companion"]["title"],
                    "summary": spoken["companion"]["summary"],
                    "key_points": spoken["companion"]["key_points"],
                    "transcript": spoken["transcript"],
                    "sources": spoken["sources"],
                }
            )
    editions_path = project.runtime / "editions.json"
    editions = read_json(editions_path) if editions_path.exists() else {}
    if revision not in editions:
        editions[revision] = len(editions) + 1
        write_json(editions_path, editions)
    edition_label = {"es": "edición", "fr": "édition"}.get(
        channel.locale.tag.split("-")[0], "edition"
    )
    broadcast_title = f"{proposal.title} ({edition_label} {editions[revision]})"
    output, chapters, duration = render(
        parts,
        directory,
        broadcast_title,
        channel.locale.tag,
        proposal.target_seconds,
        proposal.commentary_budget_seconds,
    )
    # Do not upload a mix assembled while the live library changed underneath us.
    for recording in inputs["recordings"].values():
        if file_hash(Path(recording["path"])) != recording["sha256"]:
            raise ValueError(
                "A source recording changed during rendering; prepare again"
            )
    if (
        digest(project.proposal().model_dump()) != digest(proposal.model_dump())
        or Plan.model_validate(read_json(project.documents / "plan.json")).model_dump()
        != plan.model_dump()
    ):
        raise ValueError(
            "Episode documents changed during production; review and prepare again"
        )
    require_manuscript_approval(project, plan)
    receipt = {
        "version": 1,
        "episode_id": project.id,
        "station": project.station,
        "channel": channel.key,
        "revision": revision,
        "render_hash": file_hash(output),
        "title": proposal.title,
        "broadcast_title": broadcast_title,
        "summary": proposal.premise,
        "duration": duration,
        "tts": {
            "model": inputs["settings"]["tts_model"],
            "provider": inputs["settings"]["provider"],
            "voice": inputs["settings"]["voice"],
        },
        "chapters": chapters,
        "uploaded": None,
    }
    write_json(receipt_path, receipt)
    return upload_render(project, channel, receipt, directory, client)


def upload_render(
    project: Project,
    channel: HostChannel,
    receipt: dict[str, Any],
    directory: Path,
    client: AzuraCastClient,
) -> dict[str, Any]:
    if not receipt.get("uploaded"):
        remote = f"Episodes/{project.id}/{receipt['revision']}/episode.mp3"
        response = None
        # Missing acknowledgements must not create another copy on a blind retry.
        if (
            receipt.get("upload_attempt")
            and receipt["upload_attempt"].get("status") != "rejected"
        ):
            matches = [
                entry
                for entry in client.list_media_files(channel.azuracast_station)
                if str(entry.get("path", "")).casefold() == remote.casefold()
            ]
            if len(matches) != 1:
                raise RuntimeError(
                    "Previous upload is unresolved. Inspect AzuraCast for the recorded path before retrying; no second upload was sent."
                )
            response = matches[0]
        else:
            if receipt.get("upload_attempt"):
                receipt.setdefault("upload_history", []).append(
                    receipt["upload_attempt"]
                )
            large = (directory / "episode.mp3").stat().st_size > 8 * 1024 * 1024
            receipt["upload_attempt"] = {
                "path": remote,
                "attempted_at": time.time(),
                "method": "flow" if large else "base64",
            }
            write_json(directory / "production.json", receipt)
            try:
                if large:
                    response = client.upload_media_chunked(
                        channel.azuracast_station,
                        directory / "episode.mp3",
                        remote_path=remote,
                        timeout=300,
                    )
                else:
                    response = client.upload_media(
                        channel.azuracast_station,
                        directory / "episode.mp3",
                        remote_path=remote,
                        timeout=300,
                    )
            except RequestsHTTPError as exc:
                if exc.response is not None and exc.response.status_code == 413:
                    receipt["upload_attempt"].update(status="rejected", http_status=413)
                    write_json(directory / "production.json", receipt)
                raise
        media_id = extract_upload_media_id(response)
        path = extract_upload_storage_path(response) or remote
        song_id = extract_upload_song_id(response)
        if not media_id or not song_id:
            matches = [
                entry
                for entry in client.list_media_files(channel.azuracast_station)
                if str(entry.get("path", "")).casefold() == remote.casefold()
            ]
            if len(matches) == 1:
                media_id = extract_upload_media_id(matches[0])
                song_id = extract_upload_song_id(matches[0])
                path = extract_upload_storage_path(matches[0]) or path
        if not media_id or not song_id or path.casefold() != remote.casefold():
            raise ValueError(
                "Upload metadata is not ready or its path differs. Re-run prepare to resolve the recorded path without another upload."
            )
        # Uploading for review never publishes a podcast or listener guide.
        receipt["uploaded"] = {
            "media_id": str(media_id),
            "song_id": str(song_id),
            "path": path,
        }
        write_json(directory / "production.json", receipt)
    write_json(
        project.runtime / "latest.json",
        {"revision": receipt["revision"], "render_hash": receipt["render_hash"]},
    )
    return receipt
