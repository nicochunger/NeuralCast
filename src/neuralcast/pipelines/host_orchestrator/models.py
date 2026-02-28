"""Shared enums and dataclasses for the AI host orchestrator pipeline."""

from __future__ import annotations

import pathlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class Archetype(str, Enum):
    BACK_SELL = "back_sell"
    UP_NEXT_TEASE = "up_next_tease"
    SHORT_STORY = "short_story"
    DEEP_DIVE = "deep_dive"
    NEWS = "news"
    CONCERT_CHECK = "concert_check"
    BLOCK_INTRO = "block_intro"
    ULTRA_MINIMAL = "ultra_minimal"


@dataclass
class QueueTrack:
    queue_id: str
    song_id: Optional[str]
    artist: str
    title: str
    duration: Optional[int]
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class StoryAssets:
    text_path: pathlib.Path
    audio_path: pathlib.Path
    story_text: str
    remote_path: str


@dataclass
class TrackMetadata:
    year: Optional[str] = None
    genre: Optional[str] = None
    album: Optional[str] = None
    bpm: Optional[str] = None
    mood_tags: Optional[str] = None
    notes: Optional[str] = None


@dataclass(frozen=True)
class ScheduleContext:
    block_key: str
    section_label: str
    genre_labels: List[str]
    mode: str
    playlist_name: Optional[str]
    progress_ratio: float
    phase: str
    mention_intent: Optional[str]
    next_section_label: Optional[str]
    start_local_iso: str
    end_local_iso: str


@dataclass(frozen=True)
class StationPersonality:
    script_profile: str
    tts_profile: str


@dataclass
class NewsStoryMeta:
    topic: str
    headline: str
    source_url: str
    published_at: Optional[str] = None


@dataclass
class NewsSegment:
    script: str
    story_count: int
    stories: List[NewsStoryMeta]


@dataclass
class ConcertEventMeta:
    artist: str
    country: str
    city: str
    venue: str
    event_date: str
    source_url: str


@dataclass
class ConcertSegment:
    script: str
    events: List[ConcertEventMeta]


@dataclass
class OrchestratorState:
    state_version: int
    last_seen_track_key: Optional[str]
    last_seen_ts: Optional[float]
    songs_since_last_spoken: int
    songs_until_next_speak: int
    next_speak_deadline_ts: float
    last_spoken_track_key: Optional[str]
    last_spoken_ts: Optional[float]
    last_spoken_expected_end_ts: Optional[float]
    cooldown_until: Dict[str, float]
    recent_archetypes: List[str]
    recent_hooks: List[str]
    last_angle_by_archetype: Dict[str, str]
    recent_news_dedup: List[Dict[str, Any]]
    recent_scripts: List[str]
    schedule_block_mentions: Dict[str, Dict[str, Any]]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "state_version": self.state_version,
            "last_seen_track_key": self.last_seen_track_key,
            "last_seen_ts": self.last_seen_ts,
            "songs_since_last_spoken": self.songs_since_last_spoken,
            "songs_until_next_speak": self.songs_until_next_speak,
            "next_speak_deadline_ts": self.next_speak_deadline_ts,
            "last_spoken_track_key": self.last_spoken_track_key,
            "last_spoken_ts": self.last_spoken_ts,
            "last_spoken_expected_end_ts": self.last_spoken_expected_end_ts,
            "cooldown_until": self.cooldown_until,
            "recent_archetypes": self.recent_archetypes,
            "recent_hooks": self.recent_hooks,
            "last_angle_by_archetype": self.last_angle_by_archetype,
            "recent_news_dedup": self.recent_news_dedup,
            "recent_scripts": self.recent_scripts,
            "schedule_block_mentions": self.schedule_block_mentions,
        }
