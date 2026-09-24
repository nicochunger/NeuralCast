from __future__ import annotations

import pytest

from neuralcast.pipelines.host_orchestrator.speech import (
    INLINE_VOCAL_TAGS,
    validate_speech_transcript,
)


def test_documented_vocal_tag_list_includes_expressive_and_pause_tags() -> None:
    assert {"short pause", "cackle", "throat-clearing", "whimper"} <= INLINE_VOCAL_TAGS


def test_transcript_validation_preserves_tags_and_derives_plain_text() -> None:
    result = validate_speech_transcript("Hola... <short pause> qué tal <chuckle> hoy?")

    assert result.tags == ("short pause", "chuckle")
    assert result.tts_text.endswith("hoy?")
    assert result.plain_text == "Hola... qué tal hoy?"


def test_bracketed_track_title_is_not_mistaken_for_a_stage_direction() -> None:
    title = "Lavender (Nightfall Remix) [feat. Kaytranada & Snoop Dogg]"
    result = validate_speech_transcript(
        f"Coming up: {title}.", protected_texts=(title,)
    )

    assert result.plain_text == f"Coming up: {title}."
    with pytest.raises(ValueError, match="stage directions"):
        validate_speech_transcript(f"Coming up: {title}.")


def test_protected_title_does_not_permit_unrelated_stage_directions() -> None:
    with pytest.raises(ValueError, match="stage directions"):
        validate_speech_transcript(
            "[laughs] Coming up: Lavender [feat. Kaytranada].",
            protected_texts=("Lavender [feat. Kaytranada]",),
        )


@pytest.mark.parametrize("raw", ["Hola <applause>", "[laughs] Hola", "Hola <pause"])
def test_transcript_validation_rejects_unknown_or_malformed_directions(
    raw: str,
) -> None:
    with pytest.raises(ValueError):
        validate_speech_transcript(raw)
