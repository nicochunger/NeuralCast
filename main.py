#!/usr/bin/env python3
"""Shim to run the playlist sync CLI without changing existing commands."""

from __future__ import annotations

from _bootstrap_src import bootstrap_src


def main() -> None:
    bootstrap_src()
    from neuralcast.cli.sync_playlists import run

    run()


if __name__ == "__main__":
    main()
