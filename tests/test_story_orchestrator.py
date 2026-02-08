#!/usr/bin/env python3
"""Unit tests for AI host orchestrator helpers."""

from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from neuralcast.pipelines.story_injector import (  # noqa: E402
    Archetype,
    OrchestratorState,
    build_system_prompt,
    build_tts_instructions,
    build_news_dedup_key,
    choose_weighted_archetype,
    default_state,
    migrate_state,
    parse_news_output,
    resolve_station_personality,
    station_name_for_generation,
    should_speak_now,
    validate_news_freshness_and_dedup,
)


class OrchestratorHelpersTest(unittest.TestCase):
    def test_migrate_state_clamps_wait_range(self) -> None:
        ts = time.time()
        rng_seed = __import__("random").Random(123)
        raw = {
            "songs_since_last_spoken": "5",
            "songs_until_next_speak": 999,
            "next_speak_deadline_ts": ts - 60,
            "cooldown_until": {"back_sell": ts + 90},
        }

        state = migrate_state(raw, ts, rng_seed)
        self.assertEqual(state.songs_since_last_spoken, 5)
        self.assertEqual(state.songs_until_next_speak, 5)
        self.assertGreater(state.cooldown_until["back_sell"], ts)

    def test_wait_gate_song_count_and_repeat_protection(self) -> None:
        ts = time.time()
        state = OrchestratorState(
            state_version=1,
            last_seen_track_key="a|b",
            last_seen_ts=ts - 120,
            songs_since_last_spoken=3,
            songs_until_next_speak=2,
            next_speak_deadline_ts=ts + 600,
            last_spoken_track_key="x|y",
            last_spoken_ts=ts - 360,
            last_spoken_expected_end_ts=ts - 100,
            cooldown_until={"back_sell": 0, "system_check": 0, "deep_dive": 0, "news": 0},
            recent_archetypes=[],
            recent_hooks=[],
            last_angle_by_archetype={},
            recent_news_dedup=[],
        )

        ok, _ = should_speak_now(state, "new|track", ts)
        self.assertTrue(ok)

        state.last_spoken_track_key = "new|track"
        state.last_spoken_expected_end_ts = ts + 90
        ok, reason = should_speak_now(state, "new|track", ts)
        self.assertFalse(ok)
        self.assertIn("already consumed", reason)

    def test_weighted_choice_single_legal(self) -> None:
        rng = __import__("random").Random(7)
        state = default_state(time.time(), __import__("random").Random(1))
        selected = choose_weighted_archetype([Archetype.DEEP_DIVE], state, rng)
        self.assertEqual(selected, Archetype.DEEP_DIVE)

    def test_weighted_choice_avoids_immediate_repeat_when_possible(self) -> None:
        rng = __import__("random").Random(9)
        state = default_state(time.time(), __import__("random").Random(2))
        state.recent_archetypes = [Archetype.BACK_SELL.value]
        legal = [Archetype.BACK_SELL, Archetype.SYSTEM_CHECK]
        selected = choose_weighted_archetype(legal, state, rng)
        self.assertEqual(selected, Archetype.SYSTEM_CHECK)

    def test_parse_news_output_valid(self) -> None:
        output = """SCRIPT:
Acá va el bloque en español.

META (JSON):
{
  "story_count": 1,
  "language": "es-AR",
  "stories": [
    {
      "topic": "Tech/AI",
      "headline": "Nuevo avance",
      "source_url": "https://example.com/item",
      "published_at": "2030-01-01T00:00:00Z"
    }
  ]
}
"""
        segment, reason = parse_news_output(output)
        self.assertEqual(reason, "ok")
        self.assertIsNotNone(segment)
        assert segment is not None
        self.assertEqual(segment.story_count, 1)
        self.assertEqual(segment.stories[0].topic, "Tech/AI")

    def test_news_validation_duplicate_and_age(self) -> None:
        ts = time.time()
        rng = __import__("random").Random(5)
        state = default_state(ts, rng)
        dedup_key = build_news_dedup_key(
            "Tech/AI",
            "Nuevo avance",
            "https://example.com/item",
        )
        state.recent_news_dedup = [
            {
                "key": dedup_key,
                "ts": ts,
                "topic": "Tech/AI",
                "headline": "Nuevo avance",
                "source_domain": "example.com",
            }
        ]

        output = """SCRIPT:
Texto.

META (JSON):
{
  "story_count": 1,
  "language": "es-AR",
  "stories": [
    {
      "topic": "Tech/AI",
      "headline": "Nuevo avance",
      "source_url": "https://example.com/item",
      "published_at": "2030-01-01T00:00:00Z"
    }
  ]
}
"""

        segment, reason = parse_news_output(output)
        self.assertEqual(reason, "ok")
        assert segment is not None

        ok, detail = validate_news_freshness_and_dedup(segment, state, ts)
        self.assertFalse(ok)
        self.assertIn("duplicate", detail)

    def test_station_personality_profiles(self) -> None:
        neuralcast = resolve_station_personality("neuralcast")
        neuralforge = resolve_station_personality("neuralforge")
        self.assertIn("Aspen", neuralcast.script_profile)
        self.assertIn("metal", neuralforge.script_profile.lower())

    def test_personality_applies_to_system_and_tts(self) -> None:
        personality = resolve_station_personality("neuralforge")
        system_prompt = build_system_prompt(personality)
        tts_instructions = build_tts_instructions(personality)
        self.assertIn("Station personality profile", system_prompt)
        self.assertIn("metal", system_prompt.lower())
        self.assertIn("Ajuste de personalidad de estacion", tts_instructions)
        self.assertIn("metal", tts_instructions.lower())

    def test_station_name_spelling_for_generation(self) -> None:
        self.assertEqual(
            station_name_for_generation("neuralcast", "NeuralCast"),
            "NéuralCast",
        )
        self.assertEqual(
            station_name_for_generation("neuralforge", "NeuralForge"),
            "NéuralForsh",
        )
        self.assertEqual(
            station_name_for_generation("otherstation", "OtherStation"),
            "OtherStation",
        )


if __name__ == "__main__":
    unittest.main()
