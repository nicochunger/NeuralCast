"""Owner review operations on unpublished episode documents."""

from typing import Any, TYPE_CHECKING
from neuralcast.pipelines.episode_producer.storage import (
    read_json,
    write_json,
    write_text,
)
from neuralcast.pipelines.episode_producer.models import Plan
from neuralcast.pipelines.episode_producer import editorial
from neuralcast.pipelines.episode_producer.manuscript import manuscript_hash

if TYPE_CHECKING:
    from neuralcast.pipelines.episode_producer.workspace import Workspace


def edit_passage(
    workspace: "Workspace", episode_id: str, revision: str, segment_id: str, script: str
) -> dict[str, Any]:
    project = workspace.project(episode_id)
    with project.lock():
        workspace.check(project, revision)
        if workspace.detail(project)["status"] == "published":
            raise ValueError("Published episodes are immutable")
        proposal, brief, common = editorial._context(project, "Owner text correction")
        original = read_json(project.documents / "plan.json")
        plan = Plan.model_validate(original)
        part = next(
            (s for s in plan.segments if s.id == segment_id and s.kind == "commentary"),
            None,
        )
        if part is None:
            raise ValueError("Unknown host passage")
        part.script = script
        timing = editorial.validate_plan(plan, proposal, brief, common["recordings"])
        from neuralcast.pipelines.episode_producer.storage import digest

        directory = (
            project.runtime
            / "revisions"
            / digest({"before": original, "after": plan.model_dump()})
        )
        write_json(directory / "previous_plan.json", original)
        write_json(directory / "final_plan.json", plan.model_dump())
        report_path = project.runtime / "editorial_latest.json"
        report = read_json(report_path) if report_path.exists() else {}
        from neuralcast.pipelines.episode_producer.editorial_models import Edit

        edit = Edit(
            passages=[
                {
                    "segment_id": part.id,
                    "script": part.script,
                    "explanation": "Owner correction",
                }
            ],
            resolutions=[],
            production_notes=report.get("production_notes", []),
        )
        markdown = editorial.reading_markdown(
            proposal,
            plan,
            edit,
            timing,
            manuscript_hash(project, plan),
            report.get("blockers", []),
            project.episode_number,
        )
        write_json(
            directory / "receipt.json",
            {"manuscript_hash": manuscript_hash(project, plan)},
        )
        write_json(project.documents / "plan.json", plan.model_dump())
        write_text(project.documents / "manuscript.md", markdown)
        report.update(
            manuscript_hash=manuscript_hash(project, plan),
            timing=timing,
            artifacts=str(directory),
        )
        write_json(report_path, report)
    return workspace.detail(project)
