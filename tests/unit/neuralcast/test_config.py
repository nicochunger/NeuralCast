"""Unit tests for shared package configuration."""

from __future__ import annotations

import pytest

from neuralcast import config


def test_station_dir_from_slug_resolves_known_station() -> None:
    assert config.station_dir_from_slug("neuralforge") == config.PROJECT_ROOT / "NeuralForge"


def test_station_dir_from_slug_rejects_unknown_station() -> None:
    with pytest.raises(ValueError, match="Unsupported station"):
        config.station_dir_from_slug("unknown")


def test_runtime_paths_are_project_local() -> None:
    assert config.RUNTIME_ROOT == config.PROJECT_ROOT / "runtime"
    assert config.LOGS_ROOT == config.RUNTIME_ROOT / "logs"


def test_default_timezone_is_europe_zurich() -> None:
    assert config.DEFAULT_TIMEZONE_NAME == "Europe/Zurich"
