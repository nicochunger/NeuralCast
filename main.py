#!/usr/bin/env python3
"""Shim to run the playlist sync CLI without changing existing commands."""

from __future__ import annotations

import pathlib
import sys


def _bootstrap_package() -> None:
    project_root = pathlib.Path(__file__).resolve().parent
    src_dir = project_root / "src"
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))


def main() -> None:
    _bootstrap_package()
    from neuralcast.cli.sync_playlists import run

    run()


if __name__ == "__main__":
    main()
