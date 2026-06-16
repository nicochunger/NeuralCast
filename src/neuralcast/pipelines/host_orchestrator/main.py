"""Dynamic AI host orchestrator that injects station voice segments into AzuraCast."""

from __future__ import annotations

import argparse
import os
import random
import pathlib
import sys
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

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
    archetype_settings_for_station,
    cadence_settings_for_station,
    configure_station_file_logging,
    configure_logging,
    lead_time_seconds_for_archetype,
    log_segment_event,
)
from .generation import (
    build_tts_instructions,
    generate_archetype_script,
    resolve_station_personality,
    station_name_for_generation,
)
from .models import (
    Archetype,
    QueueTrack,
    ScheduleContext,
    StationPersonality,
    TrackFocus,
    TrackMetadata,
    supports_track_focus,
)
from .schedule import (
    load_schedule_state_payload,
    prune_schedule_block_mentions,
    resolve_schedule_context_for_upcoming_break,
    seconds_until_schedule_block_change,
    should_force_block_intro,
)
from .state import (
    StationLock,
    apply_success_state_update,
    assemble_banned_list,
    choose_angle,
    choose_hook,
    choose_weighted_archetype,
    legal_archetypes_for_remaining,
    load_state,
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


class ArgumentValidationError(ValueError):
    """Raised when a host-orchestrator CLI argument combination is invalid."""


@dataclass(frozen=True)
class StationRuntime:
    station_dir: pathlib.Path
    client: AzuraCastClient
    station_id: int
    generation_station_name: str
    station_personality: StationPersonality
    schedule_state: Mapping[str, Any] | None


@dataclass(frozen=True)
class PlaybackContext:
    current_track: QueueTrack
    current_remaining: int
    current_key: str
    listener_count: int | None


@dataclass(frozen=True)
class QueueContext:
    upcoming_tracks: Sequence[QueueTrack]
    next_track: QueueTrack
    schedule_context: ScheduleContext | None
    schedule_reference_ts: float


@dataclass(frozen=True)
class GenerationContext:
    selected_archetype: Archetype
    angle: str | None
    hook: str
    banned_list: Sequence[str]
    current_meta: TrackMetadata
    next_meta: TrackMetadata
    forced_news_mode: bool


def validate_runtime_args(args: argparse.Namespace) -> TrackFocus | None:
    """Validate cross-argument constraints and return any forced track focus."""

    force_track_focus = getattr(args, "force_track_focus", None)
    if not force_track_focus:
        return None

    if not args.force_archetype:
        raise ArgumentValidationError(
            "--force-track-focus requires --force-archetype."
        )

    forced_archetype = Archetype(args.force_archetype)
    if not supports_track_focus(forced_archetype):
        supported = ", ".join(
            archetype.value for archetype in Archetype if supports_track_focus(archetype)
        )
        raise ArgumentValidationError(
            "--force-track-focus is only supported for forceable story archetypes: "
            f"{supported}."
        )

    return TrackFocus(force_track_focus)


def _load_required_api_key() -> str:
    load_dotenv()
    api_key = os.getenv("AZURACAST_API_KEY")
    if not api_key:
        raise RuntimeError("AZURACAST_API_KEY is not set in the environment.")
    return api_key


def _load_station_runtime(
    args: argparse.Namespace,
    api_key: str,
    station_dir: pathlib.Path,
    schedule_block_mentions: Mapping[str, Mapping[str, Any]],
) -> StationRuntime:
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
        raise RuntimeError("Station payload missing station ID; cannot queue media.")

    station_name = derive_station_display_name(station_payload, fallback=args.station)
    generation_station_name = station_name_for_generation(args.station, station_name)
    station_personality = resolve_station_personality(args.station)
    LOGGER.info("[station] Personality profile active: %s", args.station)

    schedule_state = load_schedule_state_payload(station_dir)
    if schedule_state is not None:
        LOGGER.info(
            "[schedule] Loaded weekly schedule state for context (week_start=%s).",
            schedule_state.get("week_start_local_date") or "unknown",
        )

    return StationRuntime(
        station_dir=station_dir,
        client=client,
        station_id=station_id,
        generation_station_name=generation_station_name,
        station_personality=station_personality,
        schedule_state=schedule_state,
    )


def _fetch_playback_context(
    args: argparse.Namespace,
    client: AzuraCastClient,
    state,
) -> PlaybackContext | None:
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
        return None

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
            return None
        if listener_count < args.min_listeners:
            LOGGER.info(
                "[gate] Only %s listener(s) connected (< %s); skipping cycle.",
                listener_count,
                args.min_listeners,
            )
            return None

    if current_remaining is None:
        LOGGER.info(
            "[gate] Current remaining time unavailable; skipping for lead-time safety."
        )
        return None
    if current_remaining < LEAD_TIME_SECONDS:
        LOGGER.info(
            "[gate] Current track has only %ss remaining (< %ss); skipping cycle.",
            current_remaining,
            LEAD_TIME_SECONDS,
        )
        return None

    return PlaybackContext(
        current_track=current_track,
        current_remaining=current_remaining,
        current_key=current_key,
        listener_count=listener_count,
    )


def _fetch_queue_context(
    args: argparse.Namespace,
    client: AzuraCastClient,
    playback: PlaybackContext,
    schedule_state: Mapping[str, Any] | None,
    mention_state: Mapping[str, Mapping[str, Any]],
) -> QueueContext | None:
    queue_payload = run_with_retries(
        "Fetch queue",
        lambda: client.get_upcoming_queue(args.station),
    )
    queue_tracks = parse_queue_tracks(queue_payload)
    upcoming_tracks = choose_upcoming_tracks(
        current=playback.current_track,
        queue_tracks=queue_tracks,
        limit=4,
    )
    next_track = upcoming_tracks[0] if upcoming_tracks else None
    if next_track is None:
        LOGGER.info("[queue] No suitable next track found; skipping cycle.")
        return None

    LOGGER.info("[queue] Next track: %s - %s", next_track.artist, next_track.title)
    LOGGER.info(
        "[queue] Upcoming queue snapshot (%s): %s",
        len(upcoming_tracks),
        " | ".join(f"{track.artist} - {track.title}" for track in upcoming_tracks),
    )
    schedule_reference_ts = now_ts() + max(0, playback.current_remaining)
    schedule_context = resolve_schedule_context_for_upcoming_break(
        schedule_state=schedule_state,
        ts_now=now_ts(),
        ts_break=schedule_reference_ts,
        mention_state=mention_state,
        next_track=next_track,
    )
    if schedule_context is not None:
        LOGGER.info(
            "[schedule] Next-track boundary block='%s' phase=%s mention_intent=%s",
            schedule_context.section_label,
            schedule_context.phase,
            schedule_context.mention_intent or "none",
        )

    return QueueContext(
        upcoming_tracks=upcoming_tracks,
        next_track=next_track,
        schedule_context=schedule_context,
        schedule_reference_ts=schedule_reference_ts,
    )


def _resolve_effective_forced_archetype(
    args: argparse.Namespace,
    schedule_context: ScheduleContext | None,
) -> tuple[Archetype | None, bool]:
    forced_archetype = (
        Archetype(args.force_archetype) if args.force_archetype else None
    )
    auto_forced_block_intro = False
    if should_force_block_intro(schedule_context, forced_archetype):
        forced_archetype = Archetype.BLOCK_INTRO
        auto_forced_block_intro = True
        LOGGER.info(
            "[schedule] Block start window active for '%s'; forcing block_intro archetype.",
            schedule_context.section_label if schedule_context else "n/d",
        )
    return forced_archetype, auto_forced_block_intro


def _select_archetype(
    args: argparse.Namespace,
    state,
    playback: PlaybackContext,
    queue_context: QueueContext,
    forced_archetype: Archetype | None,
    auto_forced_block_intro: bool,
    forced_track_focus: TrackFocus | None,
    rng: random.Random,
) -> Archetype | None:
    if forced_archetype is not None:
        required_lead_time = lead_time_seconds_for_archetype(forced_archetype)
        if playback.current_remaining < required_lead_time:
            LOGGER.info(
                "[gate] Forced archetype %s requires %ss lead time; current track has %ss remaining. Skipping cycle.",
                forced_archetype.value,
                required_lead_time,
                playback.current_remaining,
            )
            return None

    if forced_archetype is None:
        eligible, wait_reason = should_speak_now(state, playback.current_key, now_ts())
        if not eligible:
            LOGGER.info("[gate] Wait gate closed: %s", wait_reason)
            return None
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
        if forced_track_focus is not None:
            LOGGER.info(
                "[gate] Forced track focus active: %s.",
                forced_track_focus.value,
            )
        return forced_archetype

    seconds_until_block_change = seconds_until_schedule_block_change(
        queue_context.schedule_context,
        queue_context.schedule_reference_ts,
    )
    legal = legal_archetypes_for_remaining(
        state,
        now_ts(),
        current_remaining=playback.current_remaining,
        seconds_until_block_change=seconds_until_block_change,
        disabled_archetypes=archetype_settings_for_station(
            args.station
        ).disabled_archetypes,
    )
    if legal:
        selected_archetype = choose_weighted_archetype(legal, state, rng)
        LOGGER.info(
            "[archetype] Legal archetypes: %s (seconds_until_block_change=%s)",
            [item.value for item in legal],
            (
                "n/a"
                if seconds_until_block_change is None
                else round(seconds_until_block_change, 1)
            ),
        )
        return selected_archetype

    LOGGER.warning(
        "[archetype] No legal archetypes available after cooldowns/block-change guards; using ultra_minimal."
    )
    return Archetype.ULTRA_MINIMAL


def _build_generation_context(
    args: argparse.Namespace,
    runtime: StationRuntime,
    playback: PlaybackContext,
    queue_context: QueueContext,
    state,
    selected_archetype: Archetype,
    forced_archetype: Archetype | None,
    auto_forced_block_intro: bool,
    rng: random.Random,
) -> GenerationContext:
    angle = choose_angle(selected_archetype, state, rng)
    hook = choose_hook(selected_archetype, state, rng)
    banned_list = assemble_banned_list(state)

    metadata_cache = load_station_track_metadata(runtime.station_dir)
    current_meta = metadata_cache.get(playback.current_key, TrackMetadata())
    next_meta = metadata_cache.get(
        track_key(queue_context.next_track.artist, queue_context.next_track.title),
        TrackMetadata(),
    )

    LOGGER.info(
        "[generation] archetype=%s | angle=%s | hook=%s",
        selected_archetype.value,
        angle or "none",
        hook,
    )
    if selected_archetype == Archetype.NEWS:
        LOGGER.info("[news] Archetype selected; topic rolls will be logged in generation.")

    return GenerationContext(
        selected_archetype=selected_archetype,
        angle=angle,
        hook=hook,
        banned_list=banned_list,
        current_meta=current_meta,
        next_meta=next_meta,
        forced_news_mode=forced_archetype == Archetype.NEWS,
    )


def _publish_segment(
    args: argparse.Namespace,
    runtime: StationRuntime,
    playback: PlaybackContext,
    queue_context: QueueContext,
    generation_context: GenerationContext,
    archetype_used: Archetype,
    news_segment,
    script_text: str,
    assets,
    state,
    rng: random.Random,
) -> None:
    upload_response = run_with_retries(
        "Upload media",
        lambda: runtime.client.upload_media(
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
        title=f"AI Host: {playback.current_track.title}",
        duration=story_duration,
    )

    telnet_response = run_with_retries(
        "Queue media via telnet",
        lambda: runtime.client.send_telnet_command(runtime.station_id, telnet_command),
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
        current_track_key=playback.current_key,
        current_remaining=playback.current_remaining,
        archetype_used=archetype_used,
        hook=generation_context.hook,
        angle=generation_context.angle,
        news_segment=news_segment,
        script_text=script_text,
        schedule_context=queue_context.schedule_context,
        rng=rng,
        cadence_settings=cadence_settings_for_station(args.station),
    )

    expected_play_at_utc = iso_utc(success_ts + max(0, playback.current_remaining))
    news_topics_text = None
    if news_segment is not None:
        topic_list = [story.topic for story in news_segment.stories if story.topic]
        if topic_list:
            news_topics_text = ",".join(topic_list)
    log_segment_event(
        station=args.station,
        archetype=archetype_used.value,
        current_track=f"{playback.current_track.artist} - {playback.current_track.title}",
        next_track=f"{queue_context.next_track.artist} - {queue_context.next_track.title}",
        queued_request_id=request_id,
        expected_play_at_utc=expected_play_at_utc,
        audio_path=str(assets.audio_path),
        remote_path=assets.remote_path,
        schedule_section=(
            queue_context.schedule_context.section_label
            if queue_context.schedule_context is not None
            else None
        ),
        mention_intent=(
            queue_context.schedule_context.mention_intent
            if queue_context.schedule_context is not None
            else None
        ),
        news_topics=news_topics_text,
    )
    LOGGER.info(
        "[segment] Success | archetype=%s | request_id=%s | expected_play_at_utc=%s",
        archetype_used.value,
        request_id or "n/a",
        expected_play_at_utc,
    )

    cleanup_local_stories(args.station, args.keep_local_days)
    cleanup_remote_stories(runtime.client, args.station, args.keep_remote_days)


def run(args: argparse.Namespace) -> None:
    configure_logging()
    forced_track_focus = validate_runtime_args(args)
    LOGGER.info("%s", "=" * 84)
    LOGGER.info(
        "[cycle] Invocation received | station=%s | dry_run=%s | force_archetype=%s | force_track_focus=%s",
        args.station,
        args.dry_run,
        args.force_archetype or "none",
        forced_track_focus.value if forced_track_focus is not None else "none",
    )

    api_key = _load_required_api_key()
    rng = random.Random()
    cycle_ts = now_ts()
    station_dir, state_path, lock_path = station_state_paths(args.station)
    metadata_dir = station_dir / "metadata"
    main_log_path, segment_log_path = configure_station_file_logging(metadata_dir)
    lock = StationLock(lock_path)
    if not lock.acquire():
        return

    LOGGER.info(
        "[cycle] Starting orchestrator cycle | station=%s | dry_run=%s",
        args.station,
        args.dry_run,
    )
    LOGGER.info(
        "[log] File logs active | main=%s | segments=%s",
        main_log_path,
        segment_log_path,
    )

    cadence_settings = cadence_settings_for_station(args.station)
    state = load_state(state_path, cycle_ts, rng, cadence_settings)
    LOGGER.info("[state] Loaded orchestrator state: %s", state_path)
    LOGGER.info(
        "[cadence] State snapshot | songs_since_last_spoken=%s | songs_until_next_speak=%s | next_deadline=%s | wait_range=%s-%s | deadline_minutes=%s | cooldown_multiplier=%.2f",
        state.songs_since_last_spoken,
        state.songs_until_next_speak,
        iso_utc(state.next_speak_deadline_ts),
        cadence_settings.wait_range_songs[0],
        cadence_settings.wait_range_songs[1],
        cadence_settings.speak_deadline_minutes,
        cadence_settings.cooldown_multiplier,
    )

    try:
        state.schedule_block_mentions = prune_schedule_block_mentions(
            state.schedule_block_mentions, now_ts()
        )
        runtime = _load_station_runtime(
            args=args,
            api_key=api_key,
            station_dir=station_dir,
            schedule_block_mentions=state.schedule_block_mentions,
        )
        playback = _fetch_playback_context(args, runtime.client, state)
        if playback is None:
            return

        queue_context = _fetch_queue_context(
            args=args,
            client=runtime.client,
            playback=playback,
            schedule_state=runtime.schedule_state,
            mention_state=state.schedule_block_mentions,
        )
        if queue_context is None:
            return

        forced_archetype, auto_forced_block_intro = _resolve_effective_forced_archetype(
            args=args,
            schedule_context=queue_context.schedule_context,
        )
        selected_archetype = _select_archetype(
            args=args,
            state=state,
            playback=playback,
            queue_context=queue_context,
            forced_archetype=forced_archetype,
            auto_forced_block_intro=auto_forced_block_intro,
            forced_track_focus=forced_track_focus,
            rng=rng,
        )
        if selected_archetype is None:
            return

        generation_context = _build_generation_context(
            args=args,
            runtime=runtime,
            playback=playback,
            queue_context=queue_context,
            state=state,
            selected_archetype=selected_archetype,
            forced_archetype=forced_archetype,
            auto_forced_block_intro=auto_forced_block_intro,
            rng=rng,
        )

        script_text, news_segment, archetype_used = generate_archetype_script(
            archetype=generation_context.selected_archetype,
            station_name=runtime.generation_station_name,
            personality=runtime.station_personality,
            current_track=playback.current_track,
            next_track=queue_context.next_track,
            upcoming_tracks=queue_context.upcoming_tracks,
            current_meta=generation_context.current_meta,
            next_meta=generation_context.next_meta,
            angle=generation_context.angle,
            hook=generation_context.hook,
            banned_list=generation_context.banned_list,
            schedule_context=queue_context.schedule_context,
            state=state,
            rng=rng,
            forced_mode=generation_context.forced_news_mode,
            forced_track_focus=forced_track_focus,
        )

        if not script_text.strip():
            raise RuntimeError("Generated script was empty after cleanup.")

        tts_instructions = build_tts_instructions(runtime.station_personality)
        assets = run_with_retries(
            "TTS synthesis",
            lambda: ensure_story_assets(
                station_slug=args.station,
                current_track=playback.current_track,
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

        _publish_segment(
            args=args,
            runtime=runtime,
            playback=playback,
            queue_context=queue_context,
            generation_context=generation_context,
            archetype_used=archetype_used,
            news_segment=news_segment,
            script_text=script_text,
            assets=assets,
            state=state,
            rng=rng,
        )

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
        "--force-track-focus",
        choices=[focus.value for focus in TrackFocus],
        help=(
            "When used with --force-archetype for short_story, album_spotlight, "
            "era_snapshot, or deep_dive, force the archetype to focus on the "
            "current or next track instead of choosing randomly."
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
    parser = build_arg_parser()
    parsed_args = parser.parse_args()
    try:
        run(parsed_args)
    except ArgumentValidationError as exc:
        parser.error(str(exc))
