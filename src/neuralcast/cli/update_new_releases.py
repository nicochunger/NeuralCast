"""CLI entrypoint for updating New Releases playlists."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from neuralcast.config import ALLOWED_STATION_SLUGS, DEFAULT_STATION_SLUG


def build_arg_parser() -> argparse.ArgumentParser:
    """Build the New Releases command-line parser."""

    parser = argparse.ArgumentParser(
        description="Refresh the New Releases playlist for a station."
    )
    parser.add_argument(
        "-s",
        "--station",
        choices=ALLOWED_STATION_SLUGS,
        default=DEFAULT_STATION_SLUG,
        help="Station slug (default: %(default)s).",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=120,
        help="Lookback window in days for releases (default: 120)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Collect and display results without writing output files",
    )
    parser.add_argument(
        "--per-artist",
        type=int,
        default=3,
        help="Max tracks to keep per artist (default: 3)",
    )
    parser.add_argument(
        "--min-rank",
        "--min-popularity",
        type=int,
        dest="min_rank",
        default=0,
        help="Minimum rank to keep (default: 0)",
    )
    parser.add_argument(
        "--prefer-singles",
        action="store_true",
        help="Prefer singles when ranking candidates (default: off)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show detailed debug output",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    from neuralcast.pipelines.new_releases.runtime import (
        NewReleasesRequest,
        NewReleasesRuntime,
    )

    args = build_arg_parser().parse_args(argv)
    NewReleasesRuntime().run(
        NewReleasesRequest(
            station=args.station,
            days=args.days,
            per_artist=args.per_artist,
            min_rank=args.min_rank,
            prefer_singles=args.prefer_singles,
            dry_run=args.dry_run,
            verbose=args.verbose,
        )
    )
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
