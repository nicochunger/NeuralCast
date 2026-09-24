"""Tests for cleanup without schedule-phrase injection."""

from __future__ import annotations

import random
import time
from unittest.mock import patch

import pytest

from neuralcast.pipelines.host_orchestrator import generation
from neuralcast.pipelines.host_orchestrator.models import (
    Archetype,
    QueueTrack,
    ScheduleContext,
    TrackMetadata,
)
from neuralcast.pipelines.host_orchestrator.state import default_state
from neuralcast.pipelines.host_orchestrator.script_processing import (
    cleanup_generated_script,
)


def test_cleanup_generated_script_removes_links_urls_citations_and_fences() -> None:
    assert cleanup_generated_script(
        "  [source](https://example.test/a) https://example.test/b [ 12 ] ```  "
    ) == "source"


@pytest.mark.parametrize("intent", ["start", "mid"])
def test_generated_script_is_not_given_a_schedule_phrase(intent: str) -> None:
    context = ScheduleContext(
        block_key="acoustic",
        section_label="Acoustic Singer-Songwriter + Aspen Vibes",
        genre_labels=["Acoustic Singer-Songwriter", "Aspen Vibes"],
        mode="playlist",
        playlist_name="Acoustic Singer-Songwriter",
        progress_ratio=0.0,
        phase=intent,
        mention_intent=intent,
        next_section_label=None,
        start_local_iso="",
        end_local_iso="",
        official_titles={"es": "Acústico y Relax"},
    )
    script = "Sigue John Waite con Missing You."

    with patch.object(generation, "gemini_generate_text", return_value=script):
        result, _, archetype = generation.generate_archetype_script(
            archetype=Archetype.BLOCK_INTRO,
            station_name="NeuralCast",
            personality=generation.resolve_station_personality("neuralcast"),
            current_track=QueueTrack("1", None, "Crazy P", "Like a Fool", 200),
            next_track=QueueTrack("2", None, "John Waite", "Missing You", 200),
            upcoming_tracks=[],
            current_meta=TrackMetadata(),
            next_meta=TrackMetadata(),
            angle=None,
            hook="presentar el bloque",
            banned_list=[],
            schedule_context=context,
            state=default_state(time.time(), random.Random(2)),
            rng=random.Random(1),
            forced_mode=False,
        )

    assert result == script
    assert archetype == Archetype.BLOCK_INTRO


def test_local_fallback_is_not_given_a_schedule_phrase() -> None:
    context = ScheduleContext(
        block_key="acoustic",
        section_label="Acoustic Singer-Songwriter + Aspen Vibes",
        genre_labels=["Acoustic Singer-Songwriter", "Aspen Vibes"],
        mode="playlist",
        playlist_name="Acoustic Singer-Songwriter",
        progress_ratio=0.0,
        phase="start",
        mention_intent="start",
        next_section_label=None,
        start_local_iso="",
        end_local_iso="",
        official_titles={"es": "Acústico y Relax"},
    )
    script = generation.build_local_ultra_minimal_script(
        current_track=QueueTrack("1", None, "Crazy P", "Like a Fool", 200),
        next_track=QueueTrack("2", None, "John Waite", "Missing You", 200),
        schedule_context=context,
        rng=random.Random(1),
    )

    assert "Acústico y Relax" not in script
    assert "Acoustic Singer-Songwriter" not in script
