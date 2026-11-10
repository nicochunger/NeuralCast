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
    definitions = {
        "ideas": [("topics", "Suggesting topics")],
        "develop": [("concept", "Researching and developing the brief")],
        "refine_brief": [("concept", "Researching and revising the brief")],
        "queue": [("queue", "Approving and queueing the brief")],
        "write": [
            ("write", "Research and draft"),
            ("factual_support", "Fact review"),
            ("narrative_continuity", "Story review"),
            ("conversational_voice", "Voice review"),
            ("edit", "Final edit"),
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
    if action == "render":
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
        if directory and (directory / f"{step['id']}.json").exists():
            step["status"] = "succeeded"
        elif directory and list(
            (directory / step["id"]).glob("attempt-*/context.json")
        ):
            step["status"] = "running"
        if (
            job["status"] == "failed"
            and step["status"] != "succeeded"
            and directory
            and list((directory / step["id"]).glob("attempt-*/failure.json"))
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
