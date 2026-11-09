"""Inputs and saved owner feedback for the episode workspace."""

from typing import Annotated
from pydantic import Field
from .models import Document, Identifier, Music, Source, Text


class Topic(Document):
    title: Annotated[str, Field(min_length=1, max_length=160)]
    premise: Text
    angle: Text
    artists: list[str] = Field(default_factory=list, max_length=10)


class Topics(Document):
    ideas: list[Topic] = Field(min_length=1, max_length=3)


class Concept(Document):
    title: Annotated[str, Field(min_length=1, max_length=160)]
    premise: Text
    outline: list[Text] = Field(min_length=1, max_length=20)
    instructions: Text
    candidate_songs: list[Music] = Field(min_length=1, max_length=12)
    initial_research: list[Source] = Field(default_factory=list, max_length=30)


class Feedback(Document):
    revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    overall: str = Field(default="", max_length=5000)
    sections: dict[Identifier, Annotated[str, Field(max_length=3000)]] = Field(
        default_factory=dict, max_length=40
    )
    research: bool = False
