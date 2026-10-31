"""Approved concepts, focused findings and final manuscript edits."""

from typing import Annotated, Literal

from pydantic import Field, model_validator

from .models import (
    Document,
    Identifier,
    Music,
    NarrationScript,
    Plan,
    Source,
    Summary,
    Text,
)

SHA256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
ReviewText = Annotated[str, Field(min_length=1, max_length=1500)]


class EditorialBrief(Document):
    version: Literal[1] = 1
    proposal_hash: SHA256
    instructions: Text
    candidate_songs: list[Music] = Field(min_length=1, max_length=30)
    initial_research: list[Source] = Field(default_factory=list, max_length=100)
    narration_words_per_minute: int = Field(default=165, ge=100, le=220)
    duration_tolerance_seconds: int = Field(default=120, ge=15, le=600)

    @model_validator(mode="after")
    def unique_candidates(self) -> "EditorialBrief":
        ids = [song.id for song in self.candidate_songs]
        identities = [
            (song.artist.casefold(), song.title.casefold())
            for song in self.candidate_songs
        ]
        if len(set(ids)) != len(ids) or len(set(identities)) != len(identities):
            raise ValueError("Candidate songs must have unique IDs and identities")
        return self

    def validate_selection(self, plan: Plan) -> None:
        candidates = {song.id: song for song in self.candidate_songs}
        for part in plan.segments:
            if isinstance(part, Music):
                candidate = candidates.get(part.id)
                if candidate is None or any(
                    getattr(part, field) != getattr(candidate, field)
                    for field in ("playlist", "artist", "title")
                ):
                    raise ValueError(f"Unapproved song identity: {part.id}")


class Draft(Document):
    plan: Plan
    research_notes: Text
    open_questions: list[ReviewText] = Field(default_factory=list, max_length=20)
    production_notes: list[ReviewText] = Field(default_factory=list, max_length=20)


class Finding(Document):
    segment_ids: list[Identifier] = Field(min_length=1, max_length=40)
    quote: ReviewText
    severity: Literal["major", "minor"]
    problem: ReviewText
    correction: ReviewText


class Review(Document):
    strengths: list[ReviewText] = Field(default_factory=list, max_length=3)
    findings: list[Finding] = Field(default_factory=list, max_length=6)
    additional_sources: list[Source] = Field(default_factory=list, max_length=20)


class Resolution(Document):
    finding_id: Identifier
    status: Literal["resolved", "dismissed", "unresolved"]
    explanation: ReviewText


class DeliveryPassage(Document):
    segment_id: Identifier
    script: NarrationScript
    explanation: ReviewText
    companion: Summary | None = None
    sources: list[Source] | None = Field(default=None, max_length=20)


class DeliveryNote(Document):
    segment_id: Identifier
    note: ReviewText


class Edit(Document):
    passages: list[DeliveryPassage] = Field(min_length=1, max_length=40)
    resolutions: list[Resolution] = Field(default_factory=list, max_length=40)
    open_questions: list[ReviewText] = Field(default_factory=list, max_length=20)
    production_notes: list[ReviewText] = Field(default_factory=list, max_length=20)
    listening_notes: list[DeliveryNote] = Field(default_factory=list, max_length=20)
