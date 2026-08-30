"""Shared AzuraCast connection settings for runtime services."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

from neuralcast.config import DEFAULT_STATION_SLUG


class AzuraCastConfigError(RuntimeError):
    """Raised when required AzuraCast connection settings are missing."""


@dataclass(frozen=True)
class AzuraCastSettings:
    base_url: str
    api_key: str
    station: str


def resolve_azuracast_station(
    station: str | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> str:
    values = os.environ if environ is None else environ
    return str(
        station or values.get("AZURACAST_STATION") or DEFAULT_STATION_SLUG
    ).strip()


def load_azuracast_settings(
    *,
    base_url: str | None = None,
    api_key: str | None = None,
    station: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> AzuraCastSettings:
    """Resolve and validate shared AzuraCast settings.

    Explicit values take precedence over environment variables. The base URL is
    normalized without a trailing slash so every client receives the same form.
    """

    values = os.environ if environ is None else environ
    resolved_base_url = str(
        base_url or values.get("AZURACAST_BASE_URL") or ""
    ).strip()
    if not resolved_base_url:
        raise AzuraCastConfigError("AZURACAST_BASE_URL is not configured.")

    resolved_api_key = str(api_key or values.get("AZURACAST_API_KEY") or "").strip()
    if not resolved_api_key:
        raise AzuraCastConfigError("AZURACAST_API_KEY is not configured.")

    return AzuraCastSettings(
        base_url=resolved_base_url.rstrip("/"),
        api_key=resolved_api_key,
        station=resolve_azuracast_station(station, environ=values),
    )


__all__ = [
    "AzuraCastConfigError",
    "AzuraCastSettings",
    "load_azuracast_settings",
    "resolve_azuracast_station",
]
