"""CLI entrypoint for playlist synchronization."""

from __future__ import annotations

import argparse


def run() -> None:
    parser = argparse.ArgumentParser(
        description="AI-assisted local-network radio pipeline."
    )
    parser.add_argument(
        "-s",
        "--station",
        type=str,
        help="The name of the radio station to process (e.g., NeuralCast, NeuralForge).",
    )
    parser.add_argument(
        "-n",
        "--dry-run",
        action="store_true",
        help="Dry run: validate and re-tag existing MP3s, but skip new downloads.",
    )
    args = parser.parse_args()
    station = args.station or "NeuralCast"
    from neuralcast.pipelines.playlist_sync import list_playlists, main

    list_playlists(station)
    main(station, args.dry_run)


if __name__ == "__main__":
    run()
