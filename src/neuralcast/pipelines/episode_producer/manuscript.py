"""Complete spoken-manuscript validation and exact human approval."""

import re

from neuralcast.pipelines.host_orchestrator.speech import validate_speech_transcript

from .models import Commentary, Music, Plan
from .storage import Project, digest, read_json, write_json


def validate_manuscript(plan: Plan) -> int:
    """Return spoken words, rejecting incomplete or unusable TTS scripts."""
    words = 0
    for index, segment in enumerate(plan.segments):
        if index % 2:
            if not isinstance(segment, Music):
                raise ValueError(
                    "Use one continuous host passage per music gap; alternate commentary and music"
                )
        elif not isinstance(segment, Commentary):
            raise ValueError("Use one continuous host passage between songs")
        if not isinstance(segment, Commentary):
            continue
        if 0 < index < len(plan.segments) - 1 and segment.role != "bridge":
            raise ValueError("Interior host passages must be bridges")
        if not segment.script or not segment.companion:
            raise ValueError(
                f"Complete manuscript required: {segment.id} has no script"
            )
        if len(segment.companion.title) > 40:
            raise ValueError("Host chapter titles must be at most 40 characters")
        speech = validate_speech_transcript(segment.script)
        count = len(speech.plain_text.split())
        if count > segment.max_words:
            raise ValueError(f"Commentary {segment.id} exceeds its word budget")
        if re.search(r"https?://|www\.", speech.plain_text, re.I):
            raise ValueError("Source URLs must not appear in spoken narration")
        words += count
    return words


def manuscript_hash(project: Project, plan: Plan) -> str:
    return digest(
        {
            "concept_hash": project.concept_hash(),
            "proposal": project.proposal().model_dump(),
            "plan": plan.model_dump(),
        }
    )


def approve_manuscript(project: Project, expected_hash: str) -> dict:
    """Record approval already explicitly given by the owner; never infer it."""
    from .service import validated_inputs

    _, plan, _, _, _ = validated_inputs(project)
    validate_manuscript(plan)
    actual = manuscript_hash(project, plan)
    if expected_hash != actual:
        raise ValueError(
            "Manuscript changed; review the current manuscript before approving"
        )
    require_no_blockers(project, actual)
    receipt = {"manuscript_hash": actual}
    write_json(project.runtime / "manuscript_approval.json", receipt)
    return receipt


def require_no_blockers(project: Project, actual: str) -> None:
    latest = project.runtime / "editorial_latest.json"
    if latest.exists():
        status = read_json(latest)
        if status.get("manuscript_hash") == actual and status.get("blockers"):
            raise ValueError(
                "Manuscript has unresolved editorial blockers; revise first"
            )


def require_manuscript_approval(project: Project, plan: Plan) -> None:
    validate_manuscript(plan)
    require_no_blockers(project, manuscript_hash(project, plan))
    path = project.runtime / "manuscript_approval.json"
    if not path.exists() or read_json(path).get("manuscript_hash") != manuscript_hash(
        project, plan
    ):
        raise ValueError(
            "Current manuscript has not been approved; use approve-manuscript after owner review"
        )
