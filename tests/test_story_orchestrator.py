#!/usr/bin/env python3
"""Unit tests for AI host orchestrator helpers."""

from __future__ import annotations

import datetime as dt
import sys
import time
import unittest
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from neuralcast.pipelines.host_orchestrator import (  # noqa: E402
    Archetype,
    OrchestratorState,
    ScheduleContext,
    apply_success_state_update,
    build_system_prompt,
    build_tts_instructions,
    build_news_dedup_key,
    choose_weighted_archetype,
    default_state,
    migrate_state,
    parse_news_output,
    resolve_schedule_context,
    resolve_station_personality,
    should_force_block_intro,
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
            recent_scripts=[],
            schedule_block_mentions={},
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
        self.assertIn("NeuralCast script profile", neuralcast.script_profile)
        self.assertIn("metal", neuralforge.script_profile.lower())

    def test_personality_applies_to_system_and_tts(self) -> None:
        personality = resolve_station_personality("neuralforge")
        system_prompt = build_system_prompt("NeuralForge", personality)
        tts_instructions = build_tts_instructions(personality)
        self.assertIn("Station personality profile", system_prompt)
        self.assertIn("metal", system_prompt.lower())
        self.assertIn("La voz suena natural", tts_instructions)

    def test_schedule_context_start_intent(self) -> None:
        tz = ZoneInfo("Europe/Zurich")
        now_local = dt.datetime(2026, 2, 16, 0, 5, tzinfo=tz)
        date_local = now_local.date().isoformat()
        schedule_state = {
            "timezone": "Europe/Zurich",
            "expanded_blocks": [
                {
                    "block_key": f"{date_local}|0|00:00|08:00|playlist|10",
                    "date_local": date_local,
                    "start_time_local": "00:00",
                    "end_time_local": "08:00",
                    "mode": "playlist",
                    "section_label": "Prog Dawn",
                    "genre_labels": ["prog", "metal"],
                    "playlist_id": "10",
                    "playlist_name": "Prog Metal",
                },
                {
                    "block_key": f"{date_local}|1|08:00|24:00|open|open",
                    "date_local": date_local,
                    "start_time_local": "08:00",
                    "end_time_local": "24:00",
                    "mode": "open",
                    "section_label": "Open Rotation",
                    "genre_labels": ["mixed"],
                },
            ],
        }

        context = resolve_schedule_context(
            schedule_state=schedule_state,
            ts=now_local.timestamp(),
            mention_state={},
        )
        self.assertIsNotNone(context)
        assert context is not None
        self.assertEqual(context.section_label, "Prog Dawn")
        self.assertEqual(context.mention_intent, "start")

    def test_schedule_context_mid_intent_suppressed_after_mention(self) -> None:
        tz = ZoneInfo("Europe/Zurich")
        now_local = dt.datetime(2026, 2, 16, 15, 0, tzinfo=tz)
        date_local = now_local.date().isoformat()
        block_key = f"{date_local}|0|12:00|18:00|playlist|10"
        schedule_state = {
            "timezone": "Europe/Zurich",
            "expanded_blocks": [
                {
                    "block_key": block_key,
                    "date_local": date_local,
                    "start_time_local": "12:00",
                    "end_time_local": "18:00",
                    "mode": "playlist",
                    "section_label": "Afternoon Forge",
                    "genre_labels": ["symphonic", "metal"],
                    "playlist_id": "10",
                    "playlist_name": "Symphonic Metal",
                }
            ],
        }

        fresh_context = resolve_schedule_context(
            schedule_state=schedule_state,
            ts=now_local.timestamp(),
            mention_state={},
        )
        self.assertIsNotNone(fresh_context)
        assert fresh_context is not None
        self.assertEqual(fresh_context.mention_intent, "mid")

        already_mentioned_context = resolve_schedule_context(
            schedule_state=schedule_state,
            ts=now_local.timestamp(),
            mention_state={block_key: {"mid": True, "updated_at": now_local.timestamp()}},
        )
        self.assertIsNotNone(already_mentioned_context)
        assert already_mentioned_context is not None
        self.assertIsNone(already_mentioned_context.mention_intent)

    def test_should_force_block_intro_on_start_intent(self) -> None:
        context = ScheduleContext(
            block_key="2026-02-16|0|00:00|08:00|playlist|10",
            section_label="Prog Dawn",
            genre_labels=["prog", "metal"],
            mode="playlist",
            playlist_name="Prog Metal",
            progress_ratio=0.05,
            phase="start",
            mention_intent="start",
            next_section_label="Open Rotation",
            start_local_iso="2026-02-16T00:00:00+01:00",
            end_local_iso="2026-02-16T08:00:00+01:00",
        )

        self.assertTrue(should_force_block_intro(context, None))
        self.assertFalse(should_force_block_intro(context, Archetype.BACK_SELL))

    def test_start_schedule_mention_recorded_only_for_block_intro(self) -> None:
        ts = time.time()
        state = default_state(ts, __import__("random").Random(1))
        context = ScheduleContext(
            block_key="2026-02-16|0|00:00|08:00|playlist|10",
            section_label="Prog Dawn",
            genre_labels=["prog"],
            mode="playlist",
            playlist_name="Prog Metal",
            progress_ratio=0.05,
            phase="start",
            mention_intent="start",
            next_section_label="Open Rotation",
            start_local_iso="2026-02-16T00:00:00+01:00",
            end_local_iso="2026-02-16T08:00:00+01:00",
        )

        apply_success_state_update(
            state=state,
            ts=ts,
            current_track_key="a|b",
            current_remaining=120,
            archetype_used=Archetype.BACK_SELL,
            hook="hook",
            angle=None,
            news_segment=None,
            script_text="script",
            schedule_context=context,
            rng=__import__("random").Random(2),
        )
        self.assertNotIn(context.block_key, state.schedule_block_mentions)

        apply_success_state_update(
            state=state,
            ts=ts + 1,
            current_track_key="a|b",
            current_remaining=120,
            archetype_used=Archetype.BLOCK_INTRO,
            hook="hook",
            angle=None,
            news_segment=None,
            script_text="script",
            schedule_context=context,
            rng=__import__("random").Random(3),
        )
        self.assertTrue(state.schedule_block_mentions[context.block_key]["start"])

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
