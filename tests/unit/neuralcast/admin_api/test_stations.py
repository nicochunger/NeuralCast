"""Unit tests for admin station service helpers."""

from __future__ import annotations

import pytest

from tests.fakes import FakeAzuraCastClient

from neuralcast.admin_api.stations import AdminStationService, StationServiceConfigError


def test_station_service_uses_injected_client_factory() -> None:
    service = AdminStationService(client_factory=lambda: FakeAzuraCastClient())

    now_playing = service.now_playing("neuralforge")
    queue = service.queue("neuralforge", limit=1)

    assert now_playing["current_track"]["title"] == "Black Winter Day"
    assert queue["next_track"]["title"] == "Noose"
    assert len(queue["items"]) == 1


def test_station_service_requires_azuracast_env_for_default_client(monkeypatch) -> None:
    monkeypatch.delenv("AZURACAST_BASE_URL", raising=False)
    monkeypatch.delenv("AZURACAST_API_KEY", raising=False)

    with pytest.raises(StationServiceConfigError):
        AdminStationService._build_client_from_env()
