"""Focused review assignments, finding identities and curated voice examples."""

import json

from neuralcast.config import ASSETS_ROOT
from .editorial_models import Draft, Review

EXAMPLE_LIBRARY = ASSETS_ROOT / "stories" / "episode_examples" / "library.json"
ROLES = ("factual_support", "narrative_continuity", "conversational_voice")


def examples(*ids: str) -> list[dict]:
    library = json.loads(EXAMPLE_LIBRARY.read_text(encoding="utf-8"))
    records = {item["id"]: item for item in library["examples"]}
    if len(records) != len(library["examples"]):
        raise ValueError("Duplicate editorial example IDs")
    return [
        {
            key: records[id_][key]
            for key in (
                "id",
                "category",
                "status",
                "before",
                "after",
                "reason",
                "check",
            )
        }
        for id_ in ids
    ]


def finding_ledger(draft: Draft, reports: dict[str, Review]) -> list[dict]:
    if set(reports) != set(ROLES):
        raise ValueError("All three focused reviews are required")
    ids = {part.id for part in draft.plan.segments}
    findings = []
    for role in ROLES:
        for index, finding in enumerate(reports[role].findings, 1):
            if set(finding.segment_ids) - ids:
                raise ValueError("Review refers to unknown manuscript segment IDs")
            findings.append(
                {"id": f"{role}-{index}", "reviewer": role, **finding.model_dump()}
            )
    for index, question in enumerate(draft.open_questions, 1):
        findings.append(
            {
                "id": f"research-question-{index}",
                "reviewer": "research",
                "segment_ids": [
                    part.id for part in draft.plan.segments if part.kind == "commentary"
                ],
                "quote": question,
                "severity": "major",
                "problem": question,
                "correction": "Resolve using the supplied evidence, or leave explicitly unresolved.",
            }
        )
    return findings
