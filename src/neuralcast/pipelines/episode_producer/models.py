"""Reviewable episode documents, with strict recording and narration identities."""

from __future__ import annotations

from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Identifier = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,79}$")]
Text = Annotated[str, Field(min_length=1, max_length=5000)]


class Document(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Proposal(Document):
    version: Literal[1] = 1
    episode_id: Identifier
    channel: Identifier
    title: Annotated[str, Field(min_length=1, max_length=160)]
    premise: Text
    target_seconds: int = Field(default=2400, ge=300, le=7200)
    commentary_budget_seconds: int = Field(default=240, ge=30, le=1200)
    outline: list[Text] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def budget_fits(self) -> Proposal:
        if self.commentary_budget_seconds >= self.target_seconds:
            raise ValueError("Commentary budget must be shorter than the episode")
        return self


class Source(Document):
    url: str
    claim: Text

    @field_validator("url")
    @classmethod
    def http_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
        ):
            raise ValueError("Source must be an HTTP(S) URL without credentials")
        return value


class Summary(Document):
    title: Annotated[str, Field(min_length=1, max_length=100)]
    summary: Annotated[str, Field(min_length=1, max_length=450)]
    key_points: list[Annotated[str, Field(min_length=1, max_length=180)]] = Field(
        default_factory=list, max_length=3
    )


class Music(Document):
    kind: Literal["music"]
    id: Identifier
    playlist: Annotated[str, Field(min_length=1, max_length=150)]
    artist: Annotated[str, Field(min_length=1, max_length=250)]
    title: Annotated[str, Field(min_length=1, max_length=250)]
    rationale: Text

    @field_validator("playlist")
    @classmethod
    def playlist_stem(cls, value: str) -> str:
        if (
            "/" in value
            or "\\" in value
            or value in {".", ".."}
            or value.endswith(".csv")
        ):
            raise ValueError("Use a playlist name, not a path or CSV filename")
        return value


class Commentary(Document):
    kind: Literal["commentary"]
    id: Identifier
    role: Literal["opening", "bridge", "closing"]
    archetype: Literal[
        "short_story", "album_spotlight", "era_snapshot", "deep_dive", "block_intro"
    ] = "short_story"
    brief: Text
    max_words: int = Field(default=100, ge=15, le=300)
    sources: list[Source] = Field(default_factory=list, max_length=20)
    script: Text | None = None
    companion: Summary | None = None

    @model_validator(mode="after")
    def manual_script_needs_summary(self) -> Commentary:
        if self.script and not self.companion:
            raise ValueError("A supplied script needs its reading companion")
        return self


class Plan(Document):
    version: Literal[1] = 1
    proposal_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    segments: list[Annotated[Music | Commentary, Field(discriminator="kind")]] = Field(
        min_length=3, max_length=40
    )

    @model_validator(mode="after")
    def coherent_sequence(self) -> Plan:
        ids = [part.id for part in self.segments]
        if len(ids) != len(set(ids)):
            raise ValueError("Segment IDs must be unique")
        if not any(isinstance(part, Music) for part in self.segments):
            raise ValueError("An episode must contain music")
        if (
            not isinstance(self.segments[0], Commentary)
            or self.segments[0].role != "opening"
        ):
            raise ValueError("Begin with an opening commentary")
        if (
            not isinstance(self.segments[-1], Commentary)
            or self.segments[-1].role != "closing"
        ):
            raise ValueError("End with a closing commentary")
        return self
