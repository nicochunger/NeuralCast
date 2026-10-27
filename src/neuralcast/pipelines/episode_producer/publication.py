"""Permanent, explicitly published on-demand episodes; no station mutations."""

from __future__ import annotations

import datetime as dt
import math
import shutil
from pathlib import Path
from typing import Any

from neuralcast.config import ALLOWED_STATION_SLUGS, RUNTIME_ROOT, station_dir_from_slug
from neuralcast.pipelines.host_orchestrator.channels import get_channel_registry
from pydantic import TypeAdapter

from .models import EpisodeNumber
from .storage import Project, file_hash, identifier, read_json, write_json

CHAPTER_FIELDS = {
    "kind",
    "id",
    "start",
    "end",
    "title",
    "artist",
    "album",
    "year",
    "rationale",
    "summary",
    "key_points",
    "transcript",
    "sources",
}


def catalogue_root() -> Path:
    return RUNTIME_ROOT / "episodes" / "catalogue"


def episode_directory(station: str, episode_id: str) -> Path:
    if station not in ALLOWED_STATION_SLUGS:
        raise ValueError("Unknown station")
    return catalogue_root() / station / identifier(episode_id)


def _media_path(station: str, relative: str) -> Path:
    """Only the dedicated station episode tree may be served, never arbitrary media."""
    root = (station_dir_from_slug(station) / "songs" / "episodes").resolve()
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts or "\\" in relative:
        raise ValueError("Invalid episode media path")
    if not path.parts or path.parts[0].casefold() != "episodes":
        raise ValueError("Audio is outside the episode media tree")
    resolved = (root / Path(*path.parts[1:])).resolve()
    if not resolved.is_relative_to(root) or resolved.suffix != ".mp3":
        raise ValueError("Audio is outside the episode media tree")
    return resolved


def load_publication(station: str, episode_id: str) -> dict[str, Any]:
    value = read_json(episode_directory(station, episode_id) / "manifest.json")
    if value["station"] != station or value["id"] != episode_id:
        raise ValueError("Invalid publication identity")
    if "episode_number" in value:
        TypeAdapter(EpisodeNumber).validate_python(value["episode_number"])
    return value


def set_episode_number(project: Project, number: int) -> dict[str, Any]:
    """Assign display metadata without changing published URLs or audio approvals.

    Caller holds the project lock. Existing publication snapshots receive only
    the number; the existing proposal supplies source metadata, while station
    media and historical production receipts remain untouched.
    """
    number = TypeAdapter(EpisodeNumber).validate_python(number)
    manifest = episode_directory(project.station, project.id) / "manifest.json"
    existing = (
        load_publication(project.station, project.id) if manifest.exists() else None
    )
    if existing and existing.get("episode_number") not in (None, number):
        raise ValueError("Published episode already has a different number")
    for path in (catalogue_root() / project.station).glob("*/manifest.json"):
        other = load_publication(project.station, path.parent.name)
        if other["id"] != project.id and other.get("episode_number") == number:
            raise ValueError("Episode number is already published in this station")
    directory = project.set_episode_number(number)
    if existing is not None:
        write_json(manifest, {**existing, "episode_number": number})
    return {
        "episode_id": project.id,
        "episode_number": number,
        "documents": str(directory),
    }


def public_episode(value: dict[str, Any], *, detail: bool = True) -> dict[str, Any]:
    """Whitelist listener fields; operational paths, hashes, and receipts stay private."""
    prefix = f"/public/episodes/{value['station']}/{value['id']}"
    result = {
        key: value[key]
        for key in (
            "id",
            "station",
            "channel",
            "language",
            "title",
            "description",
            "published_at",
            "duration",
            "revision",
        )
    }
    if "episode_number" in value:
        result["episode_number"] = value["episode_number"]
    result["audio_url"] = f"{prefix}/audio"
    if detail:
        result["chapters"] = [
            {
                **{k: v for k, v in chapter.items() if k in CHAPTER_FIELDS},
                "art_url": (
                    f"{prefix}/art/{chapter['id']}" if chapter.get("artwork") else None
                ),
            }
            for chapter in value["chapters"]
        ]
        result["transcript"] = "\n\n".join(
            chapter.get("transcript", "")
            for chapter in value["chapters"]
            if chapter["kind"] == "commentary"
        )
    return result


def list_episodes(station: str | None = None) -> list[dict[str, Any]]:
    stations = [station] if station else ALLOWED_STATION_SLUGS
    results = []
    for slug in stations:
        if slug not in ALLOWED_STATION_SLUGS:
            raise ValueError("Unknown station")
        for path in (catalogue_root() / slug).glob("*/manifest.json"):
            try:
                results.append(
                    public_episode(
                        load_publication(slug, path.parent.name), detail=False
                    )
                )
            except (OSError, ValueError, KeyError, TypeError):
                continue
    return sorted(results, key=lambda episode: episode["published_at"], reverse=True)


def published_audio(station: str, episode_id: str) -> Path:
    value = load_publication(station, episode_id)
    path = _media_path(station, value["audio_path"])
    if not path.is_file() or path.stat().st_size != value["audio_bytes"]:
        raise ValueError("Published audio is missing or changed")
    return path


def published_artwork(station: str, episode_id: str, chapter_id: str) -> Path:
    value = load_publication(station, episode_id)
    identifier(chapter_id)
    chapter = next((c for c in value["chapters"] if c["id"] == chapter_id), None)
    if not chapter or not chapter.get("artwork"):
        raise ValueError("No chapter artwork")
    directory = episode_directory(station, episode_id).resolve()
    path = (directory / chapter["artwork"]).resolve()
    if (
        not path.is_relative_to(directory)
        or path.suffix != ".jpg"
        or not path.is_file()
    ):
        raise ValueError("No chapter artwork")
    return path


def publish(
    project: Project, expected_hash: str, description: str | None = None
) -> dict[str, Any]:
    """Caller holds project lock. Publish the exact reviewed render, without queueing."""
    latest = read_json(project.runtime / "latest.json")
    revision = latest["revision"]
    if len(revision) != 64 or any(c not in "0123456789abcdef" for c in revision):
        raise ValueError("Invalid stored revision")
    directory = project.runtime / revision
    receipt = read_json(directory / "production.json")
    if (
        receipt["render_hash"] != expected_hash
        or file_hash(directory / "episode.mp3") != expected_hash
    ):
        raise ValueError("Publication requires the exact reviewed render hash")
    if receipt["station"] != project.station or receipt["episode_id"] != project.id:
        raise ValueError("Production identity mismatch")
    uploaded = receipt.get("uploaded")
    if not uploaded:
        raise ValueError("Upload the episode for review before publication")
    destination = episode_directory(project.station, project.id)
    if (destination / "manifest.json").exists():
        existing = load_publication(project.station, project.id)
        if existing["revision"] != revision or existing["render_hash"] != expected_hash:
            raise ValueError(
                "This episode URL already publishes another revision; preserve it and use a new episode ID"
            )
        return public_episode(existing)
    audio = _media_path(project.station, uploaded["path"])
    if not audio.is_file() or file_hash(audio) != expected_hash:
        raise ValueError("AzuraCast audio differs from the reviewed render")
    duration = float(receipt["duration"])
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("Invalid episode duration")
    channel = get_channel_registry().channels[receipt["channel"]]
    destination.mkdir(parents=True, exist_ok=True)
    chapters = []
    for chapter in receipt["chapters"]:
        clean = {k: v for k, v in chapter.items() if k in CHAPTER_FIELDS}
        chapter_id = identifier(clean["id"])
        if chapter.get("artwork"):
            artwork = (directory / chapter["artwork"]).resolve()
            if (
                not artwork.is_relative_to(directory.resolve())
                or artwork.suffix != ".jpg"
            ):
                raise ValueError("Invalid chapter artwork")
            # Tiny thumbnails are publication assets, independent of production caches.
            target = destination / f"{chapter_id}.jpg"
            shutil.copyfile(artwork, target)
            clean["artwork"] = target.name
        chapters.append(clean)
    value = {
        "version": 1,
        "id": project.id,
        "station": project.station,
        "channel": receipt["channel"],
        "language": channel.locale.tag,
        "title": receipt["title"],
        "description": (
            description.strip()
            if description and description.strip()
            else receipt["summary"]
        ),
        "published_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "duration": duration,
        "revision": revision,
        "render_hash": expected_hash,
        "audio_path": uploaded["path"],
        "audio_bytes": audio.stat().st_size,
        "chapters": chapters,
    }
    if project.episode_number is not None:
        value["episode_number"] = project.episode_number
    # This is the visibility boundary: all assets exist before the atomic manifest write.
    write_json(destination / "manifest.json", value)
    return public_episode(value)
