"""Unit tests for host orchestrator CLI entrypoint."""

from __future__ import annotations

import argparse

import pytest

from neuralcast.cli import host_orchestrator
from neuralcast.pipelines.host_orchestrator.main import ArgumentValidationError


def test_host_orchestrator_main_dispatches_parsed_args(monkeypatch) -> None:
    parsed = argparse.Namespace(station="neuralforge")
    calls: list[argparse.Namespace] = []

    class Parser:
        def parse_args(self) -> argparse.Namespace:
            return parsed

        def error(self, message: str) -> None:
            raise AssertionError(message)

    monkeypatch.setattr(host_orchestrator, "build_arg_parser", lambda: Parser())
    monkeypatch.setattr(host_orchestrator, "run", calls.append)

    host_orchestrator.main()

    assert calls == [parsed]


def test_host_orchestrator_main_converts_validation_error_to_parser_error(monkeypatch) -> None:
    class Parser:
        def parse_args(self) -> argparse.Namespace:
            return argparse.Namespace()

        def error(self, message: str) -> None:
            raise SystemExit(message)

    monkeypatch.setattr(host_orchestrator, "build_arg_parser", lambda: Parser())
    monkeypatch.setattr(
        host_orchestrator,
        "run",
        lambda _args: (_ for _ in ()).throw(ArgumentValidationError("bad args")),
    )

    with pytest.raises(SystemExit, match="bad args"):
        host_orchestrator.main()
