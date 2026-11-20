"""Unified manuscript revision and delivery-only polish path."""

import json
import re
from typing import Callable

from neuralcast.services import editorial_agy as agy
from . import editorial
from .editorial_models import Edit, Review
from .models import Plan
from .storage import Project, digest, read_json, write_json


def revise_manuscript(
    project: Project,
    feedback: dict | str,
    *,
    script_only: bool = False,
    source_plan: dict | None = None,
    source_editorial: dict | None = None,
    runner: Callable = agy.run_stage,
    progress: Callable[[str], None] = print,
) -> dict:
    feedback_text = (
        feedback if isinstance(feedback, str) else json.dumps(feedback, ensure_ascii=False)
    )
    proposal, brief, common = editorial._context(project, feedback_text)
    latest_path = project.runtime / "editorial_latest.json"
    latest = (
        source_editorial
        if source_editorial is not None
        else read_json(latest_path) if latest_path.exists() else {}
    )
    if script_only and latest.get("blockers"):
        raise ValueError(
            "Resolve existing editorial blockers before a delivery-only pass"
        )
    source = Plan.model_validate(
        source_plan or read_json(project.documents / "plan.json")
    )
    prompt = (editorial.PROMPTS / "edit.md").read_text(encoding="utf-8")
    jobs = editorial.job_settings()
    settings = jobs["edit"]
    production_notes = latest.get("production_notes", [])

    if script_only:
        identity = {
            "prompt": prompt,
            "job": settings,
            "schema": agy.output_schema(Edit),
        }
        if latest.get("mode") == "delivery" and latest.get(
            "installed_plan_hash"
        ) == digest(source.model_dump()):
            if not re.fullmatch(r"[a-f0-9]{64}", latest.get("run_hash", "")):
                raise ValueError("Invalid stored delivery run")
            saved_path = (
                project.runtime / "delivery" / latest["run_hash"] / "inputs.json"
            )
            saved = read_json(saved_path)
            if digest(saved) != latest["run_hash"]:
                raise ValueError("Stored delivery inputs changed")
            if {k: v for k, v in saved.items() if k != "context"} == identity and {
                k: saved["context"].get(k) for k in common
            } == common:
                source = Plan.model_validate(saved["context"]["source_plan"])
                production_notes = saved["context"]["production_notes"]
        timing = editorial.validate_plan(source, proposal, brief, common["recordings"])
        context = editorial._edit_context(
            common,
            source,
            timing,
            [],
            [],
            {},
            production_notes,
            script_only=True,
        )
        inputs = {**identity, "context": context}
        directory = project.runtime / "delivery" / digest(inputs)
        editorial._snapshot(project, directory, inputs)
        receipt = editorial._final_edit(
            project,
            common,
            proposal,
            brief,
            directory,
            context,
            prompt,
            settings,
            runner,
            progress,
        )
        write_json(project.runtime / "delivery_latest.json", receipt)
        return receipt

    timing = editorial.validate_plan(source, proposal, brief, common["recordings"])
    findings = [
        dict(
            id=f"open-{i}",
            reviewer="editorial",
            segment_ids=[],
            quote=question,
            severity="major",
            problem=question,
            correction="Resolve with evidence, accurate attribution, qualification or removal of unsupported claims. Move routine recording checks to production notes after neutralizing edition-dependent narration.",
        )
        for i, question in enumerate(latest.get("blockers", []), 1)
    ]
    research_prompt = (editorial.PROMPTS / "feedback_research.md").read_text(
        encoding="utf-8"
    )
    inputs = {
        "context": common,
        "source_plan": source.model_dump(),
        "feedback": feedback,
        "blockers": findings,
        "prompt": prompt,
        "research_prompt": research_prompt,
        "jobs": jobs,
        "schema": agy.output_schema(Edit),
    }
    directory = project.runtime / "revisions" / digest(inputs)
    editorial._snapshot(project, directory, inputs)
    evidence = []
    if isinstance(feedback, dict) and feedback.get("research"):
        research_context = {
            **common,
            "source_plan": source.model_dump(),
            "owner_comments": feedback,
            "open_questions": latest.get("blockers", []),
        }
        research_settings = {**jobs["research"], "effort": "medium", "model": agy.model_for("medium")}

        def validate(report: Review) -> None:
            ids = {p.id for p in source.segments}
            if any(set(f.segment_ids) - ids for f in report.findings):
                raise ValueError("Research refers to an unknown passage")

        report = editorial._job(
            directory,
            "feedback_research",
            research_context,
            Review,
            research_settings,
            research_prompt,
            runner,
            validate,
            progress,
        )
        evidence = [s.model_dump() for s in report.additional_sources]
        findings += [
            {"id": f"research-{i}", "reviewer": "feedback_research", **f.model_dump()}
            for i, f in enumerate(report.findings, 1)
        ]
    write_json(directory / "findings.json", {"findings": findings})
    context = editorial._edit_context(
        common,
        source,
        timing,
        findings,
        evidence,
        {},
        production_notes,
        script_only=False,
    )
    return editorial._final_edit(
        project,
        common,
        proposal,
        brief,
        directory,
        context,
        prompt,
        settings,
        runner,
        progress,
    )
