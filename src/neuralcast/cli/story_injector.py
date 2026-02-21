"""Backward-compatible CLI alias for the host orchestrator entrypoint."""

from __future__ import annotations

from neuralcast.cli.host_orchestrator import main


if __name__ == "__main__":
    main()
