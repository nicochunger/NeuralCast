"""CLI entrypoint for the NeuralCast admin HTTP service."""

from __future__ import annotations

import argparse
import os

import uvicorn

from neuralcast.admin_api.app import create_app
from neuralcast.admin_api.jobs import DEFAULT_ADMIN_HTTP_HOST, DEFAULT_ADMIN_HTTP_PORT


def build_arg_parser() -> argparse.ArgumentParser:
    """Build the command-line parser for the admin HTTP API."""

    parser = argparse.ArgumentParser(
        description="Authenticated localhost HTTP API for NeuralCast admin actions."
    )
    parser.add_argument(
        "--host",
        default=os.getenv("NEURALCAST_ADMIN_HTTP_HOST", DEFAULT_ADMIN_HTTP_HOST),
        help="Bind host for the admin API (default: %(default)s).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("NEURALCAST_ADMIN_HTTP_PORT", DEFAULT_ADMIN_HTTP_PORT)),
        help="Bind port for the admin API (default: %(default)s).",
    )
    return parser


def main() -> None:
    args = build_arg_parser().parse_args()

    if not os.getenv("NEURALCAST_ADMIN_HTTP_TOKEN"):
        raise RuntimeError("NEURALCAST_ADMIN_HTTP_TOKEN must be set before startup.")

    uvicorn.run(
        create_app(),
        host=args.host,
        port=args.port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
