"""Unit tests for shared AzuraCast connection settings."""

from __future__ import annotations

import pytest

from neuralcast.services.azuracast_config import (
    AzuraCastConfigError,
    load_azuracast_settings,
    resolve_azuracast_station,
)


def test_explicit_settings_override_environment_and_normalize_url() -> None:
    settings = load_azuracast_settings(
        base_url=" https://override.test/ ",
        api_key=" explicit-key ",
        station="neuralcast",
        environ={
            "AZURACAST_BASE_URL": "https://environment.test",
            "AZURACAST_API_KEY": "environment-key",
            "AZURACAST_STATION": "neuralforge",
        },
    )

    assert settings.base_url == "https://override.test"
    assert settings.api_key == "explicit-key"
    assert settings.station == "neuralcast"


def test_environment_settings_and_default_station_are_supported() -> None:
    settings = load_azuracast_settings(
        environ={
            "AZURACAST_BASE_URL": "https://environment.test/",
            "AZURACAST_API_KEY": "environment-key",
        }
    )

    assert settings.base_url == "https://environment.test"
    assert settings.api_key == "environment-key"
    assert settings.station == "neuralforge"
    assert resolve_azuracast_station(environ={}) == "neuralforge"


@pytest.mark.parametrize(
    ("environ", "missing_name"),
    [
        ({"AZURACAST_API_KEY": "key"}, "AZURACAST_BASE_URL"),
        ({"AZURACAST_BASE_URL": "https://example.test"}, "AZURACAST_API_KEY"),
    ],
)
def test_required_connection_settings_are_validated(
    environ: dict[str, str], missing_name: str
) -> None:
    with pytest.raises(AzuraCastConfigError, match=missing_name):
        load_azuracast_settings(environ=environ)
