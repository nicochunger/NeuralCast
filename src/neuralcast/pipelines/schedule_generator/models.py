"""Data models for schedule generation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .config import STATE_VERSION


class ScheduleValidationError(ValueError):
    """Raised when generated schedule data does not satisfy hard rules."""


@dataclass
class StationPlaylist:
    id: str
    name: str
    is_enabled: bool
    weight: float
    schedule_items: List[Dict[str, Any]]
    raw: Dict[str, Any]


@dataclass
class DailyTemplateBlock:
    start_time_local: str
    end_time_local: str
    start_minute: int
    end_minute: int
    mode: str
    section_label: str
    genre_labels: List[str]
    playlist_ids: List[str]
    playlist_names: List[str]
    playlist_id: Optional[str] = None
    playlist_name: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "start_time_local": self.start_time_local,
            "end_time_local": self.end_time_local,
            "start_minute": self.start_minute,
            "end_minute": self.end_minute,
            "mode": self.mode,
            "section_label": self.section_label,
            "genre_labels": list(self.genre_labels),
            "playlist_ids": list(self.playlist_ids),
            "playlist_names": list(self.playlist_names),
            "playlist_id": self.playlist_id,
            "playlist_name": self.playlist_name,
        }


@dataclass
class ExpandedScheduleBlock:
    block_key: str
    day_of_week: int
    date_local: str
    start_time_local: str
    end_time_local: str
    mode: str
    section_label: str
    genre_labels: List[str]
    playlist_ids: List[str]
    playlist_names: List[str]
    playlist_id: Optional[str]
    playlist_name: Optional[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "block_key": self.block_key,
            "day_of_week": self.day_of_week,
            "date_local": self.date_local,
            "start_time_local": self.start_time_local,
            "end_time_local": self.end_time_local,
            "mode": self.mode,
            "section_label": self.section_label,
            "genre_labels": list(self.genre_labels),
            "playlist_ids": list(self.playlist_ids),
            "playlist_names": list(self.playlist_names),
            "playlist_id": self.playlist_id,
            "playlist_name": self.playlist_name,
        }


@dataclass
class WeeklySchedulePlan:
    station: str
    station_name: str
    timezone: str
    week_start_local_date: str
    week_end_local_date: str
    generated_at_utc: str
    seed_mode: str
    seed_salt: Optional[str]
    resolved_seed: int
    open_ratio_min: float
    open_ratio_max: float
    daily_template: List[DailyTemplateBlock]
    expanded_blocks: List[ExpandedScheduleBlock]
    rationale: str
    plan_hash: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "state_version": STATE_VERSION,
            "station": self.station,
            "station_name": self.station_name,
            "timezone": self.timezone,
            "week_start_local_date": self.week_start_local_date,
            "week_end_local_date": self.week_end_local_date,
            "generated_at_utc": self.generated_at_utc,
            "seed_mode": self.seed_mode,
            "seed_salt": self.seed_salt,
            "resolved_seed": self.resolved_seed,
            "open_ratio_min": self.open_ratio_min,
            "open_ratio_max": self.open_ratio_max,
            "daily_template": [block.to_dict() for block in self.daily_template],
            "expanded_blocks": [block.to_dict() for block in self.expanded_blocks],
            "rationale": self.rationale,
            "plan_hash": self.plan_hash,
        }
