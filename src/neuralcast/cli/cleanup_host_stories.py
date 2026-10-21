"""CLI entrypoint for daily host snippet retention, including paused channels."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from dotenv import load_dotenv

from neuralcast.pipelines.host_orchestrator.config import configure_logging
from neuralcast.pipelines.host_orchestrator.retention import cleanup_host_assets
from neuralcast.pipelines.host_orchestrator.transport import AzuraCastClient
from neuralcast.services.azuracast_config import load_azuracast_settings


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--keep-local-days", type=int, default=3)
    parser.add_argument("--keep-remote-days", type=int, default=7)
    parser.add_argument("--verify-tls", action="store_true")
    args = parser.parse_args(argv)
    load_dotenv()
    configure_logging()
    settings = load_azuracast_settings()
    client = AzuraCastClient(settings.base_url, settings.api_key, args.verify_tls)
    cleanup_host_assets(
        client,
        keep_local_days=args.keep_local_days,
        keep_remote_days=args.keep_remote_days,
        dry_run=args.dry_run,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
