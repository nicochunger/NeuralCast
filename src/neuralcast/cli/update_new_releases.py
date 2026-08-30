"""CLI entrypoint for updating New Releases playlists."""

from __future__ import annotations

from collections.abc import Sequence


def build_arg_parser():
    """Build the New Releases command-line parser."""

    from neuralcast.pipelines.new_releases.main import build_arg_parser as build_parser

    return build_parser()


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


def run() -> int:
    """Compatibility alias for the former console-script entrypoint."""

    return main()


if __name__ == "__main__":
    raise SystemExit(main())
