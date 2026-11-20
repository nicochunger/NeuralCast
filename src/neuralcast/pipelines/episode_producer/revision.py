"""Apply frozen owner feedback to the current manuscript and its evidence."""

import json
from typing import Callable
from neuralcast.services import editorial_agent as codex, editorial_text as gemini
from . import editorial
from .editorial_models import Review, Edit
from .models import Plan
from .storage import Project, read_json, digest, write_json


def revise_manuscript(
    project: Project,
    feedback: dict,
    *,
    pipeline: str = "hybrid",
    source_plan: dict | None = None,
    source_editorial: dict | None = None,
    progress: Callable[[str], None] = print,
) -> dict:
    proposal, brief, common = editorial._context(
        project, json.dumps(feedback, ensure_ascii=False)
    )
    source = Plan.model_validate(
        source_plan or read_json(project.documents / "plan.json")
    )
    timing = editorial.validate_plan(source, proposal, brief, common["recordings"])
    latest_path = project.runtime / "editorial_latest.json"
    latest = (
        source_editorial
        if source_editorial is not None
        else read_json(latest_path) if latest_path.exists() else {}
    )
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
    research_prompt = (editorial.PROMPTS / "feedback_research.md").read_text()
    if pipeline == "gemini":
        research_prompt = research_prompt.replace(
            "No file edits, agents, audio, uploads or publication.",
            "Use Google Search and URL Context to verify claims against primary sources. No audio, uploads or publication.",
        )
    prompt = (editorial.PROMPTS / "edit.md").read_text()
    jobs = editorial.job_settings(pipeline)
    inputs = {
        "context": common,
        "source_plan": source.model_dump(),
        "feedback": feedback,
        "blockers": findings,
        "prompt": prompt,
        "research_prompt": research_prompt,
        "jobs": jobs,
        "schema": codex.output_schema(Edit),
    }
    directory = project.runtime / "revisions" / digest(inputs)
    editorial._snapshot(project, directory, inputs)
    evidence = []
    if feedback.get("research"):
        context = {
            **common,
            "source_plan": source.model_dump(),
            "owner_comments": feedback,
            "open_questions": latest.get("blockers", []),
        }
        research_settings = jobs["factual_support"]
        research_runner = (
            gemini.run_stage
            if research_settings["provider"] == "gemini"
            else codex.run_stage
        )

        def validate(report):
            ids = {p.id for p in source.segments}
            if any(set(f.segment_ids) - ids for f in report.findings):
                raise ValueError("Research refers to an unknown passage")

        report = editorial._job(
            directory,
            "feedback_research",
            context,
            Review,
            research_settings,
            research_prompt,
            research_runner,
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
        latest.get("production_notes", []),
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
        jobs["edit"],
        gemini.run_stage,
        progress,
    )
