#!/usr/bin/env python3
"""Shim to run the New Releases updater from the repository root."""

from __future__ import annotations

from _bootstrap_src import bootstrap_src


def main() -> int:
    bootstrap_src()
    from neuralcast.cli.update_new_releases import main as cli_main

    return cli_main()


if __name__ == "__main__":
    raise SystemExit(main())
