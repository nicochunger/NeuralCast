"""Timing-only block intro planning and persisted preparation between cron runs."""

from __future__ import annotations

import datetime as dt
import math
import pathlib
from dataclasses import asdict, dataclass, replace
from typing import Any, Mapping, Sequence

from .config import SCHEDULE_BLOCK_INTRO_LOOKAHEAD_MINUTES
from .models import QueueTrack, ScheduleContext, StoryAssets
from .schedule import (
    _parse_schedule_blocks,
    _resolve_schedule_timezone,
    resolve_schedule_context,
)
from .transport import tracks_match

# AutoDJ queue estimates can move across the clock boundary as tracks crossfade.
# Only an already prepared song may retain its slot slightly before that time.
BLOCK_INTRO_EARLY_DRIFT_SECONDS = 60


@dataclass(frozen=True)
class BlockIntroPlan:
    context: ScheduleContext
    target: QueueTrack
    predecessor: QueueTrack
    upcoming_tracks: Sequence[QueueTrack]
    target_index: int
    expected_play_at: float


def _timestamp(value: Any) -> float | None:
    try:
        result = float(value)
        return result if math.isfinite(result) and result > 0 else None
    except (TypeError, ValueError):
        return None


def _current_start(current: QueueTrack, now: float, remaining: int) -> float | None:
    started_at = _timestamp(current.raw.get("played_at"))
    if started_at is None and current.duration is not None:
        started_at = now - max(0, current.duration - remaining)
    return started_at


def plan_block_intro(
    schedule_state: Mapping[str, Any] | None,
    now: float,
    current: QueueTrack,
    remaining: int,
    queue: Sequence[QueueTrack],
    mentions: Mapping[str, Mapping[str, Any]],
    *,
    prepared: PreparedBlockIntro | None = None,
) -> BlockIntroPlan | None:
    """Choose the first predicted song boundary at/after a scheduled start.

    Never inspect playlists. Missing queue timestamps mean we wait for another
    snapshot rather than inventing a multi-song timeline. A block whose first
    boundary has already passed is skipped, rather than introduced late.
    """
    if not schedule_state or not queue:
        return None
    tz = _resolve_schedule_timezone(schedule_state)
    local_now = dt.datetime.fromtimestamp(now, tz)
    started_at = _current_start(current, now, remaining)
    if started_at is None:
        return None
    for start, end, key, _, _ in _parse_schedule_blocks(schedule_state, local_now, tz):
        start_ts = start.timestamp()
        if (
            start_ts <= started_at
            or start_ts > now + SCHEDULE_BLOCK_INTRO_LOOKAHEAD_MINUTES * 60
        ):
            continue
        mention = mentions.get(key, {})
        if (
            mention.get("start")
            or mention.get("intro_missed")
            or end.timestamp() <= now
        ):
            continue
        if (
            prepared is not None
            and prepared.context.block_key == key
            and tracks_match(current, prepared.target)
        ):
            # The prepared first song has already started, even if it began a
            # little before the scheduled clock time. Do not introduce song two.
            continue
        previous_time = now
        for index, track in enumerate(queue):
            played_at = _timestamp(track.raw.get("played_at"))
            if played_at is None or played_at <= previous_time:
                return None
            previous_time = played_at
            predecessor = current if index == 0 else queue[index - 1]
            retained_boundary = (
                prepared is not None
                and prepared.context.block_key == key
                and played_at >= start_ts - BLOCK_INTRO_EARLY_DRIFT_SECONDS
                and tracks_match(track, prepared.target)
                and tracks_match(predecessor, prepared.predecessor)
            )
            if played_at < start_ts and not retained_boundary:
                continue
            if played_at >= end.timestamp():
                break
            context = resolve_schedule_context(schedule_state, start_ts, mentions)
            if context is None:
                return None
            return BlockIntroPlan(
                context=replace(context, mention_intent="start"),
                target=track,
                predecessor=predecessor,
                upcoming_tracks=queue[index:],
                target_index=index,
                expected_play_at=played_at,
            )
    return None


@dataclass(frozen=True)
class PreparedBlockIntro:
    context: ScheduleContext
    target: QueueTrack
    predecessor: QueueTrack
    assets: StoryAssets
    title: str
    hook: str
    angle: str | None

    def awaiting_boundary(
        self,
        schedule_state: Mapping[str, Any] | None,
        now: float,
        current: QueueTrack,
        remaining: int,
        mentions: Mapping[str, Mapping[str, Any]],
    ) -> bool:
        """Keep preparation through a temporarily incomplete queue snapshot."""
        start = dt.datetime.fromisoformat(self.context.start_local_iso).timestamp()
        end = dt.datetime.fromisoformat(self.context.end_local_iso).timestamp()
        started_at = _current_start(current, now, remaining)
        return (
            now < end
            and not tracks_match(current, self.target)
            and (started_at is None or started_at < start)
            and resolve_schedule_context(schedule_state, start, mentions)
            == self.context
        )

    def matches(self, plan: BlockIntroPlan) -> bool:
        return (
            self.context == plan.context
            and tracks_match(self.target, plan.target)
            and tracks_match(self.predecessor, plan.predecessor)
        )

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        # Queue payloads include large AutoDJ logs; only keep song identity.
        result["target"]["raw"] = {}
        result["predecessor"]["raw"] = {}
        result["assets"]["text_path"] = str(self.assets.text_path)
        result["assets"]["audio_path"] = str(self.assets.audio_path)
        return result

    @classmethod
    def from_dict(cls, value: dict[str, Any] | None) -> PreparedBlockIntro | None:
        if not value:
            return None
        try:
            assets = dict(value["assets"])
            assets["text_path"] = pathlib.Path(assets["text_path"])
            assets["audio_path"] = pathlib.Path(assets["audio_path"])
            return cls(
                context=ScheduleContext(**value["context"]),
                target=QueueTrack(**value["target"]),
                predecessor=QueueTrack(**value["predecessor"]),
                assets=StoryAssets(**assets),
                title=value["title"],
                hook=value["hook"],
                angle=value["angle"],
            )
        except (KeyError, TypeError, ValueError):
            return None
