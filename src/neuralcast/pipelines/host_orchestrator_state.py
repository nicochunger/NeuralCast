"""State, locking, cadence, and archetype helpers for host orchestrator."""

from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import random
import re
import shutil
import time
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import urlparse

from neuralcast.pipelines.host_orchestrator_config import (
    ANGLE_OPTIONS,
    BANNED_OPENERS,
    COOLDOWN_SECONDS,
    HOOKS_BY_ARCHETYPE,
    HOOK_FREE_OPEN_PROB_BY_ARCHETYPE,
    LOCK_STALE_SECONDS,
    LOGGER,
    NEWS_DEDUP_MAX_ENTRIES,
    NEWS_DUPLICATE_WINDOW_DAYS,
    OVERUSED_STYLE_CLICHES,
    RECENT_SCRIPT_MEMORY_SIZE,
    SPEAK_DEADLINE_MINUTES,
    STATE_VERSION,
    TEMPERATURE_TOP_P_RANGES,
    WAIT_RANGE_SONGS,
    WEIGHTED_ARCHETYPES,
)
from neuralcast.pipelines.host_orchestrator_models import (
    Archetype,
    NewsSegment,
    OrchestratorState,
    ScheduleContext,
)
from neuralcast.pipelines.host_orchestrator_schedule import prune_schedule_block_mentions
from neuralcast.pipelines.host_orchestrator_utils import iso_utc


def normalize_text_for_key(value: str) -> str:
    normalized = re.sub(r"\s+", " ", (value or "").strip().lower())
    return normalized


def source_domain(url: str) -> str:
    parsed = urlparse(url)
    domain = (parsed.netloc or "").lower()
    domain = re.sub(r"^www\.", "", domain)
    return domain


def build_news_dedup_key(topic: str, headline: str, source_url: str) -> str:
    return "|".join(
        [
            normalize_text_for_key(topic),
            normalize_text_for_key(headline),
            source_domain(source_url),
        ]
    )


class StationLock:
    """Station-scoped lockfile guard with stale lock recovery."""

    def __init__(self, path: pathlib.Path, stale_seconds: int = LOCK_STALE_SECONDS):
        self.path = path
        self.stale_seconds = stale_seconds
        self.acquired = False

    def _read_lock_timestamp(self) -> Optional[float]:
        if not self.path.exists():
            return None
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            ts_raw = payload.get("created_at")
            if ts_raw is not None:
                return float(ts_raw)
        except Exception:  # noqa: BLE001
            pass
        try:
            return self.path.stat().st_mtime
        except OSError:
            return None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        now_ts = time.time()
        if self.path.exists():
            lock_ts = self._read_lock_timestamp()
            if lock_ts is not None and now_ts - lock_ts < self.stale_seconds:
                age = int(now_ts - lock_ts)
                LOGGER.info(
                    "[lock] Active lockfile at %s (%ss old); skipping cycle.",
                    self.path,
                    age,
                )
                return False
            LOGGER.warning("[lock] Removing stale lockfile: %s", self.path)
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass

        payload = {
            "pid": os.getpid(),
            "created_at": now_ts,
            "created_at_iso": dt.datetime.fromtimestamp(
                now_ts, tz=dt.timezone.utc
            ).isoformat(),
        }
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        try:
            fd = os.open(self.path, flags)
        except FileExistsError:
            LOGGER.info(
                "[lock] Lockfile %s was created concurrently; skipping cycle.",
                self.path,
            )
            return False

        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.write("\n")
        self.acquired = True
        return True

    def release(self) -> None:
        if not self.acquired:
            return
        try:
            self.path.unlink(missing_ok=True)
        except OSError:
            LOGGER.warning("[lock] Failed to remove lockfile: %s", self.path)
        self.acquired = False


def default_state(ts: float, rng: random.Random) -> OrchestratorState:
    cooldown_until = {arch.value: 0.0 for arch in COOLDOWN_SECONDS}
    return OrchestratorState(
        state_version=STATE_VERSION,
        last_seen_track_key=None,
        last_seen_ts=None,
        songs_since_last_spoken=0,
        songs_until_next_speak=rng.randint(*WAIT_RANGE_SONGS),
        next_speak_deadline_ts=ts + SPEAK_DEADLINE_MINUTES * 60,
        last_spoken_track_key=None,
        last_spoken_ts=None,
        last_spoken_expected_end_ts=None,
        cooldown_until=cooldown_until,
        recent_archetypes=[],
        recent_hooks=[],
        last_angle_by_archetype={},
        recent_news_dedup=[],
        recent_scripts=[],
        schedule_block_mentions={},
    )


def migrate_state(
    raw: Mapping[str, Any], ts: float, rng: random.Random
) -> OrchestratorState:
    state = default_state(ts, rng)

    def _as_float(value: Any) -> Optional[float]:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _as_int(value: Any, fallback: int) -> int:
        try:
            candidate = int(value)
        except (TypeError, ValueError):
            return fallback
        return candidate

    if not isinstance(raw, Mapping):
        return state

    state.last_seen_track_key = raw.get("last_seen_track_key") or None
    state.last_seen_ts = _as_float(raw.get("last_seen_ts"))
    state.songs_since_last_spoken = max(
        0, _as_int(raw.get("songs_since_last_spoken"), state.songs_since_last_spoken)
    )
    state.songs_until_next_speak = min(
        WAIT_RANGE_SONGS[1],
        max(
            WAIT_RANGE_SONGS[0],
            _as_int(raw.get("songs_until_next_speak"), state.songs_until_next_speak),
        ),
    )

    deadline_candidate = _as_float(raw.get("next_speak_deadline_ts"))
    if deadline_candidate is not None:
        state.next_speak_deadline_ts = deadline_candidate

    state.last_spoken_track_key = raw.get("last_spoken_track_key") or None
    state.last_spoken_ts = _as_float(raw.get("last_spoken_ts"))
    state.last_spoken_expected_end_ts = _as_float(
        raw.get("last_spoken_expected_end_ts")
    )

    cooldown_raw = raw.get("cooldown_until")
    if isinstance(cooldown_raw, Mapping):
        for arch in COOLDOWN_SECONDS:
            value = cooldown_raw.get(arch.value)
            parsed = _as_float(value)
            if parsed is not None:
                state.cooldown_until[arch.value] = parsed

    recent_archetypes = raw.get("recent_archetypes")
    if isinstance(recent_archetypes, list):
        state.recent_archetypes = [str(item) for item in recent_archetypes if item][:1]

    recent_hooks = raw.get("recent_hooks")
    if isinstance(recent_hooks, list):
        state.recent_hooks = [str(item) for item in recent_hooks if item][:1]

    last_angle = raw.get("last_angle_by_archetype")
    if isinstance(last_angle, Mapping):
        normalized_angles: Dict[str, str] = {}
        for key, value in last_angle.items():
            if not key or not value:
                continue
            archetype_key = str(key)
            angle_value = str(value)
            try:
                arch = Archetype(archetype_key)
            except ValueError:
                continue
            valid_options = ANGLE_OPTIONS.get(arch, ())
            if angle_value in valid_options:
                normalized_angles[archetype_key] = angle_value
        state.last_angle_by_archetype = normalized_angles

    recent_news = raw.get("recent_news_dedup")
    if isinstance(recent_news, list):
        normalized_entries: List[Dict[str, Any]] = []
        for entry in recent_news:
            if not isinstance(entry, Mapping):
                continue
            key = str(entry.get("key") or "").strip()
            ts_val = _as_float(entry.get("ts"))
            if not key or ts_val is None:
                continue
            normalized_entries.append(
                {
                    "key": key,
                    "ts": ts_val,
                    "topic": str(entry.get("topic") or "").strip(),
                    "headline": str(entry.get("headline") or "").strip(),
                    "source_domain": str(entry.get("source_domain") or "").strip(),
                }
            )
        state.recent_news_dedup = normalized_entries[-NEWS_DEDUP_MAX_ENTRIES:]

    recent_scripts = raw.get("recent_scripts")
    if isinstance(recent_scripts, list):
        normalized_scripts: List[str] = []
        for item in recent_scripts:
            text = " ".join(str(item).split()).strip()
            if text:
                normalized_scripts.append(text)
        state.recent_scripts = normalized_scripts[:RECENT_SCRIPT_MEMORY_SIZE]

    schedule_mentions = raw.get("schedule_block_mentions")
    if isinstance(schedule_mentions, Mapping):
        normalized_mentions: Dict[str, Dict[str, Any]] = {}
        for block_key, details in schedule_mentions.items():
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
                last_mid_speak_count = max(
                    0, int(details.get("last_mid_speak_count", 0))
                )
            except (TypeError, ValueError):
                last_mid_speak_count = 0
            updated_at = _as_float(details.get("updated_at")) or ts
            if not (start or mid or speak_count > 0 or mid_mention_count > 0):
                continue
            normalized_mentions[block_key] = {
                "start": start,
                "mid": mid,
                "speak_count": speak_count,
                "mid_mention_count": mid_mention_count,
                "last_mid_speak_count": last_mid_speak_count,
                "updated_at": updated_at,
            }

        state.schedule_block_mentions = prune_schedule_block_mentions(
            normalized_mentions,
            ts,
        )

    state.state_version = STATE_VERSION
    return state


def load_state(
    state_path: pathlib.Path, ts: float, rng: random.Random
) -> OrchestratorState:
    if not state_path.exists():
        return default_state(ts, rng)

    try:
        raw = json.loads(state_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        suffix = dt.datetime.fromtimestamp(ts, tz=dt.timezone.utc).strftime(
            "%Y%m%dT%H%M%SZ"
        )
        corrupt_path = state_path.with_name(
            f"ai_host_orchestrator_state.corrupt.{suffix}.json"
        )
        shutil.move(state_path, corrupt_path)
        LOGGER.warning(
            "[state] Invalid JSON in state file; moved to %s and reinitialized.",
            corrupt_path,
        )
        return default_state(ts, rng)

    return migrate_state(raw if isinstance(raw, Mapping) else {}, ts, rng)


def save_state_atomic(state_path: pathlib.Path, state: OrchestratorState) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = state_path.with_suffix(state_path.suffix + ".tmp")
    tmp_path.write_text(
        json.dumps(state.to_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    tmp_path.replace(state_path)


def should_speak_now(
    state: OrchestratorState,
    current_track_key: str,
    ts: float,
) -> Tuple[bool, str]:
    if (
        state.last_spoken_track_key
        and current_track_key == state.last_spoken_track_key
        and state.last_spoken_expected_end_ts is not None
        and ts < state.last_spoken_expected_end_ts
    ):
        return False, "current track already consumed by previous successful segment"

    by_song_count = state.songs_since_last_spoken >= state.songs_until_next_speak
    by_deadline = ts >= state.next_speak_deadline_ts
    if by_song_count or by_deadline:
        reason = "song cadence reached" if by_song_count else "deadline exceeded"
        return True, reason
    return (
        False,
        f"wait gate not met (songs_since_last_spoken={state.songs_since_last_spoken}, songs_until_next_speak={state.songs_until_next_speak}, deadline={iso_utc(state.next_speak_deadline_ts)})",
    )


def legal_archetypes(state: OrchestratorState, ts: float) -> List[Archetype]:
    legal: List[Archetype] = []
    for archetype in (
        Archetype.BACK_SELL,
        Archetype.DEEP_DIVE,
        Archetype.NEWS,
        Archetype.CONCERT_CHECK,
    ):
        cooldown_until = float(state.cooldown_until.get(archetype.value, 0.0))
        if ts >= cooldown_until:
            legal.append(archetype)
    return legal


def choose_weighted_archetype(
    legal: Sequence[Archetype],
    state: OrchestratorState,
    rng: random.Random,
) -> Archetype:
    if not legal:
        return Archetype.ULTRA_MINIMAL
    selectable = list(legal)
    if len(selectable) > 1 and state.recent_archetypes:
        last = state.recent_archetypes[0]
        filtered = [item for item in selectable if item.value != last]
        if filtered:
            selectable = filtered
    if len(selectable) == 1:
        return selectable[0]

    weighted: List[Tuple[Archetype, float]] = []
    for archetype in selectable:
        weight = WEIGHTED_ARCHETYPES.get(archetype, 0.0)
        if weight > 0:
            weighted.append((archetype, weight))

    total = sum(weight for _, weight in weighted)
    if total <= 0:
        return rng.choice(selectable)

    threshold = rng.uniform(0, total)
    cumulative = 0.0
    for archetype, weight in weighted:
        cumulative += weight
        if threshold <= cumulative:
            return archetype
    return weighted[-1][0]


def choose_angle(
    archetype: Archetype, state: OrchestratorState, rng: random.Random
) -> Optional[str]:
    options = list(ANGLE_OPTIONS.get(archetype, ()))
    if not options:
        return None

    last = state.last_angle_by_archetype.get(archetype.value)
    if last and len(options) > 1:
        options = [candidate for candidate in options if candidate != last] or options
    return rng.choice(options)


def choose_hook(
    archetype: Archetype, state: OrchestratorState, rng: random.Random
) -> str:
    options = list(HOOKS_BY_ARCHETYPE.get(archetype, ()))
    if not options:
        return ""

    recent = state.recent_hooks[0] if state.recent_hooks else None
    if recent and len(options) > 1:
        filtered = [hook for hook in options if hook != recent]
        if filtered:
            options = filtered

    free_open_prob = HOOK_FREE_OPEN_PROB_BY_ARCHETYPE.get(archetype, 0.0)
    if free_open_prob > 0 and rng.random() < free_open_prob:
        return ""

    return rng.choice(options)


def sample_generation_settings(
    archetype: Archetype,
    rng: random.Random,
) -> Tuple[float, float]:
    temp_range, top_p_range = TEMPERATURE_TOP_P_RANGES[archetype]
    return (
        rng.uniform(*temp_range),
        rng.uniform(*top_p_range),
    )


def assemble_banned_list(state: OrchestratorState) -> List[str]:
    banned = list(BANNED_OPENERS)
    for cliche in OVERUSED_STYLE_CLICHES:
        banned.append(f"overused style cliche: {cliche}")
    if state.recent_hooks:
        banned.append(f"repeat previous hook: {state.recent_hooks[0]}")
    if state.recent_archetypes:
        banned.append(f"repeat previous archetype: {state.recent_archetypes[0]}")
    for archetype, angle in state.last_angle_by_archetype.items():
        banned.append(f"repeat previous angle for {archetype}: {angle}")

    for entry in reversed(state.recent_news_dedup[-5:]):
        headline = str(entry.get("headline") or "").strip()
        if headline:
            banned.append(f"recent headline already used: {headline}")
    return banned


def prune_news_history(
    entries: List[Dict[str, Any]], ts: float
) -> List[Dict[str, Any]]:
    min_ts = ts - NEWS_DUPLICATE_WINDOW_DAYS * 24 * 60 * 60
    filtered = [entry for entry in entries if float(entry.get("ts", 0.0)) >= min_ts]
    return filtered[-NEWS_DEDUP_MAX_ENTRIES:]


def record_news_history(
    state: OrchestratorState,
    segment: NewsSegment,
    ts: float,
) -> None:
    entries = prune_news_history(state.recent_news_dedup, ts)
    for story in segment.stories:
        entries.append(
            {
                "key": build_news_dedup_key(
                    story.topic, story.headline, story.source_url
                ),
                "ts": ts,
                "topic": story.topic,
                "headline": story.headline,
                "source_domain": source_domain(story.source_url),
            }
        )
    state.recent_news_dedup = entries[-NEWS_DEDUP_MAX_ENTRIES:]


def apply_success_state_update(
    state: OrchestratorState,
    ts: float,
    current_track_key: str,
    current_remaining: Optional[int],
    archetype_used: Archetype,
    hook: str,
    angle: Optional[str],
    news_segment: Optional[NewsSegment],
    script_text: str,
    schedule_context: Optional[ScheduleContext],
    rng: random.Random,
) -> None:
    previous_songs_since = state.songs_since_last_spoken
    previous_songs_until = state.songs_until_next_speak
    state.last_spoken_track_key = current_track_key
    state.last_spoken_ts = ts
    state.last_spoken_expected_end_ts = ts + max(0, current_remaining or 0)

    state.songs_since_last_spoken = 0
    state.songs_until_next_speak = rng.randint(*WAIT_RANGE_SONGS)
    state.next_speak_deadline_ts = ts + SPEAK_DEADLINE_MINUTES * 60
    LOGGER.info(
        "[cadence] Reset after successful segment | previous_progress=%s/%s songs | next_wait_roll=%s songs | next_deadline=%s",
        previous_songs_since,
        previous_songs_until,
        state.songs_until_next_speak,
        iso_utc(state.next_speak_deadline_ts),
    )

    if archetype_used in COOLDOWN_SECONDS:
        cooldown = COOLDOWN_SECONDS[archetype_used]
        state.cooldown_until[archetype_used.value] = ts + cooldown

    state.recent_archetypes = [archetype_used.value]
    normalized_hook = (hook or "").strip()
    state.recent_hooks = [normalized_hook] if normalized_hook else []

    if angle and archetype_used in ANGLE_OPTIONS:
        state.last_angle_by_archetype[archetype_used.value] = angle

    state.recent_news_dedup = prune_news_history(state.recent_news_dedup, ts)
    if news_segment is not None:
        record_news_history(state, news_segment, ts)

    normalized_script = " ".join(script_text.split()).strip()
    if normalized_script:
        state.recent_scripts = [normalized_script, *state.recent_scripts][
            :RECENT_SCRIPT_MEMORY_SIZE
        ]

    state.schedule_block_mentions = prune_schedule_block_mentions(
        state.schedule_block_mentions, ts
    )
    if schedule_context is not None:
        mention_entry = dict(
            state.schedule_block_mentions.get(schedule_context.block_key, {})
        )
        current_speak_count = int(mention_entry.get("speak_count", 0) or 0)
        current_speak_count = max(0, current_speak_count) + 1
        mention_entry["speak_count"] = current_speak_count

        should_record_start_mention = (
            schedule_context.mention_intent == "start"
            and archetype_used == Archetype.BLOCK_INTRO
        )
        should_record_mid_mention = (
            schedule_context.mention_intent == "mid"
            and archetype_used
            in {
                Archetype.BACK_SELL,
                Archetype.DEEP_DIVE,
                Archetype.ULTRA_MINIMAL,
            }
        )

        if should_record_start_mention:
            mention_entry["start"] = True
        if should_record_mid_mention:
            mention_entry["mid"] = True
            mid_mention_count = int(mention_entry.get("mid_mention_count", 0) or 0)
            mention_entry["mid_mention_count"] = max(0, mid_mention_count) + 1
            mention_entry["last_mid_speak_count"] = current_speak_count

        mention_entry["updated_at"] = ts
        state.schedule_block_mentions[schedule_context.block_key] = mention_entry


def update_track_seen_state(
    state: OrchestratorState, current_track_key: str, ts: float
) -> None:
    if state.last_seen_track_key != current_track_key:
        if state.last_seen_track_key is not None:
            state.songs_since_last_spoken += 1
        state.last_seen_track_key = current_track_key
        state.last_seen_ts = ts
