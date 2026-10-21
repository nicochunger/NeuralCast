"""Manual podcast workflow: approve a proposal, prepare for review, then publish."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from collections.abc import Sequence

from dotenv import load_dotenv
from neuralcast.config import DEFAULT_STATION_SLUG, ALLOWED_STATION_SLUGS, PROJECT_ROOT
from neuralcast.pipelines.episode_producer.models import Plan, Proposal
from neuralcast.pipelines.episode_producer.editorial_models import EditorialBrief
from neuralcast.pipelines.episode_producer.editorial import edit_episode
from neuralcast.pipelines.episode_producer.manuscript import (
    approve_manuscript,
    manuscript_hash,
    validate_manuscript,
)
from neuralcast.pipelines.episode_producer.storage import (
    Project,
    digest,
    read_json,
    write_json,
)
from neuralcast.pipelines.episode_producer.service import (
    channel_for,
    prepare,
    validated_inputs,
)
from neuralcast.pipelines.host_orchestrator.transport import AzuraCastClient
from neuralcast.services.azuracast_config import load_azuracast_settings
from neuralcast.pipelines.episode_producer.publication import publish
from neuralcast.pipelines.episode_producer.narration import SUPPORTED_TTS_MODELS


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=[
            "init",
            "inspect",
            "approve-proposal",
            "editorial",
            "approve-manuscript",
            "validate",
            "prepare",
            "publish",
        ],
    )
    parser.add_argument(
        "--station", "-s", choices=ALLOWED_STATION_SLUGS, default=DEFAULT_STATION_SLUG
    )
    parser.add_argument("--episode", required=True)
    parser.add_argument(
        "--proposal", type=Path, help="Initial proposal JSON; init only"
    )
    parser.add_argument(
        "--brief",
        type=Path,
        help="Editorial concept/song/research brief JSON; init only",
    )
    parser.add_argument(
        "--feedback", default="", help="Owner revision instructions; editorial only"
    )
    parser.add_argument(
        "--hash",
        help="Exact concept, manuscript, or rendered-audio hash being approved",
    )
    parser.add_argument(
        "--description", help="Listener-facing episode description; publish only"
    )
    parser.add_argument(
        "--tts-model",
        choices=SUPPORTED_TTS_MODELS,
        help="Gemini speech model for prepare/validate only; defaults to GEMINI_TTS_MODEL or gemini-3.8-flash-lite-tts",
    )
    args = parser.parse_args(argv)
    if args.tts_model and args.action not in {"prepare", "validate"}:
        parser.error("--tts-model is supported only for prepare and validate")
    project = Project(args.station, args.episode)
    try:
        if args.action == "inspect":
            proposal = project.proposal()
            result = {
                "proposal": proposal.model_dump(),
                "proposal_hash": digest(proposal.model_dump()),
                "concept_hash": project.concept_hash(),
                "documents": str(project.documents),
            }
            plan_path = project.documents / "plan.json"
            if plan_path.exists():
                plan = Plan.model_validate(read_json(plan_path))
                result["manuscript_hash"] = manuscript_hash(project, plan)
            editorial_path = project.runtime / "editorial_latest.json"
            if editorial_path.exists():
                result["editorial"] = read_json(editorial_path)
            latest = project.runtime / "latest.json"
            if latest.exists():
                result["latest"] = read_json(latest)
                revision = result["latest"]["revision"]
                if len(revision) != 64 or any(
                    char not in "0123456789abcdef" for char in revision
                ):
                    raise ValueError("Invalid stored revision")
                production = read_json(project.runtime / revision / "production.json")
                result["production"] = {
                    key: production.get(key)
                    for key in (
                        "duration",
                        "uploaded",
                        "queue",
                        "broadcast_title",
                        "tts",
                    )
                }
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        with project.lock():
            if args.action == "init":
                if not args.proposal:
                    raise ValueError("init requires --proposal")
                proposal = Proposal.model_validate(read_json(args.proposal))
                if proposal.episode_id != project.id:
                    raise ValueError("Proposal ID does not match --episode")
                channel_for(project, proposal)
                if (project.documents / "proposal.json").exists():
                    raise ValueError(
                        "Project already exists; edit its proposal instead"
                    )
                brief = None
                if args.brief:
                    brief = EditorialBrief.model_validate(read_json(args.brief))
                    if brief.proposal_hash != digest(proposal.model_dump()):
                        raise ValueError("Brief references a different proposal")
                write_json(project.documents / "proposal.json", proposal.model_dump())
                if brief:
                    write_json(project.documents / "brief.json", brief.model_dump())
                result = {
                    "proposal_hash": digest(proposal.model_dump()),
                    "concept_hash": project.concept_hash(),
                    "documents": str(project.documents),
                }
            elif args.action == "approve-proposal":
                if not args.hash:
                    raise ValueError(
                        "Approval requires --hash of the reviewed concept from inspect"
                    )
                project.approve_proposal(args.hash)
                result = {"approved_concept": args.hash}
            elif args.action == "editorial":
                result = edit_episode(
                    project,
                    feedback=args.feedback,
                    progress=lambda message: print(
                        message, file=sys.stderr, flush=True
                    ),
                )
            elif args.action == "approve-manuscript":
                if not args.hash:
                    raise ValueError(
                        "Manuscript approval requires --hash from inspect/editorial"
                    )
                result = approve_manuscript(project, args.hash)
            elif args.action == "validate":
                _, plan, _, inputs, revision = validated_inputs(
                    project, tts_model=args.tts_model
                )
                validate_manuscript(plan)
                result = {
                    "valid": True,
                    "revision": revision,
                    "tts_model": inputs["settings"]["tts_model"],
                }
            elif args.action == "publish":
                if not args.hash:
                    raise ValueError(
                        "Publication requires --hash of the reviewed render"
                    )
                result = publish(project, args.hash, args.description)
            else:
                load_dotenv(PROJECT_ROOT / ".env")
                settings = load_azuracast_settings(station=args.station)
                client = AzuraCastClient(settings.base_url, settings.api_key)
                receipt = prepare(project, client, tts_model=args.tts_model)
                result = {
                    "revision": receipt["revision"],
                    "render_hash": receipt["render_hash"],
                    "duration_seconds": receipt["duration"],
                    "uploaded": receipt["uploaded"],
                    "tts": receipt.get("tts"),
                }
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ValueError, OSError, RuntimeError) as exc:
        parser.exit(1, f"Episode operation failed: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
