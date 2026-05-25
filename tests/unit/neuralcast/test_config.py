"""Unit tests for shared package configuration."""

from __future__ import annotations

import pytest

from neuralcast import config


def test_station_dir_from_slug_resolves_known_station() -> None:
    assert config.station_dir_from_slug("neuralforge") == config.PROJECT_ROOT / "NeuralForge"


def test_station_dir_from_slug_rejects_unknown_station() -> None:
    with pytest.raises(ValueError, match="Unsupported station"):
        config.station_dir_from_slug("unknown")
