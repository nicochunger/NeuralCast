"""Apply final narration edits within their evidence and structural boundaries."""

from collections.abc import Sequence

from .editorial_models import Edit
from .models import Commentary, Plan


def apply_edit(
    plan: Plan, edit: Edit, *, script_only: bool = False, evidence: Sequence[dict] = ()
) -> Plan:
    commentaries = {
        part.id: part for part in plan.segments if isinstance(part, Commentary)
    }
    ids = [part.segment_id for part in edit.passages]
    if len(ids) != len(set(ids)) or set(ids) != set(commentaries):
        raise ValueError("Final edit must cover exactly the existing commentary IDs")
    if any(note.segment_id not in commentaries for note in edit.listening_notes):
        raise ValueError("Listening note refers to an unknown commentary ID")
    allowed_urls = {
        source.url for part in commentaries.values() for source in part.sources
    } | {source["url"] for source in evidence}
    result = plan.model_copy(deep=True)
    passages = {part.segment_id: part for part in edit.passages}
    for part in result.segments:
        if not isinstance(part, Commentary):
            continue
        change = passages[part.id]
        if script_only and (
            (change.companion is not None and change.companion != part.companion)
            or (change.sources is not None and change.sources != part.sources)
        ):
            raise ValueError("Delivery cannot change companions or sources")
        if change.sources is not None:
            if {source.url for source in change.sources} - allowed_urls:
                raise ValueError(
                    "Final edit introduced a source outside the supplied evidence"
                )
            part.sources = change.sources
        if change.companion is not None:
            part.companion = change.companion
        part.script = change.script
    return Plan.model_validate(result.model_dump())


def required_finding_ids(findings: list[dict]) -> set[str]:
    return {
        item["id"]
        for item in findings
        if item.get("severity") == "major"
        or item.get("reviewer") in {"research", "editorial", "feedback_research"}
    }


def edit_blockers(edit: Edit, findings: list[dict]) -> list[str]:
    known = {item["id"] for item in findings}
    ids = [item.finding_id for item in edit.resolutions]
    if len(set(ids)) != len(ids) or set(ids) - known:
        raise ValueError("Final edit resolutions contain duplicate or unknown finding IDs")
    missing = required_finding_ids(findings) - set(ids)
    if missing:
        raise ValueError(
            "Final edit must resolve every required finding: "
            + ", ".join(sorted(missing))
        )
    return list(
        dict.fromkeys(
            [
                *edit.open_questions,
                *[
                    f"{item.finding_id}: {item.explanation}"
                    for item in edit.resolutions
                    if item.status == "unresolved"
                ],
            ]
        )
    )


def edit_report(
    before: Plan,
    after: Plan,
    edit: Edit,
    findings: list[dict],
    fact_check_findings: Sequence[dict] = (),
) -> str:
    original = {part.id: part for part in before.segments}
    explanations = {part.segment_id: part.explanation for part in edit.passages}
    lines = ["# Manuscript edit — before and after", ""]
    for part in after.segments:
        if isinstance(part, Commentary) and part != original[part.id]:
            lines += [
                f"## {part.id} — {part.companion.title}",
                "",
                explanations[part.id],
                "",
                "Before:",
                "",
                "```text",
                original[part.id].script,
                "```",
                "",
                "After:",
                "",
                "```text",
                part.script,
                "```",
                "",
            ]
    lines += ["## Review findings", ""]
    resolutions = {item.finding_id: item for item in edit.resolutions}
    for finding in findings:
        resolution = resolutions.get(finding["id"])
        status = resolution.status if resolution else "noted"
        explanation = resolution.explanation if resolution else "Optional minor note."
        lines += [
            f"### {finding['id']} — {status}",
            "",
            finding["problem"],
            "",
            f"Quote: {finding['quote']}",
            "",
            explanation,
            "",
        ]
    if fact_check_findings:
        lines += ["## Post-edit factual check", ""]
        for index, finding in enumerate(fact_check_findings, 1):
            lines += [
                f"### fact_check-{index} — {finding['severity']}",
                "",
                finding["problem"],
                "",
                f"Quote: {finding['quote']}",
                "",
                f"Suggested correction: {finding['correction']}",
                "",
            ]
    return "\n".join(lines) + "\n"
