"""Boundary tests for the decomposed host-generation modules."""

from __future__ import annotations

from neuralcast.pipelines.host_orchestrator import (
    concert_generation,
    generation,
    news_generation,
    prompts,
    script_processing,
    structured_output,
    text_generation,
)


def test_generation_facade_reexports_owned_functions() -> None:
    assert generation.build_prompt is prompts.build_prompt
    assert generation.gemini_generate_text is text_generation.gemini_generate_text
    assert (
        generation.ensure_schedule_genre_reference
        is script_processing.ensure_schedule_genre_reference
    )
    assert generation.parse_news_output is news_generation.parse_news_output
    assert generation.parse_concert_output is concert_generation.parse_concert_output
    assert (
        generation.parse_structured_script_and_meta
        is structured_output.parse_structured_script_and_meta
    )
