"""Unit tests for host orchestrator module execution shim."""

from __future__ import annotations

from neuralcast.pipelines.host_orchestrator import __main__ as host_main


def test_host_orchestrator_main_module_exports_parser_and_run() -> None:
    assert callable(host_main.build_arg_parser)
    assert callable(host_main.run)
