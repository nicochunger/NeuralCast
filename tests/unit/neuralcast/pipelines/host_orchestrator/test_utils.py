"""Unit tests for host orchestrator utility helpers."""

from __future__ import annotations

from neuralcast.pipelines.host_orchestrator import utils


def test_track_key_normalizes_spacing_and_case() -> None:
    assert utils.track_key("  Ghost ", "Rats   Live") == "ghost|rats live"


def test_iso_utc_formats_timestamp_with_timezone() -> None:
    assert utils.iso_utc(0).startswith("1970-01-01T00:00:00+00:00")


def test_run_with_retries_retries_until_success(monkeypatch) -> None:
    monkeypatch.setattr(utils.time, "sleep", lambda _seconds: None)
    attempts = {"count": 0}

    def flaky() -> str:
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise RuntimeError("temporary")
        return "ok"

    assert utils.run_with_retries("flaky", flaky, retries=1, delays=(0,)) == "ok"
