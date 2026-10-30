"""Read-only podcast feeds and cloud chapters from the publication allowlist."""

from __future__ import annotations

import datetime as dt
import json
import math
import os
import uuid
import xml.etree.ElementTree as ET
from email.utils import format_datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, HttpUrl

from neuralcast.config import ASSETS_ROOT, ALLOWED_STATION_SLUGS

from . import publication

ASSET_DIRECTORY = ASSETS_ROOT / "podcasts"
CHAPTER_TITLE_LIMIT = 40
CHAPTER_MEDIA_TYPE = "application/json+chapters"
ITUNES = "http://www.itunes.com/dtds/podcast-1.0.dtd"
PODCAST = "https://podcastindex.org/namespace/1.0"
ATOM = "http://www.w3.org/2005/Atom"
for prefix, namespace in (("itunes", ITUNES), ("podcast", PODCAST), ("atom", ATOM)):
    ET.register_namespace(prefix, namespace)


class Show(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    description: str
    author: str
    language: str
    explicit: bool
    category: str = "Music"


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    public_base_url: HttpUrl
    website_base_url: HttpUrl | None = None
    shows: dict[str, Show]


def settings() -> Settings:
    value = json.loads((ASSET_DIRECTORY / "shows.json").read_text(encoding="utf-8"))
    # Explicit origins preserve the HTTPS proxy prefix and never trust Host headers.
    for field, variable in (
        ("public_base_url", "NEURALCAST_PODCAST_PUBLIC_BASE_URL"),
        ("website_base_url", "NEURALCAST_PODCAST_WEBSITE_BASE_URL"),
    ):
        if os.environ.get(variable):
            value[field] = os.environ[variable]
    return Settings.model_validate(value)


def show_settings(station: str, config: Settings) -> Show:
    if station not in ALLOWED_STATION_SLUGS or station not in config.shows:
        raise ValueError("Unknown podcast")
    return config.shows[station]


def public_url(config: Settings, path: str) -> str:
    return str(config.public_base_url).rstrip("/") + path


def episode_link(config: Settings, station: str, episode_id: str) -> str:
    if config.website_base_url:
        return f"{str(config.website_base_url).rstrip('/')}/episodes/{station}/{episode_id}"
    return public_url(config, f"/public/episodes/{station}/{episode_id}")


def cover_path(station: str) -> Path:
    show_settings(station, settings())
    path = ASSET_DIRECTORY / f"{station}-cover.png"
    if not path.is_file():
        raise FileNotFoundError("Podcast cover unavailable")
    return path


def short_title(value: str) -> str:
    value = " ".join(value.split())
    if len(value) <= CHAPTER_TITLE_LIMIT:
        return value
    prefix = value[: CHAPTER_TITLE_LIMIT - 1]
    if " " in prefix:
        prefix = prefix.rsplit(" ", 1)[0]
    return prefix.rstrip(" ,:;—-") + "…"


def chapters(station: str, episode_id: str) -> dict[str, Any]:
    config = settings()
    show_settings(station, config)
    value = publication.load_publication(station, episode_id)
    overrides = (
        json.loads(
            (ASSET_DIRECTORY / "chapter_titles.json").read_text(encoding="utf-8")
        )
        .get(station, {})
        .get(episode_id, {})
    )
    result = []
    previous = -1.0
    for chapter in value["chapters"]:
        start = float(chapter["start"])
        if (
            not math.isfinite(start)
            or not 0 <= start < value["duration"]
            or start <= previous
        ):
            raise ValueError("Invalid published chapter timing")
        previous = start
        title = overrides.get(chapter["id"]) or chapter.get("title") or chapter["id"]
        if chapter["kind"] == "music" and chapter.get("artist"):
            combined = f"{title} — {chapter['artist']}"
            if len(combined) <= CHAPTER_TITLE_LIMIT:
                title = combined
        item: dict[str, Any] = {
            "startTime": start,
            "title": short_title(title),
            "url": episode_link(config, station, episode_id),
        }
        if chapter.get("artwork"):
            try:
                publication.published_artwork(station, episode_id, chapter["id"])
            except (OSError, ValueError):
                pass
            else:
                item["img"] = public_url(
                    config,
                    f"/public/episodes/{station}/{episode_id}/art/{chapter['id']}",
                )
        result.append(item)
    return {"version": "1.2.0", "chapters": result}


def _tag(parent: ET.Element, name: str, text: Any, **attributes: str) -> ET.Element:
    element = ET.SubElement(parent, name, attributes)
    element.text = str(text)
    return element


def feed(station: str) -> bytes:
    config = settings()
    show = show_settings(station, config)
    feed_url = public_url(config, f"/public/podcasts/{station}/feed.xml")
    cover_url = public_url(config, f"/public/podcasts/{station}/cover.png")
    link = (
        f"{str(config.website_base_url).rstrip('/')}/episodes?station={station}"
        if config.website_base_url
        else feed_url
    )
    root = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(root, "channel")
    for name, text in (
        ("title", show.title),
        ("description", show.description),
        ("link", link),
        ("language", show.language),
        (f"{{{ITUNES}}}author", show.author),
        (f"{{{ITUNES}}}explicit", str(show.explicit).lower()),
        (f"{{{ITUNES}}}type", "episodic"),
    ):
        _tag(channel, name, text)
    ET.SubElement(channel, f"{{{ITUNES}}}category", {"text": show.category})
    ET.SubElement(channel, f"{{{ITUNES}}}image", {"href": cover_url})
    ET.SubElement(
        channel,
        f"{{{ATOM}}}link",
        {"href": feed_url, "rel": "self", "type": "application/rss+xml"},
    )
    image = ET.SubElement(channel, "image")
    for name, text in (("url", cover_url), ("title", show.title), ("link", link)):
        _tag(image, name, text)
    for summary in publication.list_episodes(station):
        value = publication.load_publication(station, summary["id"])
        if value["language"] != show.language:
            continue
        try:
            publication.published_audio(station, value["id"])
        except (OSError, ValueError):
            # Do not advertise unavailable recordings as downloadable episodes.
            continue
        prefix = f"/public/episodes/{station}/{value['id']}"
        item = ET.SubElement(channel, "item")
        for name, text in (
            ("title", value["title"]),
            ("description", value["description"]),
            ("link", episode_link(config, station, value["id"])),
            (
                "pubDate",
                format_datetime(dt.datetime.fromisoformat(value["published_at"])),
            ),
            (f"{{{ITUNES}}}duration", round(value["duration"])),
            (f"{{{ITUNES}}}explicit", str(show.explicit).lower()),
            (f"{{{ITUNES}}}episodeType", "full"),
        ):
            _tag(item, name, text)
        # Identity excludes mutable titles, numbers, dates, domains, and revisions.
        guid = uuid.uuid5(
            uuid.NAMESPACE_URL, f"neuralcast:podcast:{station}:{value['id']}"
        )
        _tag(item, "guid", f"urn:uuid:{guid}", isPermaLink="false")
        if "episode_number" in value:
            _tag(item, f"{{{ITUNES}}}episode", value["episode_number"])
        ET.SubElement(
            item,
            "enclosure",
            {
                "url": public_url(config, prefix + "/audio"),
                "length": str(value["audio_bytes"]),
                "type": "audio/mpeg",
            },
        )
        ET.SubElement(
            item,
            f"{{{PODCAST}}}chapters",
            {
                "url": public_url(config, prefix + "/chapters.json"),
                "type": CHAPTER_MEDIA_TYPE,
            },
        )
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)
