"""Read episode job progress from its frozen request, logs and stage receipts."""

from datetime import datetime
from pathlib import Path
import re
from typing import Any, TYPE_CHECKING

from neuralcast.pipelines.episode_producer.storage import read_json

if TYPE_CHECKING:
    from neuralcast.pipelines.episode_producer.workspace import Workspace


def episode_activity(workspace: "Workspace", job: dict[str, Any]) -> dict[str, Any]:
    request_id = (job.get("episode_options") or {}).get("request_id")
    directory = workspace.root / "requests" / str(request_id)
    request = read_json(directory / "request.json")
    action = request["action"]
    episode_id = request.get("episode_id") or (job.get("result") or {}).get(
        "episode_id"
    )
    if action == "develop" and request.get("episode_id"):
        action = "refine_brief"
    def _editorial_steps(jobs: dict[str, Any] | None = None) -> list[tuple[str, str]]:
        stage_labels = [
            ("research", "Web research"),
            ("write", "Draft" if jobs and "research" in jobs else "Research and draft"),
            ("factual_support", "Fact review"),
            ("narrative_continuity", "Story review"),
            ("conversational_voice", "Voice review"),
            ("edit", "Final edit"),
            ("fact_check", "Factual drift check"),
        ]
        if jobs is None:
            return [
                (stage, label)
                for stage, label in stage_labels
                if stage not in {"research", "fact_check"}
            ]
        return [(stage, label) for stage, label in stage_labels if stage in jobs]

    compare_inputs = (request.get("comparison_snapshot") or {}).get("inputs") or {}
    definitions = {
        "ideas": [("topics", "Suggesting topics")],
        "develop": [("concept", "Researching and developing the brief")],
        "refine_brief": [("concept", "Researching and revising the brief")],
        "queue": [("queue", "Approving and queueing the brief")],
        "write": _editorial_steps(),
        "compare": [
            (f"{version}_{stage}", f"Version {version.upper()}: {label}")
            for version in ("a", "b")
            for stage, label in _editorial_steps(
                compare_inputs.get(version, {}).get("jobs")
            )
        ],
        "compare_resolve": [("edit", "Resolving research questions")],
        "compare_render": [
            ("narration", "Generating approved comparison voice"),
            ("mix", "Assembling local listening copy"),
        ],
        "revise": [
            *(
                [("feedback_research", "Focused factual research")]
                if request.get("feedback", {}).get("research")
                else []
            ),
            ("edit", "Applying manuscript feedback"),
        ],
        "render": [
            ("narration", "Generating voice"),
            ("mix", "Assembling audio"),
            ("upload", "Uploading private preview"),
        ],
        "publish": [("publish", "Publishing the episode and RSS item")],
        "weekly": [("weekly", "Preparing the weekly episode workspace")],
    }
    steps = [
        {"id": key, "label": label, "status": "pending"}
        for key, label in definitions[action]
    ]
    statuses = {step["id"]: step for step in steps}
    log = job.get("log_tail") or ""
    for event, name in re.findall(
        r"(Running|Completed|Reusing) editorial job: (\w+)", log
    ):
        if name in statuses:
            statuses[name]["status"] = "running" if event == "Running" else "succeeded"
    if action == "compare":
        for version, event, name in re.findall(
            r"Comparison version ([ab]): (Running|Completed|Reusing) editorial job: (\w+)",
            log,
        ):
            key = f"{version}_{name}"
            if key in statuses:
                statuses[key]["status"] = (
                    "running" if event == "Running" else "succeeded"
                )
    if action in {"render", "compare_render"}:
        events = re.findall(r"Episode production: (narration|mix|upload)", log)
        if events:
            current = [s["id"] for s in steps].index(events[-1])
            for index, step in enumerate(steps):
                step["status"] = (
                    "succeeded"
                    if index < current
                    else "running" if index == current else "pending"
                )

    # Stage files also report progress while a worker's stdout is buffered.
    if action in {"write", "revise"} and episode_id:
        project = workspace.project(episode_id)
        parent = project.runtime / ("editorial" if action == "write" else "revisions")
        candidates = []
        created = datetime.fromisoformat(
            request["created_at"].replace("Z", "+00:00")
        ).timestamp()
        for inputs in parent.glob("*/inputs.json"):
            if inputs.stat().st_mtime < created - 2:
                continue
            saved = read_json(inputs)
            if saved.get("context", {}).get("concept_hash") != request.get(
                "concept_hash"
            ):
                continue
            if action == "revise" and saved.get("feedback") != request.get("feedback"):
                continue
            candidates.append(inputs)
        directory = (
            max(candidates, key=lambda p: p.stat().st_mtime).parent
            if candidates
            else None
        )
    for step in steps:
        step_directory = directory
        stage_id = step["id"]
        if action == "compare" and episode_id:
            from neuralcast.pipelines.episode_producer.storage import digest

            version, stage_id = step["id"].split("_", 1)
            step_directory = (
                workspace.project(episode_id).runtime
                / "comparisons"
                / request["comparison_id"]
                / version
                / "editorial"
                / digest(request["comparison_snapshot"]["inputs"][version])
            )
        if step_directory and (step_directory / f"{stage_id}.json").exists():
            step["status"] = "succeeded"
        elif step_directory and list(
            (step_directory / stage_id).glob("attempt-*/context.json")
        ):
            step["status"] = "running"
        if (
            job["status"] == "failed"
            and step["status"] != "succeeded"
            and step_directory
            and list((step_directory / stage_id).glob("attempt-*/failure.json"))
        ):
            step["status"] = "failed"
    if job["status"] == "succeeded":
        for step in steps:
            step["status"] = "succeeded"
    elif job["status"] == "failed":
        for step in steps:
            if step["status"] == "running":
                step["status"] = "failed"
    current = [s["label"] for s in steps if s["status"] in {"running", "failed"}]
    if job["status"] == "failed":
        current_label = "Failed during " + (
            ", ".join(current) if current else "preparation"
        )
    else:
        current_label = ", ".join(current) or (
            "Finishing and saving"
            if all(s["status"] == "succeeded" for s in steps)
            else "Preparing the operation"
        )
    errors = [
        line
        for line in log.splitlines()
        if line.startswith(
            (
                "Episode workspace failed:",
                "RuntimeError:",
                "ValueError:",
                "[admin-api] runner failure:",
            )
        )
    ]
    return {
        "action": action,
        "episode_id": episode_id,
        "steps": steps,
        "current": current_label,
        "error": errors[-1][:600] if job["status"] == "failed" and errors else None,
    }
