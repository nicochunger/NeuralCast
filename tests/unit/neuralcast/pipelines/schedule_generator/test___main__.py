"""Unit tests for schedule generator module execution shim."""

from __future__ import annotations

from neuralcast.pipelines.schedule_generator import __main__ as schedule_main


def test_schedule_generator_main_module_exports_parser_and_run() -> None:
    assert callable(schedule_main.build_arg_parser)
    assert callable(schedule_main.run)
