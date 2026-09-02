"""Unit tests for schedule-generator state helpers."""

from __future__ import annotations

import pytest

from neuralcast.pipelines.schedule_generator import state


def test_schedule_state_path_creates_metadata_dir(tmp_path, monkeypatch) -> None:
    station_dir = tmp_path / "Station"
    monkeypatch.setattr(state, "resolve_station_dir", lambda _station: station_dir)

    path = state.schedule_state_path("neuralforge")

    assert path.parent == station_dir / "metadata"
    assert path.parent.exists()


def test_run_with_retries_retries_and_returns(monkeypatch) -> None:
    monkeypatch.setattr(state.time, "sleep", lambda _seconds: None)
    attempts = {"count": 0}

    def flaky() -> str:
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise RuntimeError("temporary")
        return "ok"

    assert state.run_with_retries("flaky", flaky, retries=1, delays=(0,)) == "ok"


def test_run_with_retries_reraises_after_last_attempt(monkeypatch) -> None:
    monkeypatch.setattr(state.time, "sleep", lambda _seconds: None)

    with pytest.raises(RuntimeError, match="failed"):
        state.run_with_retries("always-fails", lambda: (_ for _ in ()).throw(RuntimeError("failed")), retries=1, delays=(0,))


def test_schedule_state_atomic_round_trip_and_invalid_payload(tmp_path) -> None:
    path = tmp_path / "metadata" / "state.json"
    payload = {"presentation": {"title": "This week"}, "blocks": []}

    state.save_schedule_state_atomic(path, payload)

    assert state.load_schedule_state(path) == payload
    path.write_text("not json", encoding="utf-8")
    assert state.load_schedule_state(path) is None


def test_load_schedule_presentation_does_not_create_metadata_directory(tmp_path, monkeypatch) -> None:
    station_dir = tmp_path / "Station"
    monkeypatch.setattr(state, "resolve_station_dir", lambda _station: station_dir)

    assert state.load_schedule_presentation("neuralforge") is None
    assert not (station_dir / "metadata").exists()

    metadata_dir = station_dir / "metadata"
    metadata_dir.mkdir(parents=True)
    state.save_schedule_state_atomic(metadata_dir / state.STATE_FILENAME, {"presentation": {"name": "Plan"}})
    assert state.load_schedule_presentation("neuralforge") == {"name": "Plan"}
