"""Gemini 3.8 TTS transcript validation and presentation helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

# Gemini documents these vocal tags for Gemini 3.8 TTS.  Keep this allowlist
# explicit: arbitrary angle-bracket text is otherwise part of the transcript.
INLINE_VOCAL_TAGS = frozenset(
    {
        "argh",
        "breath",
        "heavy breath",
        "exhales",
        "cackle",
        "cheer",
        "chuckle",
        "chuckles",
        "cough",
        "cry",
        "gasp",
        "giggle",
        "groan",
        "growl",
        "grunt",
        "grr",
        "hiss",
        "laugh",
        "laughter",
        "moan",
        "pant",
        "pff",
        "phew",
        "scream",
        "shout",
        "shriek",
        "sigh",
        "sighs",
        "sneeze",
        "snicker",
        "snort",
        "sob",
        "throat-clearing",
        "tsk",
        "whimper",
        "whispers",
        "whispering",
        "yawn",
        "short pause",
        "long pause",
    }
)

_TAG_RE = re.compile(r"<([^<>]+)>")
_BRACKET_DIRECTION_RE = re.compile(r"\[[^\]\n]{1,120}\]")
_SPACE_BEFORE_PUNCTUATION_RE = re.compile(r"\s+([,.;:!?])")


@dataclass(frozen=True)
class SpeechTranscript:
    """Exact TTS input plus a tag-free form for state and presentation."""

    tts_text: str
    plain_text: str
    tags: tuple[str, ...]


def validate_speech_transcript(
    raw: str, *, protected_texts: Sequence[str | None] = ()
) -> SpeechTranscript:
    """Validate documented inline tags and derive a listener-readable script."""
    text = str(raw or "").strip()
    if not text:
        raise ValueError("TTS transcript is empty.")
    if "<" in _TAG_RE.sub("", text) or ">" in _TAG_RE.sub("", text):
        raise ValueError("TTS transcript contains malformed angle-bracket markup.")
    protected_brackets = {
        match.group(0)
        for source in protected_texts
        if source
        for match in _BRACKET_DIRECTION_RE.finditer(source)
    }
    if any(
        match.group(0) not in protected_brackets
        for match in _BRACKET_DIRECTION_RE.finditer(text)
    ):
        raise ValueError("TTS transcript contains bracketed stage directions.")

    tags = tuple(match.group(1).strip().casefold() for match in _TAG_RE.finditer(text))
    unknown = sorted({tag for tag in tags if tag not in INLINE_VOCAL_TAGS})
    if unknown:
        raise ValueError(f"Unsupported Gemini TTS inline tag(s): {', '.join(unknown)}")

    plain = _TAG_RE.sub("", text)
    plain = re.sub(r"\s{2,}", " ", plain)
    plain = _SPACE_BEFORE_PUNCTUATION_RE.sub(r"\1", plain).strip()
    if not plain:
        raise ValueError("TTS transcript has no spoken words after removing tags.")

    word_count = len(re.findall(r"\b[\wÀ-ÿ'-]+\b", plain))
    if len(tags) > 8 or len(tags) > max(2, word_count // 6 + 1):
        raise ValueError("TTS transcript uses too many inline vocal tags.")
    return SpeechTranscript(tts_text=text, plain_text=plain, tags=tags)


__all__ = ["INLINE_VOCAL_TAGS", "SpeechTranscript", "validate_speech_transcript"]
