"""Focused review assignments and curated examples for editorial coordinators."""

import json

from neuralcast.config import ASSETS_ROOT

EXAMPLE_LIBRARY = ASSETS_ROOT / "stories" / "episode_examples" / "library.json"


def examples(*ids: str) -> list[dict]:
    library = json.loads(EXAMPLE_LIBRARY.read_text(encoding="utf-8"))
    records = {item["id"]: item for item in library["examples"]}
    if len(records) != len(library["examples"]):
        raise ValueError("Duplicate editorial example IDs")
    # Pass the calibration text and its label, without operational source paths.
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


def specialist_tasks(stage: str) -> list[dict]:
    if stage == "review":
        return [
            {
                "reviewer": "factual_support",
                "instructions": "Verify factual claims, sources, chronology, attribution and necessary uncertainty. Open relevant sources to verify doubtful claims. Distinguish fact from interpretation. Review the full manuscript, but do not critique style or rewrite it.",
                "examples": [],
            },
            {
                "reviewer": "narrative_continuity",
                "instructions": "Read the whole episode in listening order, including complete songs. Find repeated explanations, weak causal links, unresolved promises, unclear references, shallow pivotal scenes and songs without a narrative purpose. Do not split the episode into independently written sections or critique facts and voice outside this remit.",
                "examples": examples("lab-name-before-pronoun"),
            },
            {
                "reviewer": "conversational_voice",
                "instructions": "Assess one host telling the story to an interested listener in the configured locale. Identify impersonal presentation prose, missing personality, and forced or repetitive asides, corrections and hesitations. Suggest fitting wording grounded in existing material. Preserve successful loose moments; do not impose a device quota or judge factual research.",
                "examples": examples(
                    "detour-and-return", "tentative-description", "brief-reaction"
                ),
            },
        ]
    if stage == "delivery":
        return [
            {
                "reviewer": "spoken_delivery",
                "instructions": "Review the coordinator's proposed complete delivery rewrite as speech. Find awkward phrasing, mechanical transitions, missing natural reactions/asides, forced corrections and repetitive rhythm. Check immediate paragraphs and song handoffs. Preserve personality and strong material. Suggest specific repairs without new facts or device quotas. Refer only to commentary IDs.",
                "examples": examples(
                    "detour-and-return", "brief-reaction", "connected-refinement"
                ),
            },
            {
                "reviewer": "meaning_preservation",
                "instructions": "Compare the coordinator's proposed delivery rewrite with source_plan and its evidence. Find added claims, omitted qualifications, changed chronology, lost attribution, interpretation presented as fact, and broken narrative connections. A small aside can introduce a factual claim. Do not research or impose stylistic preferences. Refer only to commentary IDs; flag required content/evidence changes instead of inventing repairs.",
                "examples": examples(
                    "explain-the-problem", "voice-attributed-disagreement"
                ),
            },
        ]
    raise ValueError("Unknown specialist stage")


def validate_findings(reviews: list, ids: set[str]) -> None:
    for review in reviews:
        for finding in review.findings:
            if set(finding.segment_ids) - ids:
                raise ValueError("Specialist finding refers to unknown segment IDs")
