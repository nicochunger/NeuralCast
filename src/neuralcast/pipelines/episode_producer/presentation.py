"""Approved episode guides, selected using the player's existing now-playing feed."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from neuralcast.config import RUNTIME_ROOT
from .storage import identifier, read_json, write_json


def published_root() -> Path:
    return RUNTIME_ROOT / "episodes" / "published"


def register_approved_guide(receipt: dict[str, Any], directory: Path) -> None:
    uploaded = receipt["uploaded"]
    media_id = identifier(str(uploaded["media_id"]))
    channel = identifier(receipt["channel"])
    for existing_path in (published_root() / channel).glob("*.json"):
        existing = read_json(existing_path)
        if (
            existing.get("song_id") == uploaded["song_id"]
            and existing.get("media_id") != media_id
        ):
            raise ValueError(
                "Another approved media file shares this song ID; use a distinct episode title before queueing"
            )
    # This registry is never populated by prepare/upload; explicit airing approval owns it.
    write_json(
        published_root() / channel / f"{media_id}.json",
        {
            "channel": channel,
            "media_id": media_id,
            "song_id": uploaded["song_id"],
            "title": receipt["title"],
            "summary": receipt["summary"],
            "duration": receipt["duration"],
            "revision": receipt["revision"],
            "directory": str(directory.resolve()),
            "chapters": receipt["chapters"],
        },
    )


def load_guide(channel: str, media_id: str) -> dict[str, Any] | None:
    try:
        guide = read_json(
            published_root() / identifier(channel) / f"{identifier(media_id)}.json"
        )
        return (
            guide
            if guide.get("channel") == channel and guide.get("media_id") == media_id
            else None
        )
    except (ValueError, OSError):
        return None


def episode_artwork(channel: str, media_id: str, chapter_id: str) -> Path | None:
    identifier(chapter_id)
    guide = load_guide(channel, media_id)
    if not guide:
        return None
    for chapter in guide["chapters"]:
        if chapter["id"] == chapter_id and chapter.get("artwork"):
            directory = Path(guide["directory"]).resolve()
            path = (directory / chapter["artwork"]).resolve()
            if (
                path.is_relative_to(directory)
                and path.suffix == ".jpg"
                and path.is_file()
            ):
                return path
    return None


def find_episode_companion(
    channel: str, observations: list[dict[str, Any]], now: float
) -> dict[str, Any] | None:
    if not observations:
        return None
    candidates = []
    directory = published_root() / identifier(channel)
    for path in directory.glob("*.json"):
        try:
            guide = read_json(path)
            if guide["channel"] != channel:
                continue
            for observed in observations[:9]:
                if str(observed.get("song_id")) != str(guide["song_id"]):
                    continue
                start = float(observed["played_at"])
                end = start + float(guide["duration"])
                if (
                    not math.isfinite(start)
                    or not math.isfinite(end)
                    or not 0 < start <= now
                    or now >= end + 1200
                ):
                    continue
                chapters = []
                for chapter in guide["chapters"]:
                    clean = {
                        key: value
                        for key, value in chapter.items()
                        if key not in {"path", "sha256", "artwork"}
                    }
                    clean["art_url"] = (
                        f"/api/episode-art/{channel}/{guide['media_id']}/{chapter['id']}"
                        if chapter.get("artwork")
                        else None
                    )
                    chapters.append(clean)
                card = {
                    "id": f"episode-{guide['media_id']}-{int(start)}",
                    "channel": channel,
                    "archetype": "episode",
                    "expected_start": start,
                    "expected_end": end,
                    "expires_at": end + 1200,
                    "transcript": "\n\n".join(
                        chapter.get("transcript", "")
                        for chapter in chapters
                        if chapter["kind"] == "commentary"
                    ),
                    "content": {
                        "title": guide["title"],
                        "summary": guide["summary"],
                        "key_points": [],
                        "items": [],
                    },
                    "episode": {
                        "media_id": guide["media_id"],
                        "duration": guide["duration"],
                        "chapters": chapters,
                    },
                    "is_current_episode": observed.get("current") is True and now < end,
                }
                candidates.append(card)
        except (ValueError, OSError, KeyError, TypeError, OverflowError):
            continue
    return (
        max(
            candidates,
            key=lambda card: (card["is_current_episode"], card["expected_start"]),
        )
        if candidates
        else None
    )


def choose_companion(
    ordinary: dict[str, Any] | None, episode: dict[str, Any] | None
) -> dict[str, Any] | None:
    if episode and (
        episode["is_current_episode"]
        or ordinary is None
        or ordinary["expected_start"] < episode["expected_end"]
    ):
        return {
            key: value for key, value in episode.items() if key != "is_current_episode"
        }
    return ordinary
