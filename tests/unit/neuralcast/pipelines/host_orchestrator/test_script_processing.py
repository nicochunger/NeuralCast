"""Tests for deterministic post-processing of generated host scripts."""

from __future__ import annotations

import random

from neuralcast.pipelines.host_orchestrator.models import Archetype, ScheduleContext
from neuralcast.pipelines.host_orchestrator.script_processing import (
    cleanup_generated_script,
    ensure_mid_block_reference,
    ensure_schedule_genre_reference,
)


def _context(*, mode: str = "playlist", intent: str | None = "mid") -> ScheduleContext:
    return ScheduleContext(
        block_key="metal", section_label="Metal Hour", genre_labels=["doom metal", "sludge"],
        mode=mode, playlist_name="Heavy Rotation", progress_ratio=0.5, phase="mid",
        mention_intent=intent, next_section_label=None, start_local_iso="", end_local_iso="",
    )


def test_cleanup_generated_script_removes_links_urls_citations_and_fences() -> None:
    assert cleanup_generated_script(
        "  [source](https://example.test/a) https://example.test/b [ 12 ] ```  "
    ) == "source"


def test_mid_block_reference_leaves_ineligible_or_already_oriented_scripts_unchanged() -> None:
    context = _context()

    assert ensure_mid_block_reference("Hello", Archetype.NEWS, context, random.Random(1)) == "Hello"
    assert (
        ensure_mid_block_reference("Welcome to the Metal Hour.", Archetype.SHORT_STORY, context, random.Random(1))
        == "Welcome to the Metal Hour."
    )
    assert ensure_mid_block_reference("Hello", Archetype.SHORT_STORY, None, random.Random(1)) == "Hello"


def test_mid_block_reference_injects_clause_and_keeps_ultra_minimal_to_one_sentence() -> None:
    context = _context()

    expanded = ensure_mid_block_reference("Here is a story.", Archetype.SHORT_STORY, context, random.Random(1))
    minimal = ensure_mid_block_reference("Here is a story.", Archetype.ULTRA_MINIMAL, context, random.Random(1))

    assert "Metal Hour" in expanded
    assert "... Here is a story." in expanded
    assert "Metal Hour" in minimal
    assert "..." not in minimal


def test_schedule_genre_reference_handles_open_blocks_empty_scripts_and_existing_genres() -> None:
    open_context = _context(mode="open", intent="start")
    playlist_context = _context(intent="start")

    open_script = ensure_schedule_genre_reference("Hello", Archetype.SHORT_STORY, open_context, random.Random(1))
    empty_script = ensure_schedule_genre_reference("", Archetype.ULTRA_MINIMAL, playlist_context, random.Random(1))
    existing = ensure_schedule_genre_reference(
        "A doom metal classic is next.", Archetype.SHORT_STORY, playlist_context, random.Random(1)
    )

    assert open_script != "Hello"
    assert empty_script
    assert existing == "A doom metal classic is next."
