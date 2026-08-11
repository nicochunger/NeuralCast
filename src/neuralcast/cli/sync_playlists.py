"""CLI entrypoint for playlist synchronization."""

from __future__ import annotations

import argparse

from neuralcast.config import ALLOWED_STATION_SLUGS, DEFAULT_STATION_SLUG


def run() -> None:
    parser = argparse.ArgumentParser(
        description="AI-assisted local-network radio pipeline."
    )
    parser.add_argument(
        "-s",
        "--station",
        type=str,
        choices=ALLOWED_STATION_SLUGS,
        default=DEFAULT_STATION_SLUG,
        help="Station slug (default: %(default)s).",
    )
    parser.add_argument(
        "-n",
        "--dry-run",
        action="store_true",
        help="Dry run: validate and re-tag existing MP3s, but skip new downloads.",
    )
    args = parser.parse_args()
    from neuralcast.pipelines.playlist_sync import main

    main(args.station, args.dry_run)


if __name__ == "__main__":
    run()
