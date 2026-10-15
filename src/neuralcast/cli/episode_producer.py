"""Manual episode workflow. Only approve-and-queue submits a live request."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from collections.abc import Sequence

from dotenv import load_dotenv
from neuralcast.config import DEFAULT_STATION_SLUG, ALLOWED_STATION_SLUGS, PROJECT_ROOT
from neuralcast.pipelines.episode_producer.models import Proposal
from neuralcast.pipelines.episode_producer.storage import (
    Project,
    digest,
    read_json,
    write_json,
)
from neuralcast.pipelines.episode_producer.service import (
    channel_for,
    prepare,
    approve_and_queue,
    validated_inputs,
)
from neuralcast.pipelines.host_orchestrator.transport import AzuraCastClient
from neuralcast.services.azuracast_config import load_azuracast_settings
from neuralcast.pipelines.episode_producer.publication import publish


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=[
            "init",
            "inspect",
            "approve-proposal",
            "validate",
            "prepare",
            "approve-and-queue",
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
        "--hash", help="Exact proposal hash or rendered-audio hash being approved"
    )
    parser.add_argument(
        "--description", help="Listener-facing episode description; publish only"
    )
    args = parser.parse_args(argv)
    project = Project(args.station, args.episode)
    try:
        if args.action == "inspect":
            proposal = project.proposal()
            result = {
                "proposal": proposal.model_dump(),
                "proposal_hash": digest(proposal.model_dump()),
                "documents": str(project.documents),
            }
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
                    for key in ("duration", "uploaded", "queue", "broadcast_title")
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
                write_json(project.documents / "proposal.json", proposal.model_dump())
                result = {
                    "proposal_hash": digest(proposal.model_dump()),
                    "documents": str(project.documents),
                }
            elif args.action == "approve-proposal":
                if not args.hash:
                    raise ValueError(
                        "Approval requires --hash of the reviewed proposal"
                    )
                project.approve_proposal(args.hash)
                result = {"approved_proposal": args.hash}
            elif args.action == "validate":
                _, _, _, _, revision = validated_inputs(project)
                result = {"valid": True, "revision": revision}
            elif args.action == "publish":
                if not args.hash:
                    raise ValueError(
                        "Publication requires --hash of the reviewed render"
                    )
                result = publish(project, args.hash, args.description)
            else:
                if args.action == "approve-and-queue" and not args.hash:
                    raise ValueError(
                        "Final airing approval requires --hash of the previewed render"
                    )
                load_dotenv(PROJECT_ROOT / ".env")
                settings = load_azuracast_settings(station=args.station)
                client = AzuraCastClient(settings.base_url, settings.api_key)
                receipt = (
                    prepare(project, client)
                    if args.action == "prepare"
                    else approve_and_queue(project, client, args.hash)
                )
                result = {
                    "revision": receipt["revision"],
                    "render_hash": receipt["render_hash"],
                    "duration_seconds": receipt["duration"],
                    "uploaded": receipt["uploaded"],
                    "queue": receipt["queue"],
                }
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ValueError, OSError, RuntimeError) as exc:
        parser.exit(1, f"Episode operation failed: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
