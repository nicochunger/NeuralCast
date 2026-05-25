"""Unit tests for schedule generator dataclasses."""

from __future__ import annotations

from neuralcast.pipelines.schedule_generator.models import DailyTemplateBlock


def test_daily_template_block_to_dict_copies_lists() -> None:
    block = DailyTemplateBlock(
        start_time_local="07:00",
        end_time_local="08:00",
        start_minute=420,
        end_minute=480,
        mode="playlist",
        section_label="Morning",
        genre_labels=["prog"],
        playlist_ids=["1"],
        playlist_names=["Prog"],
        playlist_id="1",
        playlist_name="Prog",
    )

    payload = block.to_dict()
    payload["genre_labels"].append("mutated")

    assert block.genre_labels == ["prog"]
    assert payload["playlist_ids"] == ["1"]
