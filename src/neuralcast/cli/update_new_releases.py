"""CLI entrypoint for updating New Releases playlists."""

from __future__ import annotations

from neuralcast.pipelines.new_releases import main


def run() -> None:
    main()


if __name__ == "__main__":
    run()
