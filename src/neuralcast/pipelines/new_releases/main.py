"""Compatibility exports for the New Releases pipeline.

New code should import from :mod:`models`, :mod:`operations`, or :mod:`runtime`.
The command-line entrypoint lives in :mod:`neuralcast.cli.update_new_releases`.
"""

import argparse
from collections.abc import Sequence

from .models import ArtistIDCache, ArtistRelease
from .operations import (
    build_new_releases,
    fetch_recent_releases,
    load_existing_new_releases,
    load_station_artists,
    parse_release_date,
    save_new_releases,
)
from .runtime import NewReleasesRequest, NewReleasesResult, NewReleasesRuntime


def build_arg_parser() -> argparse.ArgumentParser:
    """Compatibility wrapper for the CLI-owned argument parser."""

    from neuralcast.cli.update_new_releases import build_arg_parser as build_parser

    return build_parser()


def main(argv: Sequence[str] | None = None) -> int:
    """Compatibility wrapper for the public CLI entrypoint."""

    from neuralcast.cli.update_new_releases import main as cli_main

    return cli_main(argv)


__all__ = [
    "ArtistIDCache",
    "ArtistRelease",
    "NewReleasesRequest",
    "NewReleasesResult",
    "NewReleasesRuntime",
    "build_arg_parser",
    "build_new_releases",
    "fetch_recent_releases",
    "load_existing_new_releases",
    "load_station_artists",
    "main",
    "parse_release_date",
    "save_new_releases",
]
