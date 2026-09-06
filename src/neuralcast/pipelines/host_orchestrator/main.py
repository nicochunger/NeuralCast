"""Dynamic AI host orchestrator that injects station voice segments into AzuraCast."""

from __future__ import annotations

import argparse
import random
import pathlib
import sys
from dataclasses import dataclass
from typing import Any, Callable, Literal, Mapping, Sequence

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

from neuralcast.config import ALLOWED_STATION_SLUGS
from neuralcast.services.azuracast_config import (
    AzuraCastSettings,
    load_azuracast_settings,
    resolve_azuracast_station,
)

from .channels import HostChannel, host_channel_keys, resolve_host_channel
from .assets import (
    cleanup_local_stories,
    cleanup_remote_stories,
    ensure_story_assets,
    load_station_track_metadata,
)
from .config import (
    LEAD_TIME_SECONDS,
    LOGGER,
    cadence_settings_for_station,
    configure_station_file_logging,
    configure_logging,
    lead_time_seconds_for_archetype,
    log_segment_event,
)
from .generation import (
    build_tts_instructions,
    generate_archetype_script,
    station_name_for_generation,
)
from .models import (
    Archetype,
    GeneratedSegmentMetadata,
    NewsSegment,
    QueueTrack,
    ScheduleContext,
    StationPersonality,
    StoryAssets,
    TrackFocus,
    TrackMetadata,
    supports_track_focus,
)
from .presentation import build_segment_title
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
    extract_upload_media_id,
    extract_upload_song_id,
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
    channel: HostChannel
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


@dataclass(frozen=True)
class HostCycleRequest:
    station: str
    base_url: str | None
    channel: str | None = None
    dry_run: bool = False
    min_listeners: int = 1
    force_archetype: Archetype | None = None
    force_track_focus: TrackFocus | None = None
    verify_tls: bool = False
    keep_local_days: int = 3
    keep_remote_days: int = 7
    scheduled_block_intros_only: bool = False


@dataclass(frozen=True)
class HostCycleResult:
    status: Literal["skipped", "generated", "published"]
    reason: str | None
    station: str
    current_track: QueueTrack | None = None
    next_track: QueueTrack | None = None
    selected_archetype: Archetype | None = None
    used_archetype: Archetype | None = None
    segment_title: str | None = None
    assets: StoryAssets | None = None
    queued_request_id: str | None = None
    expected_play_at_utc: str | None = None
    channel: str | None = None


@dataclass(frozen=True)
class PublishResult:
    queued_request_id: str | None
    expected_play_at_utc: str


def validate_runtime_args(args: argparse.Namespace) -> TrackFocus | None:
    """Validate cross-argument constraints and return any forced track focus."""

    scheduled_block_intros_only = bool(
        getattr(args, "scheduled_block_intros_only", False)
    )
    if scheduled_block_intros_only and getattr(args, "force_archetype", None):
        raise ArgumentValidationError(
            "--scheduled-block-intros-only cannot be combined with --force-archetype."
        )

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


def _load_runtime_settings(
    base_url: str | None,
    station: str | None,
) -> AzuraCastSettings:
    load_dotenv()
    return load_azuracast_settings(base_url=base_url, station=station)


def _load_station_runtime(
    args: argparse.Namespace,
    channel: HostChannel,
    api_key: str,
    station_dir: pathlib.Path,
    schedule_block_mentions: Mapping[str, Mapping[str, Any]],
    client_factory: Callable[[str, str, bool], AzuraCastClient] = AzuraCastClient,
) -> StationRuntime:
    client = client_factory(args.base_url.rstrip("/"), api_key, args.verify_tls)

    if channel.azuracast_station_id is not None:
        station_id = channel.azuracast_station_id
        station_name = args.station
        LOGGER.info(
            "[station] Using configured AzuraCast station ID %s.", station_id
        )
    else:
        stations = run_with_retries("Fetch stations", client.get_stations)
        station_payload = choose_station_payload(stations, args.station)
        station_id_raw = station_payload.get("id")
        station_id = int(station_id_raw) if station_id_raw is not None else None
        if station_id is None:
            raise RuntimeError("Station payload missing station ID; cannot queue media.")
        station_name = derive_station_display_name(
            station_payload, fallback=args.station
        )
    generation_station_name = station_name_for_generation(
        channel.brand.personality_station, station_name
    )
    station_personality = StationPersonality(
        script_profile=channel.script_style_override or channel.brand.script_style,
        tts_profile=channel.brand.tts_style,
    )
    LOGGER.info(
        "[station] Channel=%s target=%s brand=%s locale=%s media_owner=%s",
        channel.key,
        channel.azuracast_station,
        channel.brand.key,
        channel.locale.tag,
        channel.media_owner_station,
    )

    schedule_state = load_schedule_state_payload(station_dir)
    if schedule_state is not None:
        LOGGER.info(
            "[schedule] Loaded weekly schedule state for context (week_start=%s).",
            schedule_state.get("week_start_local_date") or "unknown",
        )

    return StationRuntime(
        channel=channel,
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
    *,
    update_seen_state: bool = True,
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
    if update_seen_state:
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
        upcoming_tracks=upcoming_tracks,
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
    runtime: StationRuntime | None = None,
) -> Archetype | None:
    archetype_policy = runtime.channel.archetype_policy if runtime else None
    if forced_archetype is not None:
        required_lead_time = lead_time_seconds_for_archetype(
            forced_archetype, archetype_policy
        )
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
        archetype_policy=archetype_policy,
    )
    if legal:
        selected_archetype = choose_weighted_archetype(
            legal, state, rng, archetype_policy
        )
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
    hook = choose_hook(
        selected_archetype, state, rng, runtime.channel.archetype_policy
    )
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
    segment_title: str,
    news_segment,
    script_text: str,
    assets,
    state,
    rng: random.Random,
) -> PublishResult:
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
    media_id = extract_upload_media_id(upload_response)
    if not media_id:
        raise RuntimeError("Upload response missing media ID.")
    song_id = extract_upload_song_id(upload_response)
    full_media_path = f"{runtime.channel.liquidsoap_media_root}/{upload_path}"
    telnet_command = build_request_command(
        media_full_path=full_media_path,
        title=segment_title,
        duration=story_duration,
        media_id=media_id,
        song_id=song_id,
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
        cadence_settings=cadence_settings_for_station(
            runtime.channel.cadence_profile
        ),
        archetype_policy=runtime.channel.archetype_policy,
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
        segment_title=segment_title,
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

    cleanup_local_stories(runtime.channel.key, args.keep_local_days)
    cleanup_remote_stories(
        runtime.client,
        args.station,
        args.keep_remote_days,
        remote_prefix=runtime.channel.remote_prefix,
    )
    return PublishResult(
        queued_request_id=request_id,
        expected_play_at_utc=expected_play_at_utc,
    )


@dataclass(frozen=True)
class HostRuntimeDependencies:
    """Coarse side-effect boundaries for one host-orchestrator cycle."""

    load_settings: Callable[[str | None, str | None], AzuraCastSettings] = (
        _load_runtime_settings
    )
    configure_logging: Callable[[], None] = configure_logging
    create_client: Callable[[str, str, bool], AzuraCastClient] = (
        lambda base_url, api_key, verify_tls: AzuraCastClient(
            base_url=base_url,
            api_key=api_key,
            verify_tls=verify_tls,
        )
    )
    station_state_paths: Callable[
        [str], tuple[pathlib.Path, pathlib.Path, pathlib.Path]
    ] = station_state_paths
    configure_station_file_logging: Callable[
        [pathlib.Path], tuple[pathlib.Path, pathlib.Path]
    ] = configure_station_file_logging
    create_lock: Callable[[pathlib.Path], StationLock] = StationLock
    load_state: Callable[..., Any] = load_state
    save_state: Callable[[pathlib.Path, Any], None] = save_state_atomic
    make_rng: Callable[[], random.Random] = random.Random
    now: Callable[[], float] = now_ts
    generate_script: Callable[
        ..., tuple[str, GeneratedSegmentMetadata, Archetype]
    ] = generate_archetype_script
    create_story_assets: Callable[..., StoryAssets] = ensure_story_assets
    publish_segment: Callable[..., PublishResult] = _publish_segment


def _args_from_cycle_request(request: HostCycleRequest) -> argparse.Namespace:
    channel = resolve_host_channel(
        channel_key=request.channel,
        station_slug=request.station,
    )
    return argparse.Namespace(
        channel=channel.key,
        station=channel.azuracast_station,
        content_station=channel.content_station,
        cadence_profile=channel.cadence_profile,
        archetype_profile=channel.archetype_profile,
        base_url=request.base_url,
        dry_run=request.dry_run,
        min_listeners=request.min_listeners,
        force_archetype=(
            request.force_archetype.value if request.force_archetype else None
        ),
        force_track_focus=(
            request.force_track_focus.value if request.force_track_focus else None
        ),
        verify_tls=request.verify_tls,
        keep_local_days=request.keep_local_days,
        keep_remote_days=request.keep_remote_days,
        scheduled_block_intros_only=request.scheduled_block_intros_only,
    )


def _cycle_request_from_args(args: argparse.Namespace) -> HostCycleRequest:
    return HostCycleRequest(
        station=args.station,
        base_url=args.base_url,
        channel=getattr(args, "channel", None),
        dry_run=args.dry_run,
        min_listeners=args.min_listeners,
        force_archetype=(
            Archetype(args.force_archetype) if args.force_archetype else None
        ),
        force_track_focus=(
            TrackFocus(args.force_track_focus) if args.force_track_focus else None
        ),
        verify_tls=args.verify_tls,
        keep_local_days=args.keep_local_days,
        keep_remote_days=args.keep_remote_days,
        scheduled_block_intros_only=bool(
            getattr(args, "scheduled_block_intros_only", False)
        ),
    )


class HostOrchestratorRuntime:
    def __init__(
        self,
        dependencies: HostRuntimeDependencies | None = None,
    ) -> None:
        self.dependencies = dependencies or HostRuntimeDependencies()

    def run_cycle(self, request: HostCycleRequest) -> HostCycleResult:
        deps = self.dependencies
        args = _args_from_cycle_request(request)
        channel = resolve_host_channel(channel_key=args.channel)
        deps.configure_logging()
        forced_track_focus = validate_runtime_args(args)
        LOGGER.info("%s", "=" * 84)
        LOGGER.info(
            "[cycle] Invocation received | channel=%s | station=%s | locale=%s | dry_run=%s | force_archetype=%s | force_track_focus=%s",
            channel.key,
            args.station,
            channel.locale.tag,
            args.dry_run,
            args.force_archetype or "none",
            forced_track_focus.value if forced_track_focus is not None else "none",
        )

        settings = deps.load_settings(args.base_url, args.station)
        args.base_url = settings.base_url
        args.station = settings.station
        api_key = settings.api_key
        rng = deps.make_rng()
        cycle_ts = deps.now()
        station_dir, state_path, lock_path = deps.station_state_paths(channel.key)
        metadata_dir = state_path.parent
        metadata_dir.mkdir(parents=True, exist_ok=True)
        main_log_path, segment_log_path = deps.configure_station_file_logging(
            metadata_dir
        )
        lock = deps.create_lock(lock_path)
        if not lock.acquire():
            return HostCycleResult(
                status="skipped",
                reason="lock active",
                station=args.station,
                channel=channel.key,
            )

        state = None
        try:
            LOGGER.info(
                "[cycle] Starting orchestrator cycle | station=%s | dry_run=%s",
                args.station,
                args.dry_run,
            )
            LOGGER.info(
                "[policy] Archetype profile=%s | effective_policy=%s | news_topics=%s | concert_countries=%s",
                channel.archetype_profile,
                channel.archetype_policy.name,
                list(
                    channel.archetype_policy.for_archetype(
                        Archetype.NEWS
                    ).news.topic_ids
                ),
                list(
                    channel.archetype_policy.for_archetype(
                        Archetype.CONCERT_CHECK
                    ).concert_check.country_codes
                ),
            )
            LOGGER.info(
                "[log] File logs active | main=%s | segments=%s",
                main_log_path,
                segment_log_path,
            )

            cadence_settings = cadence_settings_for_station(
                channel.cadence_profile
            )
            state = deps.load_state(state_path, cycle_ts, rng, cadence_settings)
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

            state.schedule_block_mentions = prune_schedule_block_mentions(
                state.schedule_block_mentions, deps.now()
            )
            runtime = _load_station_runtime(
                args=args,
                channel=channel,
                api_key=api_key,
                station_dir=station_dir,
                schedule_block_mentions=state.schedule_block_mentions,
                client_factory=deps.create_client,
            )
            playback = _fetch_playback_context(
                args,
                runtime.client,
                state,
                update_seen_state=not args.scheduled_block_intros_only,
            )
            if playback is None:
                return HostCycleResult(
                    status="skipped",
                    reason="playback unavailable",
                    station=args.station,
                    channel=channel.key,
                )

            queue_context = _fetch_queue_context(
                args=args,
                client=runtime.client,
                playback=playback,
                schedule_state=runtime.schedule_state,
                mention_state=state.schedule_block_mentions,
            )
            if queue_context is None:
                return HostCycleResult(
                    status="skipped",
                    reason="queue unavailable",
                    station=args.station,
                    channel=channel.key,
                    current_track=playback.current_track,
                )

            (
                forced_archetype,
                auto_forced_block_intro,
            ) = _resolve_effective_forced_archetype(
                args=args,
                schedule_context=queue_context.schedule_context,
            )
            if args.scheduled_block_intros_only and not auto_forced_block_intro:
                LOGGER.info(
                    "[schedule] No valid upcoming block intro position; schedule-only cycle is complete."
                )
                return HostCycleResult(
                    status="skipped",
                    reason="no scheduled block intro due",
                    station=args.station,
                    channel=channel.key,
                    current_track=playback.current_track,
                    next_track=queue_context.next_track,
                )
            selected_archetype = _select_archetype(
                args=args,
                runtime=runtime,
                state=state,
                playback=playback,
                queue_context=queue_context,
                forced_archetype=forced_archetype,
                auto_forced_block_intro=auto_forced_block_intro,
                forced_track_focus=forced_track_focus,
                rng=rng,
            )
            if selected_archetype is None:
                return HostCycleResult(
                    status="skipped",
                    reason="archetype gate closed",
                    station=args.station,
                    channel=channel.key,
                    current_track=playback.current_track,
                    next_track=queue_context.next_track,
                )

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

            script_text, segment_metadata_raw, archetype_used = deps.generate_script(
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
                locale=channel.locale,
                archetype_policy=channel.archetype_policy,
            )

            if isinstance(segment_metadata_raw, GeneratedSegmentMetadata):
                segment_metadata = segment_metadata_raw
            elif isinstance(segment_metadata_raw, NewsSegment):
                # Keep compatibility with custom/older generator dependencies
                # that returned NewsSegment directly as the second tuple item.
                segment_metadata = GeneratedSegmentMetadata(
                    news_segment=segment_metadata_raw
                )
            else:
                segment_metadata = GeneratedSegmentMetadata()
            news_segment = segment_metadata.news_segment

            if not script_text.strip():
                raise RuntimeError("Generated script was empty after cleanup.")

            segment_title = build_segment_title(
                archetype=archetype_used,
                current_track=playback.current_track,
                next_track=queue_context.next_track,
                upcoming_tracks=queue_context.upcoming_tracks,
                current_meta=generation_context.current_meta,
                next_meta=generation_context.next_meta,
                segment_metadata=segment_metadata,
                schedule_context=queue_context.schedule_context,
                locale=channel.locale,
            )
            LOGGER.info("[segment] Listener-facing title: %s", segment_title)

            tts_instructions = build_tts_instructions(
                runtime.station_personality,
                locale=channel.locale,
                override_path=channel.tts_instructions_override_path,
            )
            assets = run_with_retries(
                "TTS synthesis",
                lambda: deps.create_story_assets(
                    station_slug=channel.content_station,
                    current_track=playback.current_track,
                    archetype=archetype_used,
                    script_text=script_text,
                    tts_instructions=tts_instructions,
                    segment_title=segment_title,
                    channel_key=channel.key,
                    cover_station=channel.brand.cover_station,
                    remote_prefix=channel.remote_prefix,
                    tts_voice=channel.locale.tts_voice,
                    language=channel.locale.tag,
                ),
            )
            LOGGER.info("[assets] Script saved: %s", assets.text_path)
            LOGGER.info("[assets] Audio saved: %s", assets.audio_path)

            if args.dry_run:
                LOGGER.info(
                    "[dry-run] Skipping upload/injection; cadence and cooldowns are not consumed."
                )
                return HostCycleResult(
                    status="generated",
                    reason=None,
                    station=args.station,
                    channel=channel.key,
                    current_track=playback.current_track,
                    next_track=queue_context.next_track,
                    selected_archetype=generation_context.selected_archetype,
                    used_archetype=archetype_used,
                    segment_title=segment_title,
                    assets=assets,
                )

            publish_result = deps.publish_segment(
                args=args,
                runtime=runtime,
                playback=playback,
                queue_context=queue_context,
                generation_context=generation_context,
                archetype_used=archetype_used,
                segment_title=segment_title,
                news_segment=news_segment,
                script_text=script_text,
                assets=assets,
                state=state,
                rng=rng,
            )
            return HostCycleResult(
                status="published",
                reason=None,
                station=args.station,
                channel=channel.key,
                current_track=playback.current_track,
                next_track=queue_context.next_track,
                selected_archetype=generation_context.selected_archetype,
                used_archetype=archetype_used,
                segment_title=segment_title,
                assets=assets,
                queued_request_id=publish_result.queued_request_id,
                expected_play_at_utc=publish_result.expected_play_at_utc,
            )

        finally:
            try:
                if state is not None:
                    deps.save_state(state_path, state)
                    LOGGER.info("[state] Saved orchestrator state: %s", state_path)
            finally:
                lock.release()


def run_host_orchestrator_cycle(
    request: HostCycleRequest,
    *,
    runtime: HostOrchestratorRuntime | None = None,
) -> HostCycleResult:
    return (runtime or HostOrchestratorRuntime()).run_cycle(request)


def run(args: argparse.Namespace) -> None:
    run_host_orchestrator_cycle(_cycle_request_from_args(args))


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Dynamic AI host orchestrator for AzuraCast: stateful cadence, archetype "
            "selection, and spoken segment injection."
        )
    )
    parser.add_argument(
        "--base-url",
        help=(
            "Base URL for AzuraCast. If omitted, reads AZURACAST_BASE_URL "
            "from the environment/.env file (required)."
        ),
    )
    parser.add_argument(
        "-s",
        "--station",
        choices=ALLOWED_STATION_SLUGS,
        default=resolve_azuracast_station(),
        help="AzuraCast station shortcode (default: %(default)s).",
    )
    parser.add_argument(
        "--channel",
        choices=host_channel_keys(),
        help=(
            "Configured broadcast channel. Overrides --station and selects the "
            "target AzuraCast station, content brand, locale, state, and media path."
        ),
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
        "--scheduled-block-intros-only",
        action="store_true",
        help=(
            "Run only when schedule context identifies a valid upcoming block start, "
            "and generate only the automatic block_intro archetype. Intended for a "
            "frequent companion cron alongside a slower normal host cycle."
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
