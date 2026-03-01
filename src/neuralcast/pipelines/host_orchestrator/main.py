"""Dynamic AI host orchestrator that injects station voice segments into AzuraCast."""

from __future__ import annotations

import argparse
import os
import random
import pathlib
import sys
from typing import Any

# Support direct execution via `python src/neuralcast/pipelines/host_orchestrator/main.py`.
if __package__ in (None, ""):
    src_dir = pathlib.Path(__file__).resolve().parents[3]
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))
    __package__ = "neuralcast.pipelines.host_orchestrator"

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:  # pragma: no cover - dependency guard

    def load_dotenv(*_args: Any, **_kwargs: Any) -> bool:
        return False

from neuralcast.config import ALLOWED_STATION_SLUGS, DEFAULT_STATION_SLUG

from .assets import (
    cleanup_local_stories,
    cleanup_remote_stories,
    ensure_story_assets,
    load_station_track_metadata,
)
from .config import (
    LEAD_TIME_SECONDS,
    LOGGER,
    configure_station_file_logging,
    configure_logging,
    log_segment_event,
    log_schedule_debug,
)
from .generation import (
    build_system_prompt,
    build_tts_instructions,
    generate_archetype_script,
    parse_news_output,
    resolve_station_personality,
    station_name_for_generation,
    validate_news_freshness_and_dedup,
)
from .models import (
    Archetype,
    OrchestratorState,
    ScheduleContext,
    TrackMetadata,
)
from .schedule import (
    load_schedule_state_payload,
    prune_schedule_block_mentions,
    resolve_schedule_context_for_upcoming_break,
    should_force_block_intro,
)
from .state import (
    StationLock,
    apply_success_state_update,
    assemble_banned_list,
    build_news_dedup_key,
    choose_angle,
    choose_hook,
    choose_weighted_archetype,
    default_state,
    legal_archetypes,
    load_state,
    migrate_state,
    save_state_atomic,
    should_speak_now,
    update_track_seen_state,
)
from .transport import (
    AzuraCastClient,
    build_request_command,
    choose_upcoming_tracks,
    choose_station_payload,
    derive_station_display_name,
    extract_current_listeners,
    extract_current_track,
    extract_telnet_request_id,
    extract_upload_duration,
    extract_upload_storage_path,
    parse_queue_tracks,
)
from .utils import (
    iso_utc,
    now_ts,
    run_with_retries,
    station_state_paths,
    track_key,
)


def run(args: argparse.Namespace) -> None:
    configure_logging()
    LOGGER.info("%s", "=" * 84)
    LOGGER.info(
        "[cycle] Invocation received | station=%s | dry_run=%s",
        args.station,
        args.dry_run,
    )

    load_dotenv()
    api_key = os.getenv("AZURACAST_API_KEY")
    if not api_key:
        raise RuntimeError("AZURACAST_API_KEY is not set in the environment.")

    rng = random.Random()
    cycle_ts = now_ts()
    station_dir, state_path, lock_path = station_state_paths(args.station)
    metadata_dir = station_dir / "metadata"
    main_log_path, segment_log_path, schedule_debug_log_path = configure_station_file_logging(
        metadata_dir
    )
    lock = StationLock(lock_path)
    if not lock.acquire():
        return

    LOGGER.info(
        "[cycle] Starting orchestrator cycle | station=%s | dry_run=%s",
        args.station,
        args.dry_run,
    )
    LOGGER.info(
        "[log] File logs active | main=%s | segments=%s | schedule_debug=%s",
        main_log_path,
        segment_log_path,
        schedule_debug_log_path,
    )
    log_schedule_debug(
        "cycle.logging_configured",
        station=args.station,
        main_log=str(main_log_path),
        segment_log=str(segment_log_path),
        schedule_debug_log=str(schedule_debug_log_path),
    )

    state = load_state(state_path, cycle_ts, rng)
    LOGGER.info("[state] Loaded orchestrator state: %s", state_path)
    LOGGER.info(
        "[cadence] State snapshot | songs_since_last_spoken=%s | songs_until_next_speak=%s | next_deadline=%s",
        state.songs_since_last_spoken,
        state.songs_until_next_speak,
        iso_utc(state.next_speak_deadline_ts),
    )

    try:
        client = AzuraCastClient(
            base_url=args.base_url.rstrip("/"),
            api_key=api_key,
            verify_tls=args.verify_tls,
        )

        stations = run_with_retries("Fetch stations", client.get_stations)
        station_payload = choose_station_payload(stations, args.station)
        station_id_raw = station_payload.get("id")
        station_id = int(station_id_raw) if station_id_raw is not None else None
        if station_id is None:
            raise RuntimeError(
                "Station payload missing station ID; cannot queue media."
            )

        station_name = derive_station_display_name(
            station_payload, fallback=args.station
        )
        generation_station_name = station_name_for_generation(
            args.station, station_name
        )
        station_personality = resolve_station_personality(args.station)
        LOGGER.info("[station] Personality profile active: %s", args.station)
        state.schedule_block_mentions = prune_schedule_block_mentions(
            state.schedule_block_mentions, now_ts()
        )
        log_schedule_debug(
            "cycle.mention_state_pruned",
            station=args.station,
            mention_entries=len(state.schedule_block_mentions),
            block_keys=list(state.schedule_block_mentions.keys()),
        )

        schedule_state = load_schedule_state_payload(station_dir)
        if schedule_state is not None:
            LOGGER.info(
                "[schedule] Loaded weekly schedule state for context (week_start=%s).",
                schedule_state.get("week_start_local_date") or "unknown",
            )

        try:
            now_playing_payload = run_with_retries(
                "Fetch now-playing",
                lambda: client.get_now_playing(args.station),
            )
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning(
                "[now-playing] Fetch failed after retries: %s. Skipping cycle.",
                exc,
            )
            return

        current_track, current_remaining = extract_current_track(now_playing_payload)
        current_key = track_key(current_track.artist, current_track.title)
        update_track_seen_state(state, current_key, now_ts())

        LOGGER.info(
            "[now-playing] Current track: %s - %s",
            current_track.artist,
            current_track.title,
        )

        listener_count = extract_current_listeners(now_playing_payload)
        if listener_count is None:
            LOGGER.info("[listeners] Current listeners: unavailable")
        else:
            LOGGER.info("[listeners] Current listeners: %s", listener_count)

        if args.min_listeners > 0:
            if listener_count is None:
                LOGGER.info(
                    "[gate] Listener count unavailable with --min-listeners=%s; skipping cycle.",
                    args.min_listeners,
                )
                return
            if listener_count < args.min_listeners:
                LOGGER.info(
                    "[gate] Only %s listener(s) connected (< %s); skipping cycle.",
                    listener_count,
                    args.min_listeners,
                )
                return

        if current_remaining is None:
            LOGGER.info(
                "[gate] Current remaining time unavailable; skipping for lead-time safety."
            )
            return
        if current_remaining < LEAD_TIME_SECONDS:
            LOGGER.info(
                "[gate] Current track has only %ss remaining (< %ss); skipping cycle.",
                current_remaining,
                LEAD_TIME_SECONDS,
            )
            return

        queue_payload = run_with_retries(
            "Fetch queue",
            lambda: client.get_upcoming_queue(args.station),
        )
        queue_tracks = parse_queue_tracks(queue_payload)
        upcoming_tracks = choose_upcoming_tracks(
            current=current_track,
            queue_tracks=queue_tracks,
            limit=4,
        )
        next_track = upcoming_tracks[0] if upcoming_tracks else None
        if next_track is None:
            LOGGER.info("[queue] No suitable next track found; skipping cycle.")
            return

        LOGGER.info("[queue] Next track: %s - %s", next_track.artist, next_track.title)
        LOGGER.info(
            "[queue] Upcoming queue snapshot (%s): %s",
            len(upcoming_tracks),
            " | ".join(f"{track.artist} - {track.title}" for track in upcoming_tracks),
        )
        schedule_reference_ts = now_ts() + max(0, current_remaining or 0)
        log_schedule_debug(
            "schedule.upcoming_break_lookup.start",
            station=args.station,
            current_track=f"{current_track.artist} - {current_track.title}",
            next_track=f"{next_track.artist} - {next_track.title}",
            current_remaining_seconds=current_remaining,
            ts_now=now_ts(),
            ts_break=schedule_reference_ts,
            mention_entries=len(state.schedule_block_mentions),
            schedule_state_loaded=schedule_state is not None,
        )
        schedule_context = resolve_schedule_context_for_upcoming_break(
            schedule_state=schedule_state,
            ts_now=now_ts(),
            ts_break=schedule_reference_ts,
            mention_state=state.schedule_block_mentions,
            next_track=next_track,
        )
        if schedule_context is None:
            log_schedule_debug(
                "schedule.upcoming_break_lookup.result",
                result="none",
                reason="no_schedule_context_for_break",
            )
        if schedule_context is not None:
            LOGGER.info(
                "[schedule] Next-track boundary block='%s' phase=%s mention_intent=%s",
                schedule_context.section_label,
                schedule_context.phase,
                schedule_context.mention_intent or "none",
            )
            log_schedule_debug(
                "schedule.upcoming_break_lookup.result",
                result="context",
                block_key=schedule_context.block_key,
                section_label=schedule_context.section_label,
                phase=schedule_context.phase,
                mention_intent=schedule_context.mention_intent or "none",
                progress_ratio=schedule_context.progress_ratio,
                start_local_iso=schedule_context.start_local_iso,
                end_local_iso=schedule_context.end_local_iso,
                next_section_label=schedule_context.next_section_label or "n/a",
            )

        forced_archetype = (
            Archetype(args.force_archetype) if args.force_archetype else None
        )
        auto_forced_block_intro = False
        log_schedule_debug(
            "schedule.block_intro_force.check",
            forced_archetype_arg=forced_archetype.value if forced_archetype else "none",
            schedule_context_present=schedule_context is not None,
            mention_intent=(
                schedule_context.mention_intent if schedule_context is not None else "none"
            ),
            section_label=schedule_context.section_label if schedule_context else "n/a",
        )
        if should_force_block_intro(schedule_context, forced_archetype):
            forced_archetype = Archetype.BLOCK_INTRO
            auto_forced_block_intro = True
            LOGGER.info(
                "[schedule] Block start window active for '%s'; forcing block_intro archetype.",
                schedule_context.section_label if schedule_context else "n/d",
            )
            log_schedule_debug(
                "schedule.block_intro_force.result",
                action="auto_force_block_intro",
                section_label=schedule_context.section_label if schedule_context else "n/a",
            )
        else:
            log_schedule_debug(
                "schedule.block_intro_force.result",
                action="no_auto_force",
                effective_forced_archetype=forced_archetype.value if forced_archetype else "none",
            )

        if forced_archetype is None:
            eligible, wait_reason = should_speak_now(state, current_key, now_ts())
            if not eligible:
                LOGGER.info("[gate] Wait gate closed: %s", wait_reason)
                return
            LOGGER.info("[gate] Wait gate open: %s", wait_reason)
        else:
            if auto_forced_block_intro:
                LOGGER.info(
                    "[gate] Auto-forced archetype active: %s; bypassing wait gate.",
                    forced_archetype.value,
                )
            else:
                LOGGER.info(
                    "[gate] Force archetype active: %s; bypassing wait gate.",
                    forced_archetype.value,
                )

        if forced_archetype is not None:
            selected_archetype = forced_archetype
        else:
            legal = legal_archetypes(state, now_ts())
            if legal:
                selected_archetype = choose_weighted_archetype(legal, state, rng)
                LOGGER.info(
                    "[archetype] Legal archetypes: %s",
                    [item.value for item in legal],
                )
            else:
                selected_archetype = Archetype.ULTRA_MINIMAL
                LOGGER.warning(
                    "[archetype] No legal archetypes available after cooldowns; using ultra_minimal."
                )

        angle = choose_angle(selected_archetype, state, rng)
        hook = choose_hook(selected_archetype, state, rng)
        banned_list = assemble_banned_list(state)

        metadata_cache = load_station_track_metadata(station_dir)
        current_meta = metadata_cache.get(current_key, TrackMetadata())
        next_meta = metadata_cache.get(
            track_key(next_track.artist, next_track.title), TrackMetadata()
        )

        LOGGER.info(
            "[generation] archetype=%s | angle=%s | hook=%s",
            selected_archetype.value,
            angle or "none",
            hook,
        )
        log_schedule_debug(
            "generation.archetype_selected",
            selected_archetype=selected_archetype.value,
            schedule_mention_intent=(
                schedule_context.mention_intent if schedule_context is not None else "none"
            ),
            auto_forced_block_intro=auto_forced_block_intro,
            force_arg=args.force_archetype or "none",
            section_label=schedule_context.section_label if schedule_context else "n/a",
        )
        if selected_archetype == Archetype.NEWS:
            LOGGER.info("[news] Archetype selected; topic rolls will be logged in generation.")

        script_text, news_segment, archetype_used = generate_archetype_script(
            archetype=selected_archetype,
            station_name=generation_station_name,
            personality=station_personality,
            current_track=current_track,
            next_track=next_track,
            upcoming_tracks=upcoming_tracks,
            current_meta=current_meta,
            next_meta=next_meta,
            angle=angle,
            hook=hook,
            banned_list=banned_list,
            schedule_context=schedule_context,
            state=state,
            rng=rng,
            forced_mode=forced_archetype == Archetype.NEWS,
        )

        if not script_text.strip():
            raise RuntimeError("Generated script was empty after cleanup.")

        tts_instructions = build_tts_instructions(station_personality)
        assets = run_with_retries(
            "TTS synthesis",
            lambda: ensure_story_assets(
                station_slug=args.station,
                current_track=current_track,
                archetype=archetype_used,
                script_text=script_text,
                tts_instructions=tts_instructions,
            ),
        )
        LOGGER.info("[assets] Script saved: %s", assets.text_path)
        LOGGER.info("[assets] Audio saved: %s", assets.audio_path)

        if args.dry_run:
            LOGGER.info(
                "[dry-run] Skipping upload/injection; cadence and cooldowns are not consumed."
            )
            return

        upload_response = run_with_retries(
            "Upload media",
            lambda: client.upload_media(
                args.station,
                assets.audio_path,
                remote_path=assets.remote_path,
            ),
        )

        upload_path = extract_upload_storage_path(upload_response)
        if not upload_path:
            raise RuntimeError("Upload response missing storage path.")

        story_duration = extract_upload_duration(upload_response)
        full_media_path = f"/var/azuracast/stations/{args.station}/media/{upload_path}"
        telnet_command = build_request_command(
            media_full_path=full_media_path,
            title=f"AI Host: {current_track.title}",
            duration=story_duration,
        )

        telnet_response = run_with_retries(
            "Queue media via telnet",
            lambda: client.send_telnet_command(station_id, telnet_command),
        )
        request_id = extract_telnet_request_id(telnet_response)
        if request_id:
            LOGGER.info("[queue] Queued via requests.push | request_id=%s", request_id)
        else:
            LOGGER.info("[queue] Queued via requests.push.")

        success_ts = now_ts()
        apply_success_state_update(
            state=state,
            ts=success_ts,
            current_track_key=current_key,
            current_remaining=current_remaining,
            archetype_used=archetype_used,
            hook=hook,
            angle=angle,
            news_segment=news_segment,
            script_text=script_text,
            schedule_context=schedule_context,
            rng=rng,
        )

        expected_play_at_utc: str | None = None
        if current_remaining is not None:
            expected_play_at_utc = iso_utc(success_ts + max(0, current_remaining))
        news_topics_text = None
        if news_segment is not None:
            topic_list = [story.topic for story in news_segment.stories if story.topic]
            if topic_list:
                news_topics_text = ",".join(topic_list)
        log_segment_event(
            station=args.station,
            archetype=archetype_used.value,
            current_track=f"{current_track.artist} - {current_track.title}",
            next_track=f"{next_track.artist} - {next_track.title}",
            queued_request_id=request_id,
            expected_play_at_utc=expected_play_at_utc,
            audio_path=str(assets.audio_path),
            remote_path=assets.remote_path,
            schedule_section=(
                schedule_context.section_label if schedule_context is not None else None
            ),
            mention_intent=(
                schedule_context.mention_intent if schedule_context is not None else None
            ),
            news_topics=news_topics_text,
        )
        LOGGER.info(
            "[segment] Success | archetype=%s | request_id=%s | expected_play_at_utc=%s",
            archetype_used.value,
            request_id or "n/a",
            expected_play_at_utc or "n/a",
        )

        cleanup_local_stories(args.station, args.keep_local_days)
        cleanup_remote_stories(client, args.station, args.keep_remote_days)

    finally:
        save_state_atomic(state_path, state)
        LOGGER.info("[state] Saved orchestrator state: %s", state_path)
        lock.release()


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Dynamic AI host orchestrator for AzuraCast: stateful cadence, archetype "
            "selection, and spoken segment injection."
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
        choices=ALLOWED_STATION_SLUGS,
        default=os.getenv("AZURACAST_STATION", DEFAULT_STATION_SLUG),
        help="AzuraCast station shortcode (default: %(default)s).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Generate text/audio locally without upload or queue injection.",
    )
    parser.add_argument(
        "--min-listeners",
        type=int,
        default=1,
        help=(
            "Require at least this many listeners before generation/injection "
            "(default: %(default)s; set 0 to disable)."
        ),
    )
    parser.add_argument(
        "--force-archetype",
        choices=[archetype.value for archetype in Archetype],
        help=(
            "Testing override: bypass wait gate/cooldowns and force this archetype. "
            "Still enforces listener and lead-time gates."
        ),
    )
    parser.add_argument(
        "--verify-tls",
        action="store_true",
        help="Verify TLS certificates for AzuraCast requests.",
    )
    parser.add_argument(
        "--keep-local-days",
        type=int,
        default=3,
        help="Retain local AI story assets for this many days (default: %(default)s).",
    )
    parser.add_argument(
        "--keep-remote-days",
        type=int,
        default=7,
        help="Retain remote AI story assets for this many days (default: %(default)s).",
    )
    return parser


if __name__ == "__main__":
    run(build_arg_parser().parse_args())
