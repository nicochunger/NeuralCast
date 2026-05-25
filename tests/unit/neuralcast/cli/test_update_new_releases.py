"""Unit tests for New Releases CLI entrypoint."""

from __future__ import annotations

import types

from neuralcast.cli import update_new_releases


def test_update_new_releases_run_dispatches_to_pipeline(monkeypatch) -> None:
    calls: list[bool] = []
    fake_module = types.SimpleNamespace(main=lambda: calls.append(True))
    monkeypatch.setattr("sys.argv", ["update_new_releases.py", "-s", "neuralforge"])
    monkeypatch.setitem(
        __import__("sys").modules,
        "neuralcast.pipelines.new_releases",
        fake_module,
    )

    update_new_releases.run()

    assert calls == [True]
