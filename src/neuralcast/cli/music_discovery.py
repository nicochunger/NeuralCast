"""Generate private auditions or apply one frozen owner-reviewed batch."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
import json

from dotenv import load_dotenv

from neuralcast.config import ALLOWED_STATION_SLUGS, DEFAULT_STATION_SLUG, PROJECT_ROOT
from neuralcast.pipelines.music_discovery.service import DiscoveryService


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["generate", "process"])
    parser.add_argument(
        "-s", "--station", choices=ALLOWED_STATION_SLUGS, default=DEFAULT_STATION_SLUG
    )
    parser.add_argument("--scheduled", action="store_true")
    parser.add_argument("--batch-id")
    args = parser.parse_args(argv)
    if args.action == "process" and (not args.batch_id or args.scheduled):
        parser.error("process requires --batch-id and cannot be scheduled")
    if args.action == "generate" and args.batch_id:
        parser.error("generate cannot select a batch")
    load_dotenv(PROJECT_ROOT / ".env")
    service = DiscoveryService(args.station)
    result = (
        service.generate(scheduled=args.scheduled)
        if args.action == "generate"
        else service.process(args.batch_id)
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
