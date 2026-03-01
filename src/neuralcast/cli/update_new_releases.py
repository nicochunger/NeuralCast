"""CLI entrypoint for updating New Releases playlists."""

from __future__ import annotations

import argparse
import sys

from neuralcast.config import ALLOWED_STATION_SLUGS, DEFAULT_STATION_SLUG


def _build_help_parser() -> argparse.ArgumentParser:
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
        help="Collect and display results without writing the CSV",
    )
    parser.add_argument(
        "--per-artist",
        type=int,
        default=3,
        help="Max tracks to keep per artist (default: 3)",
    )
    parser.add_argument(
        "--min-popularity",
        type=int,
        default=0,
        help="Minimum Spotify popularity (0-100) to keep (default: 0)",
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


def run() -> None:
    if any(flag in sys.argv[1:] for flag in ("-h", "--help")):
        _build_help_parser().parse_args()
        return

    from neuralcast.pipelines.new_releases import main

    main()


if __name__ == "__main__":
    run()
