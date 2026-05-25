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
