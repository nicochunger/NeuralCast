"""Weekly schedule generation and AzuraCast schedule application."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from typing import Mapping
from zoneinfo import ZoneInfo

from neuralcast.config import (
    ALLOWED_STATION_SLUGS,
    DEFAULT_STATION_SLUG,
    PROJECT_ROOT,
)

from .client import (
    AzuraCastClient,
    apply_weekly_schedule,
    azuracast_time_for_api,
    build_schedule_items_by_playlist,
    choose_station_payload,
    derive_station_name,
    derive_station_timezone,
    extract_station_playlists,
    infer_azuracast_days,
)
from .config import (
    DEFAULT_MAX_BLOCK_MINUTES,
    DEFAULT_MIN_BLOCK_MINUTES,
    DEFAULT_MAX_OPEN_SLOTS,
    DEFAULT_MIN_OPEN_SLOTS,
    DEFAULT_OPEN_RATIO_MAX,
    DEFAULT_OPEN_RATIO_MIN,
    LOGGER,
    configure_logging,
    load_dotenv,
)
from .generation import (
    DEFAULT_SCHEDULE_SEED_MODE,
    SUPPORTED_SCHEDULE_SEED_MODES,
    build_weekly_plan_with_code,
    build_weekly_plan_with_llm,
)
from .models import (
    DailyTemplateBlock,
    ExpandedScheduleBlock,
    ScheduleValidationError,
    StationPlaylist,
    WeeklySchedulePlan,
)
from .state import (
    load_schedule_state,
    resolve_station_dir,
    run_with_retries,
    save_schedule_state_atomic,
    schedule_state_path,
)
from .template import (
    block_open_preference,
    build_deterministic_daily_template,
    build_duration_partition,
    build_plan_hash,
    build_weighted_playlist_cycle,
    choose_open_block_indices,
    compute_week_start,
    expand_daily_template_to_week,
    format_hhmm,
    format_seed_template_for_prompt,
    normalize_genre_labels,
    normalize_mode,
    normalize_string_list,
    overlaps_unscheduled_window,
    parse_hhmm,
    summarize_plan,
    validate_daily_template,
)


VPS_SCHEDULER_PROJECT_ROOT = "/root/radio_host_orchestrator"


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

    open_ratio_min = float(args.open_ratio_min)
    open_ratio_max = float(args.open_ratio_max)
    if not (0.0 <= open_ratio_min <= open_ratio_max <= 1.0):
        raise ValueError("Open-slot ratio bounds must satisfy 0 <= min <= max <= 1.")

    min_open_slots = int(args.min_open_slots)
    max_open_slots = int(args.max_open_slots)
    if min_open_slots < 0 or max_open_slots < 0:
        raise ValueError("Open-slot count bounds must be non-negative.")
    if min_open_slots > max_open_slots:
        raise ValueError("min-open-slots cannot exceed max-open-slots.")

    if args.min_block_minutes > args.max_block_minutes:
        raise ValueError("min-block-minutes cannot exceed max-block-minutes.")
    if args.seed_mode == "stable_week" and args.seed_salt:
        raise ValueError(
            "seed_salt is only supported when --seed-mode is 'fresh' or 'custom'."
        )
    if args.seed_mode == "custom" and not args.seed_salt:
        raise ValueError("seed_salt is required when --seed-mode custom is used.")

    if not args.dry_run:
        current_project_root = str(PROJECT_ROOT.resolve())
        if current_project_root != VPS_SCHEDULER_PROJECT_ROOT:
            raise RuntimeError(
                "Refusing non-dry-run schedule generation outside the VPS deployment root. "
                f"Current PROJECT_ROOT={current_project_root!r}; expected "
                f"{VPS_SCHEDULER_PROJECT_ROOT!r}. Use --dry-run locally, or run on the VPS."
            )

    client = AzuraCastClient(
        base_url=base_url.rstrip("/"),
        api_key=api_key,
        verify_tls=args.verify_tls,
    )

    stations = run_with_retries("Fetch stations", client.get_stations)
    station_payload = choose_station_payload(stations, args.station)
    station_name = derive_station_name(station_payload, args.station)
    timezone_name = derive_station_timezone(station_payload)
    station_tz = ZoneInfo(timezone_name)

    now_local = dt.datetime.now(station_tz)
    if args.week_start_date:
        week_start = dt.date.fromisoformat(args.week_start_date)
    else:
        week_start = compute_week_start(now_local.date())
    week_end = week_start + dt.timedelta(days=6)

    raw_playlists = run_with_retries(
        "Fetch station playlists",
        lambda: client.get_station_playlists(args.station),
    )
    playlists = extract_station_playlists(raw_playlists)
    if not playlists:
        raise RuntimeError(
            f"No playlists returned by AzuraCast for station '{args.station}'."
        )

    LOGGER.info(
        "[station] %s (%s) | timezone=%s | playlists=%s",
        station_name,
        args.station,
        timezone_name,
        len(playlists),
    )

    plan = build_weekly_plan_with_code(
        station_slug=args.station,
        station_name=station_name,
        timezone_name=timezone_name,
        week_start=week_start,
        week_end=week_end,
        playlists=playlists,
        open_ratio_min=open_ratio_min,
        open_ratio_max=open_ratio_max,
        min_open_slots=min_open_slots,
        max_open_slots=max_open_slots,
        min_block_minutes=args.min_block_minutes,
        max_block_minutes=args.max_block_minutes,
        seed_mode=args.seed_mode,
        seed_salt=args.seed_salt,
    )

    summarize_plan(plan)

    state_path = schedule_state_path(args.station)
    existing_state = load_schedule_state(state_path)
    previous_hash = (
        str(existing_state.get("plan_hash"))
        if isinstance(existing_state, Mapping)
        else None
    )

    if args.dry_run:
        LOGGER.info("[dry-run] Skipping AzuraCast mutations.")
        print(json.dumps(plan.to_dict(), indent=2, ensure_ascii=False))
        return

    if previous_hash and previous_hash == plan.plan_hash and not args.force_apply:
        LOGGER.info(
            "[apply] Plan hash unchanged (%s); skipping remote apply (use --force-apply to override).",
            plan.plan_hash,
        )
        save_schedule_state_atomic(state_path, plan.to_dict())
        return

    updated_playlists, updated_items = apply_weekly_schedule(
        client=client,
        station_slug=args.station,
        playlists=playlists,
        daily_template=plan.daily_template,
    )

    save_schedule_state_atomic(state_path, plan.to_dict())

    LOGGER.info(
        "[apply] Updated %s playlists with %s scheduled blocks total.",
        updated_playlists,
        updated_items,
    )
    LOGGER.info("[state] Saved schedule state to %s", state_path)


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
        default=DEFAULT_MIN_BLOCK_MINUTES,
        help="Minimum allowed block duration in minutes (default: %(default)s).",
    )
    parser.add_argument(
        "--max-block-minutes",
        type=int,
        default=DEFAULT_MAX_BLOCK_MINUTES,
        help="Maximum allowed block duration in minutes (default: %(default)s).",
    )
    return parser


if __name__ == "__main__":
    run(build_arg_parser().parse_args())
