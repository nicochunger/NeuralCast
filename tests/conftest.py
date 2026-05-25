"""Shared pytest setup for repository tests."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Iterator

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


@pytest.fixture(autouse=True)
def block_external_calls(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Keep default tests offline and side-effect free.

    Tests that intentionally verify command or HTTP behavior should patch the
    exact dependency they exercise, or opt out with ``allow_external_calls``.
    """

    if request.node.get_closest_marker("allow_external_calls"):
        yield
        return

    def _blocked(*_args: object, **_kwargs: object) -> None:
        raise AssertionError(
            "Unexpected external call in default test. Patch the dependency or "
            "mark the test with @pytest.mark.allow_external_calls."
        )

    monkeypatch.setattr(subprocess, "run", _blocked)
    monkeypatch.setattr(subprocess, "Popen", _blocked)

    try:
        import requests
    except Exception:
        requests = None
    if requests is not None:
        monkeypatch.setattr(requests.sessions.Session, "request", _blocked)

    try:
        import musicbrainzngs
    except Exception:
        musicbrainzngs = None
    if musicbrainzngs is not None:
        for name in (
            "search_recordings",
            "search_releases",
            "search_release_groups",
            "get_release_by_id",
            "get_release_group_by_id",
            "get_image_list",
        ):
            if hasattr(musicbrainzngs, name):
                monkeypatch.setattr(musicbrainzngs, name, _blocked)

    yield
