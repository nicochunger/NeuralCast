"""Weekly AI schedule generation and AzuraCast schedule application."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from typing import Mapping
from zoneinfo import ZoneInfo

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
    DEFAULT_GEMINI_MODEL,
    DEFAULT_MAX_BLOCK_MINUTES,
    DEFAULT_MIN_BLOCK_MINUTES,
    DEFAULT_OPEN_RATIO_MAX,
    DEFAULT_OPEN_RATIO_MIN,
    LOGGER,
    configure_logging,
    load_dotenv,
)
from .generation import (
    build_playlist_catalog,
    build_weekly_plan_with_llm,
    extract_json_object,
    gemini_generate_schedule_text,
    load_prompt,
    strip_code_fences,
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


def run(args: argparse.Namespace) -> None:
    configure_logging()

    load_dotenv()
    api_key = os.getenv("AZURACAST_API_KEY")
    if not api_key:
        raise RuntimeError("AZURACAST_API_KEY is not set in the environment.")

    open_ratio_min = float(args.open_ratio_min)
    open_ratio_max = float(args.open_ratio_max)
    if not (0.0 <= open_ratio_min <= open_ratio_max <= 1.0):
        raise ValueError("Open-slot ratio bounds must satisfy 0 <= min <= max <= 1.")

    if args.min_block_minutes > args.max_block_minutes:
        raise ValueError("min-block-minutes cannot exceed max-block-minutes.")

    client = AzuraCastClient(
        base_url=args.base_url.rstrip("/"),
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

    plan = build_weekly_plan_with_llm(
        station_slug=args.station,
        station_name=station_name,
        timezone_name=timezone_name,
        week_start=week_start,
        week_end=week_end,
        playlists=playlists,
        open_ratio_min=open_ratio_min,
        open_ratio_max=open_ratio_max,
        min_block_minutes=args.min_block_minutes,
        max_block_minutes=args.max_block_minutes,
        model=args.model,
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
            "from AzuraCast playlists using Gemini."
        )
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv("AZURACAST_BASE_URL", "https://192.168.1.226"),
        help="Base URL for AzuraCast instance (default: %(default)s).",
    )
    parser.add_argument(
        "-s",
        "--station",
        default=os.getenv("AZURACAST_STATION", "neuralforge"),
        help="AzuraCast station shortcode (default: %(default)s).",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_GEMINI_MODEL,
        help="Gemini text model for schedule generation (default: %(default)s).",
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
        help="Minimum open-slot ratio per day (0-1).",
    )
    parser.add_argument(
        "--open-ratio-max",
        type=float,
        default=DEFAULT_OPEN_RATIO_MAX,
        help="Maximum open-slot ratio per day (0-1).",
    )
    parser.add_argument(
        "--min-block-minutes",
        type=int,
        default=DEFAULT_MIN_BLOCK_MINUTES,
        help="Minimum allowed block duration in minutes.",
    )
    parser.add_argument(
        "--max-block-minutes",
        type=int,
        default=DEFAULT_MAX_BLOCK_MINUTES,
        help="Maximum allowed block duration in minutes.",
    )
    return parser


if __name__ == "__main__":
    run(build_arg_parser().parse_args())
