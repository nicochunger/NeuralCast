"""Isolated paired editorial runs and explicitly approved local listening copies."""

from pathlib import Path
import secrets
from typing import Callable

from . import editorial, service
from .manuscript import approve_manuscript, manuscript_hash
from .models import Plan, Music
from .storage import Project, digest, file_hash, identifier, read_json, write_json

VARIANTS = {"a": "hybrid", "b": "gemini"}


class ComparisonProject(Project):
    """Reuse episode validation and rendering with experiment-local documents."""

    def __init__(self, source: Project, directory: Path):
        super().__init__(source.station, source.id, station_root=source.station_root)
        self.runtime = directory

    @property
    def episode_number(self) -> int | None:
        return self.proposal().episode_number

    @property
    def documents(self) -> Path:
        return self.runtime / "documents"


def freeze(source: Project) -> dict:
    """Called after explicit concept approval, before either provider runs."""
    _, _, common = editorial._context(source, "")
    proposal = read_json(source.documents / "proposal.json")
    # Preserve the canonical embedded brief even for imported legacy projects.
    proposal["editorial_brief"] = source.editorial_brief().model_dump()
    voice = source.runtime / "voice_directions.json"
    pipelines = dict(
        zip(
            VARIANTS,
            ("hybrid", "gemini") if secrets.randbelow(2) else ("gemini", "hybrid"),
        )
    )
    return {
        "pipelines": pipelines,
        "proposal": proposal,
        "concept_hash": source.concept_hash(),
        "speech_settings": service.writing_settings(
            service.channel_for(source, source.proposal())
        ),
        "voice": read_json(voice) if voice.exists() else {"instructions": ""},
        "inputs": {
            key: editorial.workshop_inputs(source, pipeline, common=common)
            for key, pipeline in pipelines.items()
        },
    }


def directory(source: Project, comparison_id: str) -> Path:
    return source.runtime / "comparisons" / identifier(comparison_id)


def variant_project(
    source: Project, comparison_id: str, variant: str
) -> ComparisonProject:
    if variant not in VARIANTS:
        raise ValueError("Unknown comparison version")
    root = directory(source, comparison_id)
    manifest = read_json(root / "experiment.json")
    if digest(manifest["snapshot"]) != manifest["snapshot_hash"]:
        raise ValueError("Comparison input snapshot changed")
    p = ComparisonProject(source, root / variant)
    if read_json(p.documents / "proposal.json") != manifest["snapshot"]["proposal"]:
        raise ValueError("Comparison proposal changed")
    if read_json(p.runtime / "voice_directions.json") != manifest["snapshot"]["voice"]:
        raise ValueError("Comparison voice settings changed")
    latest = p.runtime / "editorial_latest.json"
    plan = p.documents / "plan.json"
    if (
        latest.exists()
        and plan.exists()
        and digest(read_json(plan)) != read_json(latest).get("installed_plan_hash")
    ):
        raise ValueError("Saved comparison manuscript changed")
    return p


def run(
    source: Project,
    comparison_id: str,
    snapshot: dict,
    *,
    progress: Callable = lambda _: None,
) -> dict:
    root = directory(source, comparison_id)
    manifest = {"snapshot": snapshot, "snapshot_hash": digest(snapshot)}
    path = root / "experiment.json"
    if path.exists() and read_json(path) != manifest:
        raise ValueError("Comparison inputs changed; create a new comparison")
    if not path.exists():
        for key in VARIANTS:
            p = ComparisonProject(source, root / key)
            write_json(p.documents / "proposal.json", snapshot["proposal"])
            write_json(p.runtime / "voice_directions.json", snapshot["voice"])
            p.approve_proposal(snapshot["concept_hash"])
        write_json(path, manifest)
    errors = []
    for key in VARIANTS:
        p = variant_project(source, comparison_id, key)
        with p.lock():
            progress(f"Comparison version {key}: started")
            try:
                for settings in snapshot["inputs"][key]["jobs"].values():
                    current_model = (
                        editorial.codex.MODEL
                        if settings["provider"] == "codex"
                        else editorial.gemini.MODEL
                    )
                    if settings["model"] != current_model:
                        raise ValueError(
                            "Editorial model changed since comparison; restore it or create a new comparison"
                        )
                editorial.edit_episode(
                    p,
                    frozen_inputs=snapshot["inputs"][key],
                    progress=lambda message: progress(
                        f"Comparison version {key}: {message}"
                    ),
                )
            except (ValueError, RuntimeError, OSError) as exc:
                # Preserve the other arm even when one provider fails.
                write_json(
                    p.runtime / "comparison_failure.json", {"message": str(exc)[:1000]}
                )
                errors.append(key)
                continue
            (p.runtime / "comparison_failure.json").unlink(missing_ok=True)
            progress(f"Comparison version {key}: completed")
    if errors:
        raise RuntimeError(
            f"Comparison version(s) {', '.join(errors)} did not finish; retry resumes saved work"
        )
    return {
        "episode_id": source.id,
        "comparison_id": comparison_id,
        "status": "comparison_review",
    }


def render(
    source: Project,
    comparison_id: str,
    variant: str,
    expected_hash: str,
    *,
    progress: Callable = lambda _: None,
) -> dict:
    p = variant_project(source, comparison_id, variant)
    with p.lock():
        plan = Plan.model_validate(read_json(p.documents / "plan.json"))
        if manuscript_hash(p, plan) != expected_hash:
            raise ValueError("Comparison manuscript changed; review before rendering")
        snapshot = read_json(directory(source, comparison_id) / "experiment.json")[
            "snapshot"
        ]
        current_settings = service.writing_settings(
            service.channel_for(p, p.proposal())
        )
        if current_settings != snapshot["speech_settings"]:
            raise ValueError(
                "Speech configuration changed since comparison; restore it or create a new comparison"
            )
        recordings = snapshot["inputs"][variant]["context"]["recordings"]
        for segment in plan.segments:
            if isinstance(segment, Music):
                current = service.resolve_recording(p.station_root, segment)
                frozen = recordings[segment.id]
                if any(
                    current[key] != frozen[key]
                    for key in ("sha256", "artist", "title", "album", "year")
                ):
                    raise ValueError(
                        "A comparison recording changed; restore it or create a new comparison"
                    )
        approve_manuscript(p, expected_hash)
        receipt = service.prepare(p, None, local_only=True, progress=progress)
    return {
        "episode_id": source.id,
        "comparison_id": comparison_id,
        "variant": variant,
        "status": "comparison_audio_review",
        "render_hash": receipt["render_hash"],
    }


def audio(source: Project, comparison_id: str, variant: str, revision: str) -> Path:
    if len(revision) != 64 or any(c not in "0123456789abcdef" for c in revision):
        raise ValueError("Invalid audio revision")
    p = variant_project(source, comparison_id, variant)
    receipt = read_json(p.runtime / revision / "production.json")
    path = p.runtime / revision / "episode.mp3"
    if (
        receipt["revision"] != revision
        or receipt["station"] != source.station
        or receipt["episode_id"] != source.id
        or file_hash(path) != receipt["render_hash"]
    ):
        raise ValueError("Comparison audio changed")
    return path


def view(source: Project, comparison_id: str) -> dict:
    root = directory(source, comparison_id)
    manifest = read_json(root / "experiment.json")
    versions = []
    for key in VARIANTS:
        p = variant_project(source, comparison_id, key)
        latest = p.runtime / "editorial_latest.json"
        report = read_json(latest) if latest.exists() else {}
        artifact = p.runtime / "editorial" / digest(manifest["snapshot"]["inputs"][key])
        plan_path = p.documents / "plan.json"
        plan = Plan.model_validate(read_json(plan_path)) if plan_path.exists() else None
        draft_path = artifact / "write.json"
        draft = read_json(draft_path)["output"] if draft_path.exists() else {}
        audio_latest = p.runtime / "latest.json"
        production = None
        if audio_latest.exists():
            revision = read_json(audio_latest)["revision"]
            production = read_json(p.runtime / revision / "production.json")
        metrics = report.get("jobs", {})
        # Include failed/in-progress attempts before a final editorial receipt exists.
        if not metrics:
            metrics = {
                str(path.relative_to(p.runtime)): read_json(path)
                for path in (p.runtime / "editorial").glob("*/*/attempt-*/metrics.json")
            }
        findings = artifact / "findings.json"
        edit = artifact / "edit.json"
        failure = p.runtime / "comparison_failure.json"
        versions.append(
            {
                "id": key,
                "label": f"Version {key.upper()}",
                "status": report.get(
                    "status", "failed" if failure.exists() else "pending"
                ),
                "failure": read_json(failure)["message"] if failure.exists() else None,
                "manuscript_hash": manuscript_hash(p, plan) if plan else None,
                "segments": [s.model_dump() for s in plan.segments] if plan else [],
                "draft_segments": draft.get("plan", {}).get("segments", []),
                "research_notes": draft.get("research_notes", ""),
                "findings": (
                    read_json(findings).get("findings", []) if findings.exists() else []
                ),
                "changes": (
                    read_json(edit)["output"].get("passages", [])
                    if edit.exists()
                    else []
                ),
                "blockers": report.get("blockers", []),
                "timing": report.get("timing"),
                "production_notes": report.get("production_notes", []),
                "listening_notes": report.get("listening_notes", []),
                "metrics": metrics,
                "elapsed_seconds": sum(
                    m.get("elapsed_seconds", 0) for m in metrics.values()
                ),
                "attempts": len(metrics),
                "audio": (
                    {
                        k: production[k]
                        for k in ("revision", "render_hash", "duration", "chapters")
                    }
                    if production
                    else None
                ),
            }
        )
    return {
        "id": comparison_id,
        "concept_hash": manifest["snapshot"]["concept_hash"],
        "current_concept": manifest["snapshot"]["concept_hash"]
        == source.concept_hash(),
        "versions": versions,
        "pipelines": manifest["snapshot"]["pipelines"],
    }


def list_comparisons(source: Project) -> list[dict]:
    return [
        {"id": p.parent.name}
        for p in sorted(
            (source.runtime / "comparisons").glob("*/experiment.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
    ]
