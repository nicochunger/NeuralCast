"""Unit tests for New Releases module execution shim."""

from __future__ import annotations

from neuralcast.pipelines.new_releases import __main__ as new_releases_main


def test_new_releases_main_exports_callable_main() -> None:
    assert callable(new_releases_main.main)
