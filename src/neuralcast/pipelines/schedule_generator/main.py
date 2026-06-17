"""Weekly schedule generation and AzuraCast schedule application."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os

from neuralcast.config import (
    ALLOWED_STATION_SLUGS,
    DEFAULT_STATION_SLUG,
    PROJECT_ROOT,
)

from .config import (
    DEFAULT_MAX_BLOCK_MINUTES,
    DEFAULT_MIN_BLOCK_MINUTES,
    DEFAULT_MAX_OPEN_SLOTS,
    DEFAULT_MIN_OPEN_SLOTS,
    DEFAULT_OPEN_RATIO_MAX,
    DEFAULT_OPEN_RATIO_MIN,
    LOGGER,
    NEURALCAST_DEFAULT_MAX_BLOCK_MINUTES,
    NEURALCAST_DEFAULT_MIN_BLOCK_MINUTES,
    NEURALCAST_DEFAULT_OPEN_RATIO_MAX,
    NEURALCAST_DEFAULT_OPEN_RATIO_MIN,
    configure_logging,
    load_dotenv,
)
from .generation import DEFAULT_SCHEDULE_SEED_MODE, SUPPORTED_SCHEDULE_SEED_MODES
from .runtime import (
    AzuraCastScheduleRemote,
    ScheduleGeneratorRuntime,
    ScheduleRunRequest,
    VPS_SCHEDULER_PROJECT_ROOT,
    resolve_block_duration_bounds,
)
from .template import (
    summarize_plan,
)


def _resolve_block_duration_bounds(args: argparse.Namespace) -> tuple[int, int]:
    return resolve_block_duration_bounds(
        ScheduleRunRequest(
            station=args.station,
            base_url="",
            api_key="",
            min_block_minutes=args.min_block_minutes,
            max_block_minutes=args.max_block_minutes,
        )
    )


def run(args: argparse.Namespace) -> None:
    configure_logging()

    load_dotenv()
    base_url = str(args.base_url or os.getenv("AZURACAST_BASE_URL") or "").strip()
    if not base_url:
        raise RuntimeError(
            "AZURACAST_BASE_URL is not set (and --base-url was not provided)."
        )
    api_key = os.getenv("AZURACAST_API_KEY")
    if not api_key:
        raise RuntimeError("AZURACAST_API_KEY is not set in the environment.")

    request = ScheduleRunRequest(
        station=args.station,
        base_url=base_url,
        api_key=api_key,
        dry_run=args.dry_run,
        force_apply=args.force_apply,
        verify_tls=args.verify_tls,
        week_start_date=(
            dt.date.fromisoformat(args.week_start_date) if args.week_start_date else None
        ),
        seed_mode=args.seed_mode,
        seed_salt=args.seed_salt,
        open_ratio_min=float(args.open_ratio_min),
        open_ratio_max=float(args.open_ratio_max),
        min_open_slots=int(args.min_open_slots),
        max_open_slots=int(args.max_open_slots),
        min_block_minutes=args.min_block_minutes,
        max_block_minutes=args.max_block_minutes,
        project_root=PROJECT_ROOT,
    )
    runtime = ScheduleGeneratorRuntime(
        remote=AzuraCastScheduleRemote(
            base_url=request.base_url,
            api_key=request.api_key,
            verify_tls=request.verify_tls,
        )
    )
    result = runtime.run(request)
    plan = result.plan

    LOGGER.info(
        "[station] %s (%s) | timezone=%s | playlists=%s",
        plan.station_name,
        args.station,
        plan.timezone,
        result.playlist_count,
    )

    summarize_plan(plan)

    if args.dry_run:
        LOGGER.info("[dry-run] Skipping AzuraCast mutations.")
        print(json.dumps(plan.to_dict(), indent=2, ensure_ascii=False))
        return

    if result.status == "skipped_unchanged":
        LOGGER.info(
            "[apply] Plan hash unchanged (%s); skipping remote apply (use --force-apply to override).",
            plan.plan_hash,
        )
        return

    LOGGER.info(
        "[apply] Updated %s playlists with %s scheduled blocks total.",
        result.updated_playlists,
        result.updated_items,
    )
    LOGGER.info("[state] Saved schedule state to %s", result.state_path)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Generate a fixed weekly schedule (same daily template for all 7 days) "
            "from AzuraCast playlists using the built-in code scheduler."
        )
    )
    parser.add_argument(
        "--base-url",
        help=(
            "Base URL for AzuraCast instance. If omitted, reads "
            "AZURACAST_BASE_URL from environment/.env (required)."
        ),
    )
    parser.add_argument(
        "-s",
        "--station",
        choices=ALLOWED_STATION_SLUGS,
        default=os.getenv("AZURACAST_STATION", DEFAULT_STATION_SLUG),
        help="AzuraCast station shortcode (default: %(default)s).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Generate and validate plan without writing to AzuraCast.",
    )
    parser.add_argument(
        "--force-apply",
        action="store_true",
        help="Apply even when plan hash matches saved state.",
    )
    parser.add_argument(
        "--seed-mode",
        choices=SUPPORTED_SCHEDULE_SEED_MODES,
        default=DEFAULT_SCHEDULE_SEED_MODE,
        help=(
            "Seed policy for schedule generation: %(choices)s. "
            "stable_week reproduces the same plan for the same inputs; "
            "fresh rerolls; custom uses --seed-salt."
        ),
    )
    parser.add_argument(
        "--seed-salt",
        help=(
            "Optional reroll key mixed into the schedule seed. "
            "Required when --seed-mode custom is used."
        ),
    )
    parser.add_argument(
        "--verify-tls",
        action="store_true",
        help="Verify TLS certificates for AzuraCast requests.",
    )
    parser.add_argument(
        "--week-start-date",
        help=(
            "Optional ISO date (YYYY-MM-DD) for deterministic generation. "
            "Defaults to current local week's Monday in station timezone."
        ),
    )
    parser.add_argument(
        "--open-ratio-min",
        type=float,
        default=DEFAULT_OPEN_RATIO_MIN,
        help="Minimum open-slot ratio per day (0-1) (default: %(default)s).",
    )
    parser.add_argument(
        "--open-ratio-max",
        type=float,
        default=DEFAULT_OPEN_RATIO_MAX,
        help="Maximum open-slot ratio per day (0-1) (default: %(default)s).",
    )
    parser.add_argument(
        "--min-open-slots",
        type=int,
        default=DEFAULT_MIN_OPEN_SLOTS,
        help="Minimum number of open blocks per day (default: %(default)s).",
    )
    parser.add_argument(
        "--max-open-slots",
        type=int,
        default=DEFAULT_MAX_OPEN_SLOTS,
        help="Maximum number of open blocks per day (default: %(default)s).",
    )
    parser.add_argument(
        "--min-block-minutes",
        type=int,
        default=None,
        help=(
            "Minimum allowed block duration in minutes "
            f"(default: {DEFAULT_MIN_BLOCK_MINUTES}; NeuralCast default: "
            f"{NEURALCAST_DEFAULT_MIN_BLOCK_MINUTES})."
        ),
    )
    parser.add_argument(
        "--max-block-minutes",
        type=int,
        default=None,
        help=(
            "Maximum allowed block duration in minutes "
            f"(default: {DEFAULT_MAX_BLOCK_MINUTES}; NeuralCast default: "
            f"{NEURALCAST_DEFAULT_MAX_BLOCK_MINUTES})."
        ),
    )
    return parser


if __name__ == "__main__":
    run(build_arg_parser().parse_args())
