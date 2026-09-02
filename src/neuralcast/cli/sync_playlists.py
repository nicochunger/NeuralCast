"""CLI entrypoint for playlist synchronization."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from neuralcast.config import ALLOWED_STATION_SLUGS, DEFAULT_STATION_SLUG


def build_arg_parser() -> argparse.ArgumentParser:
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
        help=(
            "Preview validation, metadata, playlist, and media changes without "
            "writing them."
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    from neuralcast.pipelines.station_sync import main

    main(args.station, args.dry_run)
    return 0


def run() -> int:
    """Compatibility alias for the former console-script entrypoint."""

    return main()


if __name__ == "__main__":
    raise SystemExit(main())
