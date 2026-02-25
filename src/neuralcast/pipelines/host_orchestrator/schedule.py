"""Schedule-context helpers for host orchestrator."""

from __future__ import annotations

import datetime as dt
import json
import pathlib
import re
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

from .config import (
    LOGGER,
    SCHEDULE_BLOCK_INTRO_LOOKAHEAD_MINUTES,
    SCHEDULE_BLOCK_INTRO_BOUNDARY_GRACE_SECONDS,
    SCHEDULE_MENTION_MAX_ENTRIES,
    SCHEDULE_MENTION_RETENTION_DAYS,
    SCHEDULE_MID_PROGRESS_RANGE,
    SCHEDULE_START_WINDOW_MINUTES,
    SCHEDULE_STATE_FILENAME,
    SYSTEM_TZ,
    log_schedule_debug,
)
from .models import (
    Archetype,
    QueueTrack,
    ScheduleContext,
)


def resolve_station_metadata_file(
    station_dir: pathlib.Path, filename: str
) -> pathlib.Path:
    metadata_path = station_dir / "metadata" / filename
    if metadata_path.exists():
        return metadata_path
    legacy_path = station_dir / "playlists" / filename
    if legacy_path.exists():
        return legacy_path
    return metadata_path


def _resolve_schedule_timezone(schedule_state: Mapping[str, Any]) -> ZoneInfo:
    timezone_name = str(schedule_state.get("timezone") or "").strip()
    try:
        return ZoneInfo(timezone_name) if timezone_name else SYSTEM_TZ
    except Exception:  # noqa: BLE001
        return SYSTEM_TZ


def parse_schedule_hhmm(value: str, allow_24: bool = False) -> Optional[int]:
    text = str(value or "").strip()
    match = re.fullmatch(r"([01]?\d|2[0-3]):([0-5]\d)", text)
    if match:
        return int(match.group(1)) * 60 + int(match.group(2))
    if allow_24 and text == "24:00":
        return 24 * 60
    return None


def schedule_local_datetime(
    date_local: str,
    time_local: str,
    tz: ZoneInfo,
) -> Optional[dt.datetime]:
    try:
        date_value = dt.date.fromisoformat(date_local)
    except ValueError:
        return None

    minutes = parse_schedule_hhmm(time_local, allow_24=True)
    if minutes is None:
        return None

    if minutes == 24 * 60:
        return dt.datetime.combine(
            date_value + dt.timedelta(days=1),
            dt.time(hour=0, minute=0),
            tzinfo=tz,
        )

    hour = minutes // 60
    minute = minutes % 60
    return dt.datetime.combine(
        date_value,
        dt.time(hour=hour, minute=minute),
        tzinfo=tz,
    )


def _parse_schedule_blocks(
    schedule_state: Mapping[str, Any],
    now_local: dt.datetime,
    schedule_tz: ZoneInfo,
) -> List[Tuple[dt.datetime, dt.datetime, str, Mapping[str, Any], int]]:
    parsed_blocks: List[Tuple[dt.datetime, dt.datetime, str, Mapping[str, Any], int]] = []

    expanded = schedule_state.get("expanded_blocks")
    if isinstance(expanded, list):
        for idx, entry in enumerate(expanded):
            if not isinstance(entry, Mapping):
                continue
            date_local = str(entry.get("date_local") or "").strip()
            start_time = str(entry.get("start_time_local") or "").strip()
            end_time = str(entry.get("end_time_local") or "").strip()
            if not date_local or not start_time or not end_time:
                continue

            start_dt = schedule_local_datetime(date_local, start_time, schedule_tz)
            end_dt = schedule_local_datetime(date_local, end_time, schedule_tz)
            if start_dt is None or end_dt is None:
                continue
            if end_dt <= start_dt:
                continue

            block_key = str(entry.get("block_key") or "").strip()
            if not block_key:
                block_key = (
                    f"{date_local}|{idx}|{start_time}|{end_time}|"
                    f"{str(entry.get('mode') or 'open')}"
                )
            parsed_blocks.append((start_dt, end_dt, block_key, entry, idx))

    if parsed_blocks:
        parsed_blocks.sort(key=lambda item: item[0])
        return parsed_blocks

    daily_template = schedule_state.get("daily_template")
    if isinstance(daily_template, list):
        current_date = now_local.date().isoformat()
        for idx, entry in enumerate(daily_template):
            if not isinstance(entry, Mapping):
                continue
            start_time = str(entry.get("start_time_local") or "").strip()
            end_time = str(entry.get("end_time_local") or "").strip()
            if not start_time or not end_time:
                continue

            start_dt = schedule_local_datetime(current_date, start_time, schedule_tz)
            end_dt = schedule_local_datetime(current_date, end_time, schedule_tz)
            if start_dt is None or end_dt is None:
                continue
            if end_dt <= start_dt:
                end_dt = end_dt + dt.timedelta(days=1)

            mode = str(entry.get("mode") or "open").strip().lower()
            playlist_id = str(entry.get("playlist_id") or "open").strip()
            block_key = (
                f"{current_date}|template|{idx}|{start_time}|{end_time}|{mode}|{playlist_id}"
            )
            parsed_blocks.append((start_dt, end_dt, block_key, entry, idx))

    parsed_blocks.sort(key=lambda item: item[0])
    return parsed_blocks


def prune_schedule_block_mentions(
    mentions: Mapping[str, Mapping[str, Any]],
    ts: float,
) -> Dict[str, Dict[str, Any]]:
    if not mentions:
        return {}

    cutoff_date = (
        dt.datetime.fromtimestamp(ts, tz=dt.timezone.utc).date()
        - dt.timedelta(days=SCHEDULE_MENTION_RETENTION_DAYS)
    )

    normalized: Dict[str, Dict[str, Any]] = {}
    for block_key, details in mentions.items():
        if not isinstance(block_key, str) or not block_key.strip():
            continue
        if not isinstance(details, Mapping):
            continue

        start = bool(details.get("start"))
        mid = bool(details.get("mid"))
        try:
            speak_count = max(0, int(details.get("speak_count", 0)))
        except (TypeError, ValueError):
            speak_count = 0
        try:
            mid_mention_count = max(0, int(details.get("mid_mention_count", 0)))
        except (TypeError, ValueError):
            mid_mention_count = 0
        try:
            last_mid_speak_count = max(0, int(details.get("last_mid_speak_count", 0)))
        except (TypeError, ValueError):
            last_mid_speak_count = 0
        if not (start or mid or speak_count > 0 or mid_mention_count > 0):
            continue

        updated_raw = details.get("updated_at")
        try:
            updated_at = float(updated_raw) if updated_raw is not None else ts
        except (TypeError, ValueError):
            updated_at = ts

        block_date_text = block_key.split("|", 1)[0]
        try:
            block_date = dt.date.fromisoformat(block_date_text)
        except ValueError:
            block_date = None
        if block_date is not None and block_date < cutoff_date:
            continue

        normalized[block_key] = {
            "start": start,
            "mid": mid,
            "speak_count": speak_count,
            "mid_mention_count": mid_mention_count,
            "last_mid_speak_count": last_mid_speak_count,
            "updated_at": updated_at,
        }

    if len(normalized) <= SCHEDULE_MENTION_MAX_ENTRIES:
        return normalized

    ordered = sorted(
        normalized.items(),
        key=lambda item: float(item[1].get("updated_at", 0.0)),
    )
    return dict(ordered[-SCHEDULE_MENTION_MAX_ENTRIES:])


def _coerce_nonnegative_int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _mid_mention_interval_for_block(block_key: str) -> int:
    # Deterministically alternate 2 or 3 host breaks between block mentions.
    return 2 + (sum(ord(char) for char in block_key) % 2)


def _should_request_mid_block_mention(
    *,
    block_key: str,
    progress_ratio: float,
    mention_entry: Mapping[str, Any],
) -> bool:
    speak_count = _coerce_nonnegative_int(mention_entry.get("speak_count"))
    mid_mention_count = _coerce_nonnegative_int(mention_entry.get("mid_mention_count"))
    last_mid_speak_count = _coerce_nonnegative_int(
        mention_entry.get("last_mid_speak_count")
    )
    start_mentioned = bool(mention_entry.get("start"))
    cadence_breaks = _mid_mention_interval_for_block(block_key)
    upcoming_speak_index = speak_count + 1

    log_schedule_debug(
        "schedule.mid_mention.evaluate.start",
        block_key=block_key,
        progress_ratio=progress_ratio,
        speak_count=speak_count,
        mid_mention_count=mid_mention_count,
        last_mid_speak_count=last_mid_speak_count,
        start_mentioned=start_mentioned,
        upcoming_speak_index=upcoming_speak_index,
        cadence_breaks=cadence_breaks,
        mention_entry=dict(mention_entry),
    )

    # If we missed the boundary intro and this is the first host break we see in mid-phase,
    # add a quick orientation line now.
    if speak_count == 0 and not start_mentioned:
        log_schedule_debug(
            "schedule.mid_mention.evaluate.result",
            block_key=block_key,
            decision=True,
            reason="first_mid_break_without_start_intro",
        )
        return True

    if last_mid_speak_count > 0:
        breaks_since_last_mid = upcoming_speak_index - last_mid_speak_count
    else:
        # Count the start intro (if present) as the last block-related mention.
        baseline_speak_index = 1 if start_mentioned else 0
        breaks_since_last_mid = upcoming_speak_index - baseline_speak_index

    if breaks_since_last_mid >= cadence_breaks:
        log_schedule_debug(
            "schedule.mid_mention.evaluate.result",
            block_key=block_key,
            decision=True,
            reason="cadence_interval_reached",
            breaks_since_last_mid=breaks_since_last_mid,
            cadence_breaks=cadence_breaks,
        )
        return True

    # Safety valve: make sure we don't leave the middle window without any mid mention.
    if mid_mention_count == 0 and progress_ratio >= max(
        SCHEDULE_MID_PROGRESS_RANGE[0],
        SCHEDULE_MID_PROGRESS_RANGE[1] - 0.03,
    ):
        log_schedule_debug(
            "schedule.mid_mention.evaluate.result",
            block_key=block_key,
            decision=True,
            reason="safety_valve_near_end_of_mid_window",
            progress_ratio=progress_ratio,
            mid_mention_count=mid_mention_count,
        )
        return True

    log_schedule_debug(
        "schedule.mid_mention.evaluate.result",
        block_key=block_key,
        decision=False,
        reason="cadence_not_due",
        breaks_since_last_mid=breaks_since_last_mid,
        cadence_breaks=cadence_breaks,
        progress_ratio=progress_ratio,
        mid_mention_count=mid_mention_count,
    )
    return False


def _normalize_playlist_token(value: Any) -> str:
    text = str(value or "").strip().lower()
    return re.sub(r"\s+", " ", text)


def _extract_queue_track_playlist_refs(track: Optional[QueueTrack]) -> Tuple[set[str], set[str]]:
    if track is None:
        return set(), set()

    ids: set[str] = set()
    names: set[str] = set()

    def _add_id(value: Any) -> None:
        token = _normalize_playlist_token(value)
        if token:
            ids.add(token)

    def _add_name(value: Any) -> None:
        token = _normalize_playlist_token(value)
        if token:
            names.add(token)

    def _consume_mapping(payload: Mapping[str, Any]) -> None:
        playlist_value = payload.get("playlist")
        if isinstance(playlist_value, Mapping):
            _add_id(playlist_value.get("id"))
            _add_name(playlist_value.get("name"))
            _add_name(playlist_value.get("playlist_name"))
        elif playlist_value is not None:
            _add_name(playlist_value)

        _add_id(payload.get("playlist_id"))
        _add_name(payload.get("playlist_name"))

        playlists_value = payload.get("playlists")
        if isinstance(playlists_value, Sequence) and not isinstance(
            playlists_value, (str, bytes)
        ):
            for item in playlists_value:
                if isinstance(item, Mapping):
                    _add_id(item.get("id"))
                    _add_name(item.get("name"))
                    _add_name(item.get("playlist_name"))
                elif item is not None:
                    _add_name(item)

    if isinstance(track.raw, Mapping):
        _consume_mapping(track.raw)
        song_payload = track.raw.get("song")
        if isinstance(song_payload, Mapping):
            _consume_mapping(song_payload)

    return ids, names


def _extract_block_playlist_refs(entry: Mapping[str, Any]) -> Tuple[set[str], set[str]]:
    ids: set[str] = set()
    names: set[str] = set()

    playlist_ids = entry.get("playlist_ids")
    if isinstance(playlist_ids, list):
        ids.update(
            token
            for token in (_normalize_playlist_token(item) for item in playlist_ids)
            if token
        )

    playlist_names = entry.get("playlist_names")
    if isinstance(playlist_names, list):
        names.update(
            token
            for token in (_normalize_playlist_token(item) for item in playlist_names)
            if token
        )

    legacy_playlist_id = _normalize_playlist_token(entry.get("playlist_id"))
    legacy_playlist_name = _normalize_playlist_token(entry.get("playlist_name"))
    if legacy_playlist_id:
        ids.add(legacy_playlist_id)
    if legacy_playlist_name:
        names.add(legacy_playlist_name)

    return ids, names


def _track_matches_block_playlist(
    track: Optional[QueueTrack],
    block_entry: Mapping[str, Any],
) -> Optional[bool]:
    block_mode = str(block_entry.get("mode") or "open").strip().lower()
    if block_mode == "open":
        log_schedule_debug(
            "schedule.block_playlist_match",
            result="unknown",
            reason="block_mode_open",
            block_mode=block_mode,
            block_section=str(
                block_entry.get("section_label") or block_entry.get("playlist_name") or ""
            ).strip(),
        )
        return None

    block_ids, block_names = _extract_block_playlist_refs(block_entry)
    if not block_ids and not block_names:
        log_schedule_debug(
            "schedule.block_playlist_match",
            result="unknown",
            reason="block_has_no_playlist_refs",
            block_mode=block_mode,
            block_section=str(
                block_entry.get("section_label") or block_entry.get("playlist_name") or ""
            ).strip(),
        )
        return None

    track_ids, track_names = _extract_queue_track_playlist_refs(track)
    if not track_ids and not track_names:
        log_schedule_debug(
            "schedule.block_playlist_match",
            result="unknown",
            reason="track_has_no_playlist_refs",
            block_mode=block_mode,
            block_section=str(
                block_entry.get("section_label") or block_entry.get("playlist_name") or ""
            ).strip(),
            track_artist=(track.artist if track is not None else "n/a"),
            track_title=(track.title if track is not None else "n/a"),
        )
        return None

    result = False
    if block_ids and track_ids and block_ids.intersection(track_ids):
        result = True
    if block_names and track_names and block_names.intersection(track_names):
        result = True

    log_schedule_debug(
        "schedule.block_playlist_match",
        result=result,
        reason=("matched_refs" if result else "no_ref_intersection"),
        block_mode=block_mode,
        block_section=str(
            block_entry.get("section_label") or block_entry.get("playlist_name") or ""
        ).strip(),
        block_ids=sorted(block_ids),
        block_names=sorted(block_names),
        track_ids=sorted(track_ids),
        track_names=sorted(track_names),
        track_artist=(track.artist if track is not None else "n/a"),
        track_title=(track.title if track is not None else "n/a"),
    )
    return result


def load_schedule_state_payload(
    station_dir: pathlib.Path,
) -> Optional[Mapping[str, Any]]:
    schedule_path = resolve_station_metadata_file(station_dir, SCHEDULE_STATE_FILENAME)
    if not schedule_path.exists():
        return None
    try:
        payload = json.loads(schedule_path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        LOGGER.warning(
            "[schedule] Invalid schedule state file at %s; ignoring.", schedule_path
        )
        return None
    if isinstance(payload, Mapping):
        return payload
    LOGGER.warning("[schedule] Schedule state is not a JSON object: %s", schedule_path)
    return None


def resolve_schedule_context(
    schedule_state: Optional[Mapping[str, Any]],
    ts: float,
    mention_state: Mapping[str, Mapping[str, Any]],
) -> Optional[ScheduleContext]:
    if not isinstance(schedule_state, Mapping):
        log_schedule_debug(
            "schedule.resolve.skip",
            reason="schedule_state_missing_or_invalid",
            ts=ts,
        )
        return None

    schedule_tz = _resolve_schedule_timezone(schedule_state)
    now_local = dt.datetime.fromtimestamp(ts, tz=schedule_tz)
    parsed_blocks = _parse_schedule_blocks(schedule_state, now_local, schedule_tz)
    if not parsed_blocks:
        log_schedule_debug(
            "schedule.resolve.skip",
            reason="no_parsed_blocks",
            ts=ts,
            now_local=now_local.isoformat(),
        )
        return None

    current_index = -1
    for idx, (start_dt, end_dt, _, _, _) in enumerate(parsed_blocks):
        if start_dt <= now_local < end_dt:
            current_index = idx
            break

    if current_index < 0:
        log_schedule_debug(
            "schedule.resolve.skip",
            reason="no_active_block_for_timestamp",
            ts=ts,
            now_local=now_local.isoformat(),
            parsed_block_count=len(parsed_blocks),
        )
        return None

    start_dt, end_dt, block_key, current_entry, _ = parsed_blocks[current_index]
    duration_seconds = max((end_dt - start_dt).total_seconds(), 1.0)
    progress_ratio = min(
        1.0,
        max(0.0, (now_local - start_dt).total_seconds() / duration_seconds),
    )
    if progress_ratio < 0.35:
        phase = "start"
    elif progress_ratio <= 0.75:
        phase = "middle"
    else:
        phase = "end"

    mention_entry = mention_state.get(block_key, {})
    mention_intent: Optional[str] = None
    elapsed_minutes = max(0.0, (now_local - start_dt).total_seconds() / 60.0)
    elapsed_seconds = max(0.0, (now_local - start_dt).total_seconds())
    start_window_minutes_ok = elapsed_minutes <= SCHEDULE_START_WINDOW_MINUTES
    start_window_grace_ok = (
        elapsed_seconds <= SCHEDULE_BLOCK_INTRO_BOUNDARY_GRACE_SECONDS
    )
    start_already_mentioned = bool(mention_entry.get("start"))
    mid_window_ok = (
        SCHEDULE_MID_PROGRESS_RANGE[0]
        <= progress_ratio
        <= SCHEDULE_MID_PROGRESS_RANGE[1]
    )
    mid_request = False
    if (
        start_window_minutes_ok
        and start_window_grace_ok
        and not start_already_mentioned
    ):
        mention_intent = "start"
    elif (
        mid_window_ok
        and _should_request_mid_block_mention(
            block_key=block_key,
            progress_ratio=progress_ratio,
            mention_entry=mention_entry,
        )
    ):
        mid_request = True
        mention_intent = "mid"

    if mid_window_ok and mention_intent != "mid":
        mid_request = False

    log_schedule_debug(
        "schedule.resolve.active_block",
        ts=ts,
        now_local=now_local.isoformat(),
        block_key=block_key,
        section_label=str(
            current_entry.get("section_label") or current_entry.get("playlist_name") or ""
        ).strip(),
        start_local=start_dt.isoformat(),
        end_local=end_dt.isoformat(),
        progress_ratio=progress_ratio,
        phase=phase,
        elapsed_minutes=elapsed_minutes,
        elapsed_seconds=elapsed_seconds,
        mention_entry=dict(mention_entry),
        start_window_minutes_ok=start_window_minutes_ok,
        start_window_grace_ok=start_window_grace_ok,
        start_already_mentioned=start_already_mentioned,
        mid_window_ok=mid_window_ok,
        mid_request=mid_request,
        mention_intent=mention_intent or "none",
    )

    next_section_label: Optional[str] = None
    for start_candidate, _, _, entry_candidate, _ in parsed_blocks:
        if start_candidate > now_local:
            candidate_label = str(entry_candidate.get("section_label") or "").strip()
            next_section_label = candidate_label or None
            break
    if next_section_label is None and parsed_blocks:
        candidate_label = str(parsed_blocks[0][3].get("section_label") or "").strip()
        next_section_label = candidate_label or None

    section_label = str(current_entry.get("section_label") or "").strip()
    if not section_label:
        section_label = str(current_entry.get("playlist_name") or "Bloque activo").strip()

    genres_raw = current_entry.get("genre_labels")
    genre_labels: List[str] = []
    if isinstance(genres_raw, list):
        genre_labels = [str(item).strip() for item in genres_raw if str(item).strip()]
    elif genres_raw is not None:
        genre_labels = [chunk.strip() for chunk in str(genres_raw).split(",") if chunk.strip()]
    if not genre_labels:
        genre_labels = ["mix variado"]

    context = ScheduleContext(
        block_key=block_key,
        section_label=section_label,
        genre_labels=genre_labels,
        mode=str(current_entry.get("mode") or "open").strip().lower(),
        playlist_name=str(current_entry.get("playlist_name") or "").strip() or None,
        progress_ratio=progress_ratio,
        phase=phase,
        mention_intent=mention_intent,
        next_section_label=next_section_label,
        start_local_iso=start_dt.isoformat(),
        end_local_iso=end_dt.isoformat(),
    )
    log_schedule_debug(
        "schedule.resolve.result",
        block_key=context.block_key,
        section_label=context.section_label,
        phase=context.phase,
        progress_ratio=context.progress_ratio,
        mention_intent=context.mention_intent or "none",
        next_section_label=context.next_section_label or "n/a",
        mode=context.mode,
        playlist_name=context.playlist_name or "n/a",
    )
    return context


def resolve_schedule_context_for_upcoming_break(
    schedule_state: Optional[Mapping[str, Any]],
    ts_now: float,
    ts_break: float,
    mention_state: Mapping[str, Mapping[str, Any]],
    next_track: Optional[QueueTrack],
) -> Optional[ScheduleContext]:
    log_schedule_debug(
        "schedule.upcoming_break.resolve.start",
        ts_now=ts_now,
        ts_break=ts_break,
        mention_entries=len(mention_state),
        next_track_artist=(next_track.artist if next_track is not None else "n/a"),
        next_track_title=(next_track.title if next_track is not None else "n/a"),
        schedule_state_present=isinstance(schedule_state, Mapping),
    )
    boundary_context = resolve_schedule_context(
        schedule_state=schedule_state,
        ts=ts_break,
        mention_state=mention_state,
    )
    log_schedule_debug(
        "schedule.upcoming_break.resolve.boundary_context",
        result=("context" if boundary_context is not None else "none"),
        block_key=(boundary_context.block_key if boundary_context is not None else "n/a"),
        mention_intent=(
            boundary_context.mention_intent if boundary_context is not None else "none"
        ),
        section_label=(
            boundary_context.section_label if boundary_context is not None else "n/a"
        ),
    )
    if (
        boundary_context is not None
        and boundary_context.mention_intent == "start"
    ):
        log_schedule_debug(
            "schedule.upcoming_break.resolve.return",
            reason="boundary_context_start_intent",
            block_key=boundary_context.block_key,
            section_label=boundary_context.section_label,
        )
        return boundary_context

    if not isinstance(schedule_state, Mapping):
        log_schedule_debug(
            "schedule.upcoming_break.resolve.return",
            reason="schedule_state_missing_or_invalid",
            boundary_result=("context" if boundary_context is not None else "none"),
        )
        return boundary_context

    schedule_tz = _resolve_schedule_timezone(schedule_state)
    now_local = dt.datetime.fromtimestamp(ts_now, tz=schedule_tz)
    break_local = dt.datetime.fromtimestamp(ts_break, tz=schedule_tz)
    parsed_blocks = _parse_schedule_blocks(schedule_state, now_local, schedule_tz)
    if not parsed_blocks:
        log_schedule_debug(
            "schedule.upcoming_break.resolve.return",
            reason="no_parsed_blocks",
            now_local=now_local.isoformat(),
            break_local=break_local.isoformat(),
        )
        return boundary_context

    next_block_index: Optional[int] = None
    for idx, (start_dt, _, _, _, _) in enumerate(parsed_blocks):
        if start_dt > now_local:
            next_block_index = idx
            break
    if next_block_index is None:
        log_schedule_debug(
            "schedule.upcoming_break.resolve.return",
            reason="no_future_block_found",
            now_local=now_local.isoformat(),
        )
        return boundary_context

    next_start_dt, _, next_block_key, next_entry, _ = parsed_blocks[next_block_index]
    starts_in_seconds = (next_start_dt - now_local).total_seconds()
    if starts_in_seconds < 0:
        log_schedule_debug(
            "schedule.upcoming_break.resolve.return",
            reason="next_block_start_in_past",
            starts_in_seconds=starts_in_seconds,
            next_block_key=next_block_key,
        )
        return boundary_context
    if starts_in_seconds > SCHEDULE_BLOCK_INTRO_LOOKAHEAD_MINUTES * 60:
        log_schedule_debug(
            "schedule.upcoming_break.resolve.return",
            reason="outside_intro_lookahead_window",
            starts_in_seconds=starts_in_seconds,
            lookahead_seconds=SCHEDULE_BLOCK_INTRO_LOOKAHEAD_MINUTES * 60,
            next_block_key=next_block_key,
            next_block_section=str(
                next_entry.get("section_label") or next_entry.get("playlist_name") or ""
            ).strip(),
        )
        return boundary_context

    mention_entry = mention_state.get(next_block_key, {})
    if bool(mention_entry.get("start")):
        log_schedule_debug(
            "schedule.upcoming_break.resolve.return",
            reason="next_block_start_already_mentioned",
            next_block_key=next_block_key,
            mention_entry=dict(mention_entry),
        )
        return boundary_context

    playlist_match = _track_matches_block_playlist(next_track, next_entry)
    break_after_start = break_local >= next_start_dt
    log_schedule_debug(
        "schedule.upcoming_break.resolve.recovery_eval",
        next_block_key=next_block_key,
        next_block_section=str(
            next_entry.get("section_label") or next_entry.get("playlist_name") or ""
        ).strip(),
        next_start_local=next_start_dt.isoformat(),
        now_local=now_local.isoformat(),
        break_local=break_local.isoformat(),
        starts_in_seconds=starts_in_seconds,
        break_after_start=break_after_start,
        playlist_match=playlist_match,
        mention_entry=dict(mention_entry),
    )

    should_force_recovery_start = False
    reason = ""
    if break_after_start:
        if playlist_match is False:
            LOGGER.info(
                "[schedule] Skipping block intro recovery for '%s': next track playlist does not match next block.",
                str(next_entry.get("section_label") or next_entry.get("playlist_name") or "n/d"),
            )
            log_schedule_debug(
                "schedule.upcoming_break.resolve.return",
                reason="recovery_rejected_playlist_mismatch",
                next_block_key=next_block_key,
                next_block_section=str(
                    next_entry.get("section_label") or next_entry.get("playlist_name") or ""
                ).strip(),
                playlist_match=playlist_match,
                break_after_start=break_after_start,
            )
            return boundary_context
        should_force_recovery_start = True
        reason = "break_after_block_start_recovery"
    elif playlist_match is True:
        should_force_recovery_start = True
        reason = "next_track_matches_upcoming_block"

    if not should_force_recovery_start:
        log_schedule_debug(
            "schedule.upcoming_break.resolve.return",
            reason="no_recovery_trigger",
            next_block_key=next_block_key,
            break_after_start=break_after_start,
            playlist_match=playlist_match,
            boundary_result=("context" if boundary_context is not None else "none"),
        )
        return boundary_context

    intro_context = resolve_schedule_context(
        schedule_state=schedule_state,
        ts=next_start_dt.timestamp(),
        mention_state=mention_state,
    )
    if intro_context is None:
        log_schedule_debug(
            "schedule.upcoming_break.resolve.return",
            reason="failed_resolve_intro_context_at_block_start",
            next_block_key=next_block_key,
            recovery_reason=reason,
        )
        return boundary_context

    LOGGER.info(
        "[schedule] Robust block intro trigger for '%s' (%s; starts in %ss).",
        intro_context.section_label,
        reason,
        int(starts_in_seconds),
    )
    log_schedule_debug(
        "schedule.upcoming_break.resolve.return",
        reason="recovery_intro_context",
        recovery_reason=reason,
        returned_block_key=intro_context.block_key,
        returned_section_label=intro_context.section_label,
        returned_mention_intent=intro_context.mention_intent or "none",
    )
    return intro_context


def should_force_block_intro(
    schedule_context: Optional[ScheduleContext],
    forced_archetype: Optional[Archetype],
) -> bool:
    result = (
        forced_archetype is None
        and schedule_context is not None
        and schedule_context.mention_intent == "start"
    )
    log_schedule_debug(
        "schedule.block_intro_force.should_force",
        result=result,
        forced_archetype=(forced_archetype.value if forced_archetype is not None else "none"),
        schedule_context_present=schedule_context is not None,
        mention_intent=(schedule_context.mention_intent if schedule_context else "none"),
        block_key=(schedule_context.block_key if schedule_context else "n/a"),
        section_label=(schedule_context.section_label if schedule_context else "n/a"),
    )
    return result
