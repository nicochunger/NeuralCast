#!/usr/bin/env python3
"""Shim to run the host orchestrator from the repository root."""

from __future__ import annotations

from _bootstrap_src import bootstrap_src


def main() -> None:
    bootstrap_src()
    from neuralcast.cli.host_orchestrator import main as cli_main

    cli_main()


if __name__ == "__main__":
    main()
