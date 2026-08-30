"""Unit tests for New Releases CLI entrypoint."""

from __future__ import annotations

import argparse

from neuralcast.cli import update_new_releases


def test_update_new_releases_main_dispatches_explicit_args(monkeypatch) -> None:
    requests: list[object] = []

    class Runtime:
        def run(self, request) -> None:
            requests.append(request)

    monkeypatch.setattr(
        update_new_releases,
        "build_arg_parser",
        lambda: argparse.ArgumentParser(parents=[_request_parser()], add_help=False),
    )
    monkeypatch.setattr(
        "neuralcast.pipelines.new_releases.runtime.NewReleasesRuntime",
        Runtime,
    )

    exit_code = update_new_releases.main(["-s", "neuralforge", "--dry-run"])

    assert exit_code == 0
    assert len(requests) == 1
    assert requests[0].station == "neuralforge"
    assert requests[0].dry_run is True


def _request_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("-s", "--station")
    parser.add_argument("--days", type=int, default=120)
    parser.add_argument("--per-artist", type=int, default=3)
    parser.add_argument("--min-rank", type=int, default=0)
    parser.add_argument("--prefer-singles", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    return parser


def test_update_new_releases_run_remains_a_compatibility_alias(monkeypatch) -> None:
    monkeypatch.setattr(update_new_releases, "main", lambda: 9)

    assert update_new_releases.run() == 9
