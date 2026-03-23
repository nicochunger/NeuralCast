"""CLI entrypoint for updating New Releases playlists."""

from __future__ import annotations

import sys


def run() -> None:
    if any(flag in sys.argv[1:] for flag in ("-h", "--help")):
        from neuralcast.pipelines.new_releases.main import build_arg_parser

        build_arg_parser().parse_args()
        return

    from neuralcast.pipelines.new_releases import main

    main()


if __name__ == "__main__":
    run()
