#!/usr/bin/env python3
"""Shim to run the Deezer New Releases updater from the repository root."""

from __future__ import annotations

from _bootstrap_src import bootstrap_src


def main() -> None:
    bootstrap_src()
    from neuralcast.cli.update_new_releases_deezer import run

    run()


if __name__ == "__main__":
    main()

