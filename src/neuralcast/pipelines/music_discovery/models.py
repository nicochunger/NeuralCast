"""Strict documents for discovery and feedback; agents never own decisions."""

from __future__ import annotations

from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

Identifier = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,79}$")]
Text = Annotated[str, Field(min_length=1, max_length=4000)]


class Document(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Candidate(Document):
    id: Identifier
    artist: Annotated[str, Field(min_length=1, max_length=250)]
    title: Annotated[str, Field(min_length=1, max_length=250)]
    album: Annotated[str, Field(min_length=1, max_length=300)]
    year: Annotated[str, Field(pattern=r"^\d{4}$")]
    playlist: Annotated[str, Field(min_length=1, max_length=150)]
    rationale: Text
    artist_background: Annotated[str, Field(max_length=700)] = ""
    listening_note: Annotated[str, Field(max_length=400)] = ""
    uncertainty: Annotated[str, Field(max_length=2000)]
    source_url: str
    research_urls: list[str] = Field(min_length=1, max_length=8)

    @field_validator("artist", "title", "album", "playlist")
    @classmethod
    def canonical_text(cls, value: str) -> str:
        if "[" in value or "]" in value or any(ord(c) < 32 for c in value):
            raise ValueError("Catalog names cannot contain control markers")
        return value

    @field_validator("playlist")
    @classmethod
    def playlist_name(cls, value: str) -> str:
        if "/" in value or "\\" in value or value in {".", ".."}:
            raise ValueError("Expected a playlist name")
        return value

    @field_validator("source_url")
    @classmethod
    def youtube_source(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or parsed.hostname not in {"youtube.com", "www.youtube.com", "youtu.be"}
            or parsed.username
            or parsed.port not in (None, 443)
        ):
            raise ValueError("Auditions require an HTTPS YouTube recording URL")
        from urllib.parse import parse_qs

        video = (
            parsed.path.strip("/")
            if parsed.hostname == "youtu.be"
            else parse_qs(parsed.query).get("v", [""])[0]
        )
        import re

        if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video):
            raise ValueError("Expected an exact YouTube video")
        return f"https://www.youtube.com/watch?v={video}"

    @field_validator("research_urls")
    @classmethod
    def research_sources(cls, values: list[str]) -> list[str]:
        for value in values:
            parsed = urlsplit(value)
            if (
                parsed.scheme not in {"https", "http"}
                or not parsed.hostname
                or parsed.username
            ):
                raise ValueError("Invalid research URL")
        return values


class Suggestions(Document):
    candidates: list[Candidate] = Field(min_length=5, max_length=5)


class Feedback(Document):
    accepted: bool
    reasoning: Annotated[str, Field(max_length=4000)] = ""


class FeedbackDraft(Document):
    batch_id: Identifier
    revision: int = Field(ge=0)
    feedback: dict[str, Feedback] = Field(max_length=5)


class Placement(Document):
    candidate_id: Identifier
    playlist: str


class PreferenceUpdate(Document):
    candidate_ids: list[Identifier] = Field(min_length=1, max_length=5)
    scope: Literal["recording", "artist", "playlist", "station"]
    text: Text


class FeedbackResult(Document):
    placements: list[Placement] = Field(max_length=5)
    preference_updates: list[PreferenceUpdate] = Field(max_length=10)


class Audition(Document):
    sha256: str
    size: int = Field(gt=0)


class Batch(Document):
    version: Literal[1] = 1
    id: Identifier
    station: Literal["neuralcast", "neuralforge"]
    status: Literal["preparing", "review", "submitted", "processed"] = "preparing"
    created_at: str
    candidates: list[Candidate] = Field(default_factory=list)
    auditions: dict[str, Audition] = Field(default_factory=dict)
    feedback: dict[str, Feedback] = Field(default_factory=dict)
    revision: int = 0
    submission_hash: str | None = None
    editorial: FeedbackResult | None = None
    promoted: list[str] = Field(default_factory=list)
    cleaned: list[str] = Field(default_factory=list)
    error: str | None = None


class State(Document):
    version: Literal[1] = 1
    current_batch_id: Identifier | None = None
    last_scheduled_week: str | None = None
    preferences: list[dict] = Field(default_factory=list)
