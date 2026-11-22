"""Inputs and saved owner feedback for the episode workspace."""

import re
from typing import Annotated

from pydantic import Field, field_validator
from .models import Document, Identifier, Music, Source, Text

EPISODE_TITLE_MAX_LENGTH = 60


def _validate_episode_title(value: str) -> str:
    if ":" in value:
        raise ValueError(
            "Episode title must be short and punchy without a colon or subtitle"
        )
    if re.match(r"^#\d+\b", value):
        raise ValueError(
            "Do not include an episode number in the title; numbering is applied automatically"
        )
    return value


class Topic(Document):
    title: Annotated[str, Field(min_length=1, max_length=EPISODE_TITLE_MAX_LENGTH)]
    premise: Text
    angle: Text
    artists: list[str] = Field(default_factory=list, max_length=10)
    playlists: list[str] = Field(default_factory=list, max_length=6)

    @field_validator("title")
    @classmethod
    def valid_title(cls, value: str) -> str:
        return _validate_episode_title(value)


class Topics(Document):
    ideas: list[Topic] = Field(min_length=1, max_length=12)


class SelectedTopic(Document):
    index: int = Field(ge=0, le=11, strict=True)
    reason: Text


class TopicSelection(Document):
    ranked: list[SelectedTopic] = Field(min_length=1, max_length=4)


class Concept(Document):
    title: Annotated[str, Field(min_length=1, max_length=EPISODE_TITLE_MAX_LENGTH)]
    premise: Text
    outline: list[Text] = Field(min_length=1, max_length=20)
    instructions: Text
    candidate_songs: list[Music] = Field(min_length=1, max_length=12)
    initial_research: list[Source] = Field(default_factory=list, max_length=30)

    @field_validator("title")
    @classmethod
    def valid_title(cls, value: str) -> str:
        return _validate_episode_title(value)


class Feedback(Document):
    revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    overall: str = Field(default="", max_length=5000)
    sections: dict[Identifier, Annotated[str, Field(max_length=3000)]] = Field(
        default_factory=dict, max_length=40
    )
    research: bool = False
