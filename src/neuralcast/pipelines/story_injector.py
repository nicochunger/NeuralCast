"""Backward-compatible shim for the renamed host orchestrator pipeline."""

from __future__ import annotations

import pathlib
import sys

if __package__ in (None, ""):
    src_dir = pathlib.Path(__file__).resolve().parents[2]
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))

from neuralcast.pipelines.host_orchestrator import *  # noqa: F401,F403


if __name__ == "__main__":
    run(build_arg_parser().parse_args())
