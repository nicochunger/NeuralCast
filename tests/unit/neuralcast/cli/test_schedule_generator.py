"""Unit tests for schedule generator CLI entrypoint."""

from __future__ import annotations

import argparse

from neuralcast.cli import schedule_generator


def test_schedule_generator_main_dispatches_parsed_args(monkeypatch) -> None:
    parsed = argparse.Namespace(station="neuralforge")
    calls: list[argparse.Namespace] = []

    class Parser:
        def parse_args(self) -> argparse.Namespace:
            return parsed

    monkeypatch.setattr(schedule_generator, "build_arg_parser", lambda: Parser())
    monkeypatch.setattr(schedule_generator, "run", calls.append)

    schedule_generator.main()

    assert calls == [parsed]
