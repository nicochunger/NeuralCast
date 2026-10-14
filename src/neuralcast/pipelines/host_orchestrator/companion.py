"""Listener summaries, published after queue success with estimated airtimes."""

from __future__ import annotations

import json
import math
import os
import pathlib
import tempfile
from dataclasses import asdict
from typing import Any
from urllib.parse import urlsplit

from .models import (
    Archetype,
    GeneratedSegmentMetadata,
    QueueTrack,
    TrackFocus,
    TrackMetadata,
)
from .speech import validate_speech_transcript
from .utils import station_state_paths

MUSIC_ARCHETYPES = {
    Archetype.SHORT_STORY,
    Archetype.ALBUM_SPOTLIGHT,
    Archetype.ERA_SNAPSHOT,
    Archetype.DEEP_DIVE,
}
CARD_ARCHETYPES = MUSIC_ARCHETYPES | {Archetype.NEWS, Archetype.CONCERT_CHECK}
VISIBLE_SECONDS = 20 * 60
RETENTION_SECONDS = 7 * 24 * 60 * 60
FILENAME = "host_companion.json"
OPEN_MARKER = "<COMPANION>"
CLOSE_MARKER = "</COMPANION>"


def companion_prompt() -> str:
    return """
LISTENER COMPANION OUTPUT (extends the spoken-script output format):
Write the spoken script normally, followed by <COMPANION> and a JSON object,
then </COMPANION>. These markers and JSON are metadata, never spoken text.
The object must contain exactly: "title" (max 100 characters), "summary"
(one or two concise sentences, max 450 characters), "key_points" (zero to three
short strings, each max 180 characters). Write all fields in the script's language.
Summarize only the core researched claims already present in the script. No new
facts, URLs, greetings, teases, sign-offs, speech tags, or references to the host.
Make a useful reading card, not a transcript. No Markdown or HTML.
If the script cannot be generated, return only NO_SCRIPT as usual.
"""


def _text(value: Any, maximum: int) -> str | None:
    if not isinstance(value, str):
        return None
    value = " ".join(value.split())
    return value if value and len(value) <= maximum else None


def validate_summary(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict) or set(raw) != {"title", "summary", "key_points"}:
        return None
    title = _text(raw["title"], 100)
    summary = _text(raw["summary"], 450)
    points = raw["key_points"]
    if not title or not summary or not isinstance(points, list) or len(points) > 3:
        return None
    cleaned = [_text(point, 180) for point in points]
    if any(point is None for point in cleaned):
        return None
    return {"title": title, "summary": summary, "key_points": cleaned}


def split_companion_output(raw: str) -> tuple[str, dict[str, Any] | None]:
    """Strip optional metadata even when malformed; never send it to TTS."""
    script, marker, metadata = raw.partition(OPEN_MARKER)
    if not marker:
        return raw, None
    payload, closing, trailing = metadata.partition(CLOSE_MARKER)
    if not closing or trailing.strip():
        return script.strip(), None
    try:
        return script.strip(), validate_summary(json.loads(payload))
    except (ValueError, TypeError):
        return script.strip(), None


def _safe_url(raw: str) -> str | None:
    try:
        parsed = urlsplit(raw)
        return (
            raw
            if parsed.scheme in {"http", "https"}
            and parsed.hostname
            and not parsed.username
            else None
        )
    except ValueError:
        return None


def build_companion(
    archetype: Archetype,
    metadata: GeneratedSegmentMetadata,
    title: str,
    current: QueueTrack,
    next_track: QueueTrack,
    current_meta: TrackMetadata,
    next_meta: TrackMetadata,
) -> dict[str, Any] | None:
    if archetype not in CARD_ARCHETYPES:
        return None
    if archetype == Archetype.NEWS:
        if not metadata.news_segment:
            return None
        items = [
            {"headline": story.headline, "source_url": _safe_url(story.source_url)}
            for story in metadata.news_segment.stories
        ]
        return {"title": title, "summary": "", "key_points": [], "items": items}
    if archetype == Archetype.CONCERT_CHECK:
        if not metadata.concert_segment or not metadata.concert_segment.events:
            return None
        items = []
        for event in metadata.concert_segment.events:
            item = asdict(event)
            item.pop("country_code", None)
            item["source_url"] = _safe_url(event.source_url)
            items.append(item)
        return {"title": title, "summary": "", "key_points": [], "items": items}
    summary = validate_summary(metadata.companion)
    if summary is None:
        return None
    focus_next = metadata.track_focus == TrackFocus.NEXT
    track = next_track if focus_next else current
    track_meta = next_meta if focus_next else current_meta
    return {
        **summary,
        "items": [],
        "subject": {
            "artist": track.artist,
            "title": track.title,
            "album": track_meta.album,
            "year": track_meta.year,
        },
    }


def companion_path(channel: str) -> pathlib.Path:
    # Channel resolution also rejects unknown IDs and path traversal.
    _, state_path, _ = station_state_paths(channel)
    return state_path.with_name(FILENAME)


def _read_records(path: pathlib.Path) -> list[dict[str, Any]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(raw, dict) or raw.get("version") != 1:
        return []
    records = raw.get("records")
    if not isinstance(records, list):
        return []
    valid = []
    for item in records:
        if not isinstance(item, dict):
            continue
        if not isinstance(item.get("id"), str) or not isinstance(
            item.get("channel"), str
        ):
            continue
        try:
            start, end = float(item["expected_start"]), float(item["expires_at"])
            expected_end = float(item["expected_end"])
            if (
                not all(math.isfinite(value) for value in (start, end, expected_end))
                or end <= start
            ):
                continue
            if item.get("archetype") not in {a.value for a in CARD_ARCHETYPES}:
                continue
            if not isinstance(item.get("content"), dict) or not isinstance(
                item.get("transcript"), str
            ):
                continue
        except (KeyError, ValueError, TypeError):
            continue
        valid.append(
            {
                **item,
                "expected_start": start,
                "expires_at": end,
                "expected_end": expected_end,
            }
        )
    return valid


def publish_companion(
    channel: str,
    *,
    archetype: Archetype,
    content: dict[str, Any] | None,
    transcript: str,
    media_id: str,
    expected_start: float,
    duration: float,
    now: float,
    path: pathlib.Path | None = None,
) -> None:
    """Caller holds the channel lock; only call after successful insertion."""
    if archetype not in CARD_ARCHETYPES or not content:
        return
    if not all(math.isfinite(value) for value in (expected_start, duration, now)):
        return
    plain_text = validate_speech_transcript(
        transcript, protected_texts=(transcript,)
    ).plain_text
    path = path or companion_path(channel)
    records = [
        r
        for r in _read_records(path)
        if r["expected_start"] >= now - RETENTION_SECONDS and r["id"] != media_id
    ]
    records.append(
        {
            "id": media_id,
            "channel": channel,
            "archetype": archetype.value,
            "expected_start": expected_start,
            "expected_end": expected_start + max(0, duration),
            "expires_at": expected_start + max(0, duration) + VISIBLE_SECONDS,
            "content": content,
            "transcript": plain_text,
        }
    )
    records.sort(key=lambda r: r["expected_start"])
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(
                {"version": 1, "records": records[-100:]}, handle, ensure_ascii=False
            )
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        pathlib.Path(name).unlink(missing_ok=True)


def current_companion(
    channel: str, now: float, *, path: pathlib.Path | None = None
) -> dict[str, Any] | None:
    records = [
        r
        for r in _read_records(path or companion_path(channel))
        if r.get("channel") == channel and r["expected_start"] <= now
    ]
    if not records:
        return None
    latest = max(records, key=lambda r: r["expected_start"])
    return latest if now < latest["expires_at"] else None
