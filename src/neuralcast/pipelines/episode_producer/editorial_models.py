"""Contracts for the approved concept and three manuscript editorial stages."""

from typing import Annotated, Literal

from pydantic import Field, model_validator

from .models import Document, Identifier, Music, Plan, Source, Text

SHA256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


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
    open_questions: list[Text] = Field(default_factory=list, max_length=30)


class EditorialIssue(Document):
    id: Identifier
    segment_ids: list[Identifier] = Field(min_length=1, max_length=40)
    severity: Literal["major", "minor"]
    category: Literal[
        "continuity", "depth", "song_placement", "delivery", "accuracy", "pacing"
    ]
    problem: Text
    correction: Text
    acceptance: Text


class Review(Document):
    strengths: list[Text] = Field(min_length=1, max_length=20)
    issues: list[EditorialIssue] = Field(default_factory=list, max_length=40)
    additional_sources: list[Source] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def unique_issues(self) -> "Review":
        ids = [issue.id for issue in self.issues]
        if len(set(ids)) != len(ids):
            raise ValueError("Review issue IDs must be unique")
        return self


class Resolution(Document):
    issue_id: Identifier
    status: Literal["resolved", "unresolved"]
    explanation: Text


class Revision(Draft):
    changes: list[Text] = Field(default_factory=list, max_length=40)
    resolutions: list[Resolution] = Field(default_factory=list, max_length=40)
