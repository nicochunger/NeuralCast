"""Unit tests for admin API CLI entrypoint."""

from __future__ import annotations

import pytest

from neuralcast.cli import admin_api


def test_admin_api_parser_uses_env_defaults(monkeypatch) -> None:
    monkeypatch.setenv("NEURALCAST_ADMIN_HTTP_HOST", "127.0.0.2")
    monkeypatch.setenv("NEURALCAST_ADMIN_HTTP_PORT", "9999")

    args = admin_api.build_arg_parser().parse_args([])

    assert args.host == "127.0.0.2"
    assert args.port == 9999


def test_admin_api_main_requires_token(monkeypatch) -> None:
    monkeypatch.setattr("sys.argv", ["neuralcast-admin-api"])
    monkeypatch.delenv("NEURALCAST_ADMIN_HTTP_TOKEN", raising=False)
    monkeypatch.setattr(admin_api.uvicorn, "run", lambda *_args, **_kwargs: None)

    with pytest.raises(RuntimeError, match="NEURALCAST_ADMIN_HTTP_TOKEN"):
        admin_api.main()
