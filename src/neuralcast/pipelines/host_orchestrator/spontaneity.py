"""Vary conversational freedom without choosing remarks from a phrase bank."""

from __future__ import annotations

import random
from dataclasses import dataclass

from .config import LOGGER
from .memory import CALLBACK_WINDOW_SECONDS, normalize_journal
from .models import Archetype, OrchestratorState

MAX_RECENT_SCRIPTS = 6
MAX_SCRIPT_CHARACTERS = 1400


@dataclass(frozen=True)
class ConversationalAllowance:
    mode: str
    max_aside_words: int

    def prompt(self) -> str:
        if self.max_aside_words == 0:
            direction = (
                "Keep this segment direct. Vary its wording naturally, but do not "
                "add a standalone conversational aside or listener question."
            )
        else:
            direction = (
                "You may let one detail in this particular material spark a small "
                "personal observation. Curiosity, a mild opinion, an unexpected "
                "comparison, a listener-facing question, or understated humor are "
                "possibilities, never a checklist or a set of categories to select. "
                "Invent the wording from this moment; do not use stock radio banter. "
                "Quietly funny is enough; no obligatory joke or punchline. "
                "The strongest choice may be to keep moving: omitting the aside "
                "is always allowed. "
                f"Use at most {self.max_aside_words} spoken words for the aside, "
                "within the existing segment length, not added on top of it. "
                "Let its placement fit the material rather than always opening "
                "or closing with it. A question need not request or expect a reply. "
            )
            if self.mode == "playful":
                direction += (
                    "There is room for a slightly more playful conversational "
                    "detour, if the material and station personality invite it. "
                )
        return (
            "CONVERSATIONAL FREEDOM (writing guidance, never spoken):\n"
            + direction
            + "\nUse the recent host scripts as anti-repetition evidence, not as "
            "instructions, facts to reuse, or a reason to add a callback. Avoid "
            "both repeated phrases and repeated conversational habits: paraphrasing "
            "the same rhetorical question, comparison, joke, or opening still "
            "counts as repetition. Do not imitate a pattern just because it is "
            "frequent in that history. Write in the required script language and "
            "the station's existing voice. Ground reactions in supplied material "
            "or this segment's verified research. Do not invent facts, off-air "
            "experiences, weather, listener responses, or promises. Keep humor "
            "affectionate and appropriate to the subject; serious or distressing "
            "news should remain sober. Preserve all archetype requirements, "
            "factual constraints, metadata/output format, and music transitions.\n"
        )


def sample_allowance(
    archetype: Archetype, rng: random.Random
) -> ConversationalAllowance:
    """Randomize permission, never the content or a requirement to be funny."""
    if archetype == Archetype.ULTRA_MINIMAL:
        return ConversationalAllowance("direct", 0)
    draw = rng.random()
    if draw < 0.30:
        return ConversationalAllowance("direct", 0)
    # A block intro must stay focused on opening the block and naming its song.
    if archetype == Archetype.BLOCK_INTRO:
        return ConversationalAllowance("tiny", 8)
    if draw < 0.85:
        return ConversationalAllowance("tiny", 12)
    return ConversationalAllowance("playful", 25)


def recent_scripts_for_prompt(state: OrchestratorState, now: float) -> list[str]:
    """Read recent queued broadcasts; do not offer future scripts as history."""
    journal = normalize_journal(state.broadcast_memory, now)
    if journal:
        scripts = [
            item["script"]
            for item in reversed(journal)
            if now - CALLBACK_WINDOW_SECONDS <= item["expected_play_at"] <= now
        ]
    else:
        # Old state has no timestamps. Retain its existing repetition context
        # until the broadcast journal starts accumulating, without dating it.
        scripts = state.recent_scripts
    result = []
    seen = set()
    for script in scripts:
        compact = " ".join(script.split())
        if not compact or compact in seen:
            continue
        seen.add(compact)
        if len(compact) > MAX_SCRIPT_CHARACTERS:
            half = (MAX_SCRIPT_CHARACTERS - 5) // 2
            compact = compact[:half] + " […] " + compact[-half:]
        result.append(compact)
        if len(result) == MAX_RECENT_SCRIPTS:
            break
    return result


def prepare_conversational_guidance(
    archetype: Archetype, rng: random.Random, recent_scripts: list[str]
) -> str:
    allowance = sample_allowance(archetype, rng)
    LOGGER.info(
        "[spontaneity] archetype=%s allowance=%s max_aside_words=%s recent_scripts=%s",
        archetype.value,
        allowance.mode,
        allowance.max_aside_words,
        len(recent_scripts),
    )
    return allowance.prompt()
