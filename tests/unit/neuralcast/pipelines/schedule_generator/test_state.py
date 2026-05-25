"""Unit tests for schedule-generator state helpers."""

from __future__ import annotations

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
