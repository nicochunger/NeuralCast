"""Unit tests for schedule-generator runtime entrypoint validation."""

from __future__ import annotations

import pytest

from neuralcast.pipelines.schedule_generator import main as schedule_main


def test_build_arg_parser_rejects_invalid_station() -> None:
    with pytest.raises(SystemExit):
        schedule_main.build_arg_parser().parse_args(["-s", "missing"])


def test_run_requires_base_url_or_env(monkeypatch) -> None:
    args = schedule_main.build_arg_parser().parse_args(["--dry-run"])
    monkeypatch.setattr(schedule_main, "load_dotenv", lambda: None)
    monkeypatch.delenv("AZURACAST_BASE_URL", raising=False)
    monkeypatch.delenv("AZURACAST_API_KEY", raising=False)

    with pytest.raises(RuntimeError, match="AZURACAST_BASE_URL"):
        schedule_main.run(args)


def test_run_requires_api_key(monkeypatch) -> None:
    args = schedule_main.build_arg_parser().parse_args(
        ["--base-url", "https://example.test", "--dry-run"]
    )
    monkeypatch.setattr(schedule_main, "load_dotenv", lambda: None)
    monkeypatch.delenv("AZURACAST_API_KEY", raising=False)

    with pytest.raises(RuntimeError, match="AZURACAST_API_KEY"):
        schedule_main.run(args)


def test_neuralcast_resolves_shorter_default_block_duration() -> None:
    args = schedule_main.build_arg_parser().parse_args(
        ["--base-url", "https://example.test", "-s", "neuralcast"]
    )

    assert schedule_main._resolve_block_duration_bounds(args) == (30, 75)


def test_explicit_block_duration_overrides_neuralcast_defaults() -> None:
    args = schedule_main.build_arg_parser().parse_args(
        [
            "--base-url",
            "https://example.test",
            "-s",
            "neuralcast",
            "--min-block-minutes",
            "45",
            "--max-block-minutes",
            "90",
        ]
    )

    assert schedule_main._resolve_block_duration_bounds(args) == (45, 90)


def test_neuralforge_resolves_global_block_duration_defaults() -> None:
    args = schedule_main.build_arg_parser().parse_args(
        ["--base-url", "https://example.test", "-s", "neuralforge"]
    )

    assert schedule_main._resolve_block_duration_bounds(args) == (30, 90)
