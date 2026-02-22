"""Shared sys.path bootstrap for repository-root compatibility shims."""

from __future__ import annotations

import pathlib
import sys


def bootstrap_src() -> None:
    project_root = pathlib.Path(__file__).resolve().parent
    src_dir = project_root / "src"
    src_str = str(src_dir)
    if src_str not in sys.path:
        sys.path.insert(0, src_str)


__all__ = ["bootstrap_src"]
