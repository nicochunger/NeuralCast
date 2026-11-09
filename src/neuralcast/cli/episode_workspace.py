"""Execute saved episode admin requests or weekly approved-brief preparation."""

import argparse
from collections.abc import Sequence
import json
from dotenv import load_dotenv
from neuralcast.config import PROJECT_ROOT, ALLOWED_STATION_SLUGS
from neuralcast.pipelines.episode_producer.workspace import Workspace


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["execute", "weekly"])
    parser.add_argument("-s", "--station", choices=ALLOWED_STATION_SLUGS, required=True)
    parser.add_argument("--request-id")
    args = parser.parse_args(argv)
    load_dotenv(PROJECT_ROOT / ".env")
    workspace = Workspace(args.station)
    if args.action == "execute" and not args.request_id:
        parser.error("execute requires --request-id")
    try:
        if args.action == "weekly":
            if not workspace.state()["weekly_enabled"]:
                result = {"skipped": "Weekly preparation is disabled"}
            else:
                import uuid
                from neuralcast.admin_api.jobs import JobManager
                from neuralcast.pipelines.episode_producer.storage import write_json

                request_id = uuid.uuid4().hex
                write_json(
                    workspace.root / "requests" / request_id / "request.json",
                    {"id": request_id, "action": "weekly"},
                )
                job = JobManager().enqueue_episode(
                    station=args.station, request_id=request_id
                )
                result = {"job_id": job.job_id}
        else:
            result = workspace.execute(args.request_id)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ValueError, OSError, RuntimeError) as exc:
        parser.exit(1, f"Episode workspace failed: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
