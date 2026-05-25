"""Unit tests for shared Deezer API helpers."""

from __future__ import annotations

from datetime import datetime

import requests

from tests.fakes import FakeResponse

from neuralcast.services import deezer


def test_parse_release_date_accepts_year_month_and_day_precision() -> None:
    assert deezer.parse_release_date("2026-05-15") == datetime(2026, 5, 15)
    assert deezer.parse_release_date("2026-05") == datetime(2026, 5, 1)
    assert deezer.parse_release_date("2026") == datetime(2026, 1, 1)
    assert deezer.parse_release_date("not-a-date") is None


def test_payload_items_filters_non_dict_entries() -> None:
    assert deezer._payload_items({"data": [{"id": 1}, "bad", {"id": 2}]}) == [
        {"id": 1},
        {"id": 2},
    ]
    assert deezer._payload_items({"data": "bad"}) == []


def test_deezer_get_returns_none_for_api_error(monkeypatch) -> None:
    monkeypatch.setattr(deezer.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(deezer.time, "monotonic", lambda: 1000.0)
    monkeypatch.setattr(
        deezer.SESSION,
        "get",
        lambda *_args, **_kwargs: FakeResponse({"error": {"code": 1}}),
    )

    assert deezer.deezer_get("/search/track") is None


def test_deezer_get_retries_rate_limit_and_request_errors(monkeypatch) -> None:
    sleeps: list[float] = []
    responses = [
        FakeResponse(status_code=429),
        requests.RequestException("temporary"),
        FakeResponse({"data": [{"id": 1}]}),
    ]
    responses[0].headers["Retry-After"] = "2"
    monkeypatch.setattr(deezer.time, "sleep", sleeps.append)
    monkeypatch.setattr(deezer.time, "monotonic", lambda: 1000.0)

    def fake_get(*_args, **_kwargs):
        response = responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(deezer.SESSION, "get", fake_get)

    assert deezer.deezer_get("/search/track") == {"data": [{"id": 1}]}
    assert sleeps.count(2) >= 2


def test_deezer_get_rejects_invalid_json_and_nondict_payload(monkeypatch) -> None:
    class BadJsonResponse(FakeResponse):
        def json(self):  # noqa: ANN201
            raise ValueError("bad json")

    monkeypatch.setattr(deezer.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(deezer.time, "monotonic", lambda: 1000.0)
    monkeypatch.setattr(deezer.SESSION, "get", lambda *_args, **_kwargs: BadJsonResponse())

    assert deezer.deezer_get("/search/track") is None

    monkeypatch.setattr(
        deezer.SESSION,
        "get",
        lambda *_args, **_kwargs: FakeResponse([{"id": 1}]),
    )

    assert deezer.deezer_get("/search/track") is None


def test_get_album_and_track_reject_blank_ids_and_bad_payloads(monkeypatch) -> None:
    assert deezer.get_album("") is None
    assert deezer.get_track(" ") is None

    monkeypatch.setattr(deezer, "deezer_get", lambda _resource: ["bad"])

    assert deezer.get_album("123") is None
    assert deezer.get_track("456") is None


def test_search_helpers_build_expected_resources(monkeypatch) -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    def fake_get(resource: str, *, params: dict[str, object] | None = None) -> dict:
        calls.append((resource, params or {}))
        return {"data": [{"id": "hit"}]}

    monkeypatch.setattr(deezer, "deezer_get", fake_get)

    assert deezer.search_tracks("Ghost", limit=3) == [{"id": "hit"}]
    assert deezer.search_albums("Prequelle", limit=2) == [{"id": "hit"}]
    assert deezer.get_album("123") == {"data": [{"id": "hit"}]}
    assert deezer.get_track("456") == {"data": [{"id": "hit"}]}
    assert calls == [
        ("/search/track", {"q": "Ghost", "limit": 3}),
        ("/search/album", {"q": "Prequelle", "limit": 2}),
        ("/album/123", {}),
        ("/track/456", {}),
    ]
