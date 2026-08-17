#!/usr/bin/env python3
"""Unit tests for AI host orchestrator helpers."""

from __future__ import annotations

import argparse
import datetime as dt
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from mutagen.id3 import APIC, ID3

from neuralcast.pipelines.host_orchestrator import assets as story_assets  # noqa: E402
from neuralcast.pipelines.host_orchestrator.channels import (  # noqa: E402
    get_channel_registry,
)
from neuralcast.pipelines.host_orchestrator.generation import (  # noqa: E402
    build_prompt,
    build_system_prompt,
    build_tts_instructions,
    ensure_schedule_genre_reference,
    generate_archetype_script,
    parse_news_output,
    resolve_station_personality,
    select_album_spotlight_focus,
    should_enable_search,
    station_name_for_generation,
    validate_news_freshness_and_dedup,
)
from neuralcast.pipelines.host_orchestrator.models import (  # noqa: E402
    Archetype,
    OrchestratorState,
    QueueTrack,
    ScheduleContext,
    TrackFocus,
    TrackMetadata,
)
from neuralcast.pipelines.host_orchestrator.main import (  # noqa: E402
    PlaybackContext,
    QueueContext,
    _select_archetype,
    ArgumentValidationError,
    validate_runtime_args,
)
from neuralcast.pipelines.host_orchestrator.schedule import (  # noqa: E402
    resolve_schedule_context,
    seconds_until_schedule_block_change,
    should_force_block_intro,
)
from neuralcast.pipelines.host_orchestrator.state import (  # noqa: E402
    apply_success_state_update,
    build_news_dedup_key,
    choose_weighted_archetype,
    default_state,
    legal_archetypes_for_remaining,
    migrate_state,
    should_speak_now,
)


class StoryAssetTest(unittest.TestCase):
    @staticmethod
    def _queue_track() -> QueueTrack:
        return QueueTrack(
            queue_id="test-track",
            song_id=None,
            artist="Test Artist",
            title="Test Title",
            duration=240,
        )

    def test_story_audio_tags_preserve_embedded_cover_art(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            audio_path = Path(tmpdir) / "story.mp3"
            tags = ID3()
            tags.add(
                APIC(
                    encoding=3,
                    mime="image/jpeg",
                    type=3,
                    desc="Cover",
                    data=b"cover bytes",
                )
            )
            tags.save(audio_path, v2_version=3)

            story_assets.tag_story_audio(audio_path, "Historia del tema")

            saved_tags = ID3(audio_path)
            self.assertEqual(saved_tags.getall("TPE1")[0].text, ["NueralHost"])
            self.assertEqual(saved_tags.getall("TIT2")[0].text, ["Historia del tema"])
            self.assertEqual(saved_tags.getall("APIC")[0].data, b"cover bytes")

    def test_neuralforge_story_assets_embed_station_cover_art(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir) / "snippets"

            def _fake_speech(**kwargs: object) -> None:
                Path(str(kwargs["outfile"])).write_bytes(b"fake mp3")

            with patch.object(story_assets, "STORY_OUTPUT_DIR", output_dir), patch.object(
                story_assets, "synthesize_speech", side_effect=_fake_speech
            ), patch.object(story_assets, "apply_replaygain"), patch.object(
                story_assets, "embed_local_cover_art", return_value=True
            ) as embed_cover:
                result = story_assets.ensure_story_assets(
                    "neuralforge",
                    self._queue_track(),
                    Archetype.SHORT_STORY,
                    "Script text",
                    "TTS instructions",
                    "Historia del tema: Recién sonó - Test Artist - Test Title",
                )

            embed_cover.assert_called_once_with(
                result.audio_path,
                story_assets.AI_SNIPPET_COVER_PATH_BY_STATION["neuralforge"],
            )
            tags = ID3(result.audio_path)
            self.assertEqual(tags.getall("TPE1")[0].text, ["NueralHost"])
            self.assertEqual(
                tags.getall("TIT2")[0].text,
                ["Historia del tema: Recién sonó - Test Artist - Test Title"],
            )

    def test_neuralcast_story_assets_embed_station_cover_art(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir) / "snippets"

            def _fake_speech(**kwargs: object) -> None:
                Path(str(kwargs["outfile"])).write_bytes(b"fake mp3")

            with patch.object(story_assets, "STORY_OUTPUT_DIR", output_dir), patch.object(
                story_assets, "synthesize_speech", side_effect=_fake_speech
            ), patch.object(story_assets, "apply_replaygain"), patch.object(
                story_assets, "embed_local_cover_art", return_value=True
            ) as embed_cover:
                result = story_assets.ensure_story_assets(
                    "neuralcast",
                    self._queue_track(),
                    Archetype.SHORT_STORY,
                    "Script text",
                    "TTS instructions",
                    "Historia del tema: Ahora viene - Next Artist - Next Title",
                )

            embed_cover.assert_called_once_with(
                result.audio_path,
                story_assets.AI_SNIPPET_COVER_PATH_BY_STATION["neuralcast"],
            )

    def test_channel_story_assets_use_isolated_paths_and_locale_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir) / "snippets"
            speech_calls: list[dict[str, object]] = []

            def _fake_speech(**kwargs: object) -> None:
                speech_calls.append(kwargs)
                Path(str(kwargs["outfile"])).write_bytes(b"fake mp3")

            with patch.object(story_assets, "STORY_OUTPUT_DIR", output_dir), patch.object(
                story_assets, "synthesize_speech", side_effect=_fake_speech
            ), patch.object(story_assets, "apply_replaygain"), patch.object(
                story_assets, "embed_local_cover_art", return_value=True
            ):
                result = story_assets.ensure_story_assets(
                    "neuralcast",
                    self._queue_track(),
                    Archetype.BACK_SELL,
                    "English script",
                    "English TTS instructions",
                    "Music bridge",
                    channel_key="neuralcast-en",
                    cover_station="neuralcast",
                    remote_prefix="AI Stories/neuralcast/en",
                    tts_voice="Aoede",
                    language="en",
                )

            self.assertEqual(result.audio_path.parts[-3], "neuralcast-en")
            self.assertTrue(result.remote_path.startswith("AI Stories/neuralcast/en/"))
            self.assertEqual(speech_calls[0]["gemini_voice"], "Aoede")
            self.assertEqual(ID3(result.audio_path).getall("TLAN")[0].text, ["en"])

    def test_unmapped_station_story_assets_skip_station_cover_art(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir) / "snippets"

            def _fake_speech(**kwargs: object) -> None:
                Path(str(kwargs["outfile"])).write_bytes(b"fake mp3")

            with patch.object(story_assets, "STORY_OUTPUT_DIR", output_dir), patch.object(
                story_assets, "synthesize_speech", side_effect=_fake_speech
            ), patch.object(story_assets, "apply_replaygain"), patch.object(
                story_assets, "embed_local_cover_art", return_value=True
            ) as embed_cover:
                story_assets.ensure_story_assets(
                    "unknown",
                    self._queue_track(),
                    Archetype.SHORT_STORY,
                    "Script text",
                    "TTS instructions",
                    "Historia del tema: Recién sonó - Test Artist - Test Title",
                )

            embed_cover.assert_not_called()


class OrchestratorHelpersTest(unittest.TestCase):
    @staticmethod
    def _queue_track(artist: str, title: str) -> QueueTrack:
        return QueueTrack(
            queue_id=f"{artist}-{title}",
            song_id=None,
            artist=artist,
            title=title,
            duration=240,
        )

    @staticmethod
    def _track_meta(
        *,
        album: str = "",
        year: str = "1998",
        genre: str = "metal",
    ) -> TrackMetadata:
        return TrackMetadata(album=album or None, year=year, genre=genre)

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

    def test_migrate_state_legacy_and_current_deep_dive_keys(self) -> None:
        ts = time.time()
        rng_seed = __import__("random").Random(123)

        legacy_raw = {
            "state_version": 1,
            "cooldown_until": {"deep_dive": ts + 120},
            "recent_archetypes": ["deep_dive"],
        }
        legacy_state = migrate_state(legacy_raw, ts, rng_seed)
        self.assertGreater(legacy_state.cooldown_until["short_story"], ts)
        self.assertEqual(legacy_state.recent_archetypes, ["short_story"])

        current_raw = {
            "state_version": 2,
            "cooldown_until": {"deep_dive": ts + 240},
            "recent_archetypes": ["deep_dive"],
        }
        current_state = migrate_state(current_raw, ts, rng_seed)
        self.assertGreater(current_state.cooldown_until["deep_dive"], ts)
        self.assertEqual(current_state.recent_archetypes, ["deep_dive"])

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
            cooldown_until={"back_sell": 0, "system_check": 0, "short_story": 0, "news": 0},
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
        selected = choose_weighted_archetype([Archetype.SHORT_STORY], state, rng)
        self.assertEqual(selected, Archetype.SHORT_STORY)

    def test_weighted_choice_avoids_immediate_repeat_when_possible(self) -> None:
        rng = __import__("random").Random(9)
        state = default_state(time.time(), __import__("random").Random(2))
        state.recent_archetypes = [Archetype.BACK_SELL.value]
        legal = [Archetype.BACK_SELL, Archetype.SHORT_STORY]
        selected = choose_weighted_archetype(legal, state, rng)
        self.assertEqual(selected, Archetype.SHORT_STORY)

    def test_legal_archetypes_for_remaining_excludes_deep_dive_below_120_seconds(self) -> None:
        state = default_state(time.time(), __import__("random").Random(3))

        legal = legal_archetypes_for_remaining(
            state,
            time.time(),
            current_remaining=100,
        )

        self.assertIn(Archetype.BACK_SELL, legal)
        self.assertIn(Archetype.NEWS, legal)
        self.assertNotIn(Archetype.DEEP_DIVE, legal)

    def test_legal_archetypes_for_remaining_includes_deep_dive_at_120_seconds(self) -> None:
        state = default_state(time.time(), __import__("random").Random(4))

        legal = legal_archetypes_for_remaining(
            state,
            time.time(),
            current_remaining=120,
        )

        self.assertIn(Archetype.DEEP_DIVE, legal)

    def test_legal_archetypes_for_remaining_includes_album_spotlight_at_90_seconds(self) -> None:
        state = default_state(time.time(), __import__("random").Random(14))

        legal = legal_archetypes_for_remaining(
            state,
            time.time(),
            current_remaining=90,
        )

        self.assertIn(Archetype.ALBUM_SPOTLIGHT, legal)

    def test_legal_archetypes_for_remaining_excludes_era_snapshot_below_120_seconds(self) -> None:
        state = default_state(time.time(), __import__("random").Random(15))

        legal = legal_archetypes_for_remaining(
            state,
            time.time(),
            current_remaining=119,
        )

        self.assertNotIn(Archetype.ERA_SNAPSHOT, legal)

    def test_legal_archetypes_for_remaining_includes_era_snapshot_at_120_seconds(self) -> None:
        state = default_state(time.time(), __import__("random").Random(16))

        legal = legal_archetypes_for_remaining(
            state,
            time.time(),
            current_remaining=120,
        )

        self.assertIn(Archetype.ERA_SNAPSHOT, legal)

    def test_legal_archetypes_for_remaining_respects_disabled_archetypes(self) -> None:
        state = default_state(time.time(), __import__("random").Random(17))

        legal = legal_archetypes_for_remaining(
            state,
            time.time(),
            current_remaining=240,
            disabled_archetypes=[
                Archetype.DEEP_DIVE,
                Archetype.ERA_SNAPSHOT,
                Archetype.CONCERT_CHECK,
            ],
        )

        self.assertIn(Archetype.BACK_SELL, legal)
        self.assertIn(Archetype.NEWS, legal)
        self.assertNotIn(Archetype.DEEP_DIVE, legal)
        self.assertNotIn(Archetype.ERA_SNAPSHOT, legal)
        self.assertNotIn(Archetype.CONCERT_CHECK, legal)

    def test_legal_archetypes_for_remaining_excludes_up_next_tease_within_20_minutes_of_block_change(self) -> None:
        state = default_state(time.time(), __import__("random").Random(5))

        legal = legal_archetypes_for_remaining(
            state,
            time.time(),
            current_remaining=120,
            seconds_until_block_change=(20 * 60) - 1,
        )

        self.assertNotIn(Archetype.UP_NEXT_TEASE, legal)
        self.assertIn(Archetype.BACK_SELL, legal)

    def test_legal_archetypes_for_remaining_keeps_up_next_tease_at_20_minutes_to_block_change(self) -> None:
        state = default_state(time.time(), __import__("random").Random(6))

        legal = legal_archetypes_for_remaining(
            state,
            time.time(),
            current_remaining=120,
            seconds_until_block_change=20 * 60,
        )

        self.assertIn(Archetype.UP_NEXT_TEASE, legal)

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

    def test_parse_news_output_accepts_configured_english_locale(self) -> None:
        output = """SCRIPT:
Here is the latest update.

META (JSON):
{"story_count": 1, "language": "en", "stories": [{"topic": "Science", "headline": "New result", "source_url": "https://example.com/item"}]}
"""

        segment, reason = parse_news_output(output, expected_locale="en")

        self.assertEqual(reason, "ok")
        self.assertIsNotNone(segment)

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
        self.assertIn("Perfil de personalidad de la estacion", system_prompt)
        self.assertIn("metal", system_prompt.lower())
        self.assertIn("La voz suena natural", tts_instructions)

    def test_english_locale_controls_system_prompt_and_tts(self) -> None:
        locale = get_channel_registry().locales["en"]
        personality = resolve_station_personality("neuralcast")

        system_prompt = build_system_prompt(
            "NéuralCast", personality, locale=locale
        )
        tts_instructions = build_tts_instructions(personality, locale=locale)

        self.assertIn("exclusively in natural conversational English", system_prompt)
        self.assertIn("neutral international accent", tts_instructions)

    def test_should_enable_search_for_new_archetypes(self) -> None:
        self.assertTrue(should_enable_search(Archetype.ALBUM_SPOTLIGHT, None))
        self.assertTrue(should_enable_search(Archetype.ERA_SNAPSHOT, None))

    def test_build_prompt_routes_album_spotlight_wrapper(self) -> None:
        personality = resolve_station_personality("neuralforge")
        prompt = build_prompt(
            archetype=Archetype.ALBUM_SPOTLIGHT,
            station_name="NeuralForge",
            personality=personality,
            current=self._queue_track("Emperor", "I Am the Black Wizards"),
            next_track=self._queue_track("Bathory", "A Fine Day to Die"),
            upcoming_tracks=[],
            current_meta=self._track_meta(album="In the Nightside Eclipse"),
            next_meta=self._track_meta(album="Blood Fire Death"),
            angle=None,
            hook="el disco alrededor de este tema",
            banned_list=[],
            recent_scripts=[],
            schedule_context=None,
            album_spotlight_focus="current",
        )
        self.assertIn("Estas generando un album spotlight.", prompt)
        self.assertIn("Album-spotlight focus mode", prompt)

    def test_build_prompt_routes_era_snapshot_wrapper(self) -> None:
        personality = resolve_station_personality("neuralforge")
        prompt = build_prompt(
            archetype=Archetype.ERA_SNAPSHOT,
            station_name="NeuralForge",
            personality=personality,
            current=self._queue_track("Entombed", "Left Hand Path"),
            next_track=self._queue_track("At the Gates", "Blinded by Fear"),
            upcoming_tracks=[],
            current_meta=self._track_meta(album="Left Hand Path"),
            next_meta=self._track_meta(album="Slaughter of the Soul"),
            angle=None,
            hook="postal de epoca alrededor del tema",
            banned_list=[],
            recent_scripts=[],
            schedule_context=None,
            era_snapshot_lane="mutacion del genero",
            era_snapshot_focus="next",
        )
        self.assertIn("Estas generando un era snapshot.", prompt)
        self.assertIn("Era-snapshot lane", prompt)

    def test_select_album_spotlight_focus_prefers_track_with_album_metadata(self) -> None:
        rng = __import__("random").Random(17)
        focus = select_album_spotlight_focus(
            current_meta=self._track_meta(album=""),
            next_meta=self._track_meta(album="The Mantle"),
            rng=rng,
        )
        self.assertEqual(focus, "next")

    def test_generate_album_spotlight_falls_back_on_no_script(self) -> None:
        rng = __import__("random").Random(18)
        state = default_state(time.time(), __import__("random").Random(19))
        personality = resolve_station_personality("neuralforge")

        with patch(
            "neuralcast.pipelines.host_orchestrator.generation.gemini_generate_text",
            side_effect=["NO_SCRIPT", "Puente minimo hacia el proximo tema."],
        ):
            script, _, archetype_used = generate_archetype_script(
                archetype=Archetype.ALBUM_SPOTLIGHT,
                station_name="NeuralForge",
                personality=personality,
                current_track=self._queue_track("Opeth", "The Moor"),
                next_track=self._queue_track("Agalloch", "In the Shadow of Our Pale Companion"),
                upcoming_tracks=[],
                current_meta=self._track_meta(album="Still Life"),
                next_meta=self._track_meta(album="The Mantle"),
                angle=None,
                hook="el disco alrededor de este tema",
                banned_list=[],
                schedule_context=None,
                state=state,
                rng=rng,
                forced_mode=False,
            )

        self.assertEqual(archetype_used, Archetype.ULTRA_MINIMAL)
        self.assertIn("Puente minimo", script)

    def test_generate_album_spotlight_uses_forced_track_focus(self) -> None:
        rng = __import__("random").Random(24)
        state = default_state(time.time(), __import__("random").Random(25))
        personality = resolve_station_personality("neuralforge")

        with patch(
            "neuralcast.pipelines.host_orchestrator.generation.gemini_generate_text",
            return_value="Mirada al disco del tema actual.",
        ) as mock_generate:
            script, _, archetype_used = generate_archetype_script(
                archetype=Archetype.ALBUM_SPOTLIGHT,
                station_name="NeuralForge",
                personality=personality,
                current_track=self._queue_track("Opeth", "The Moor"),
                next_track=self._queue_track(
                    "Agalloch", "In the Shadow of Our Pale Companion"
                ),
                upcoming_tracks=[],
                current_meta=self._track_meta(album="Still Life"),
                next_meta=self._track_meta(album="The Mantle"),
                angle=None,
                hook="el disco alrededor de este tema",
                banned_list=[],
                schedule_context=None,
                state=state,
                rng=rng,
                forced_mode=False,
                forced_track_focus=TrackFocus.CURRENT,
            )

        self.assertEqual(archetype_used, Archetype.ALBUM_SPOTLIGHT)
        self.assertIn("Mirada al disco", script)
        prompt = mock_generate.call_args.kwargs["prompt"]
        self.assertIn("Album-spotlight focus mode", prompt)
        self.assertIn("actual (tema que acaba de sonar)", prompt)

    def test_generate_era_snapshot_falls_back_on_no_script(self) -> None:
        rng = __import__("random").Random(20)
        state = default_state(time.time(), __import__("random").Random(21))
        personality = resolve_station_personality("neuralforge")

        with patch(
            "neuralcast.pipelines.host_orchestrator.generation.gemini_generate_text",
            side_effect=["NO_SCRIPT", "Seguimos y ahora entra el proximo tema."],
        ):
            script, _, archetype_used = generate_archetype_script(
                archetype=Archetype.ERA_SNAPSHOT,
                station_name="NeuralForge",
                personality=personality,
                current_track=self._queue_track("Celtic Frost", "Procreation (Of the Wicked)"),
                next_track=self._queue_track("Mayhem", "Freezing Moon"),
                upcoming_tracks=[],
                current_meta=self._track_meta(album="To Mega Therion"),
                next_meta=self._track_meta(album="De Mysteriis Dom Sathanas"),
                angle=None,
                hook="postal de epoca alrededor del tema",
                banned_list=[],
                schedule_context=None,
                state=state,
                rng=rng,
                forced_mode=False,
            )

        self.assertEqual(archetype_used, Archetype.ULTRA_MINIMAL)
        self.assertIn("proximo tema", script.lower())

    def test_generate_ultra_minimal_uses_local_fallback_when_gemini_returns_no_script(
        self,
    ) -> None:
        rng = __import__("random").Random(26)
        state = default_state(time.time(), __import__("random").Random(27))
        personality = resolve_station_personality("neuralforge")

        with patch(
            "neuralcast.pipelines.host_orchestrator.generation.gemini_generate_text",
            return_value="NO_SCRIPT",
        ) as mock_generate:
            script, _, archetype_used = generate_archetype_script(
                archetype=Archetype.ULTRA_MINIMAL,
                station_name="NeuralForge",
                personality=personality,
                current_track=self._queue_track("Opeth", "The Drapery Falls"),
                next_track=self._queue_track("Agalloch", "Not Unlike the Waves"),
                upcoming_tracks=[],
                current_meta=self._track_meta(album="Blackwater Park"),
                next_meta=self._track_meta(album="The Mantle"),
                angle=None,
                hook="puente minimo",
                banned_list=[],
                schedule_context=None,
                state=state,
                rng=rng,
                forced_mode=False,
            )

        mock_generate.assert_called_once()
        self.assertEqual(archetype_used, Archetype.ULTRA_MINIMAL)
        self.assertNotEqual(script.strip(), "NO_SCRIPT")
        self.assertIn("Agalloch", script)

    def test_generate_short_story_uses_forced_track_focus(self) -> None:
        rng = __import__("random").Random(22)
        state = default_state(time.time(), __import__("random").Random(23))
        personality = resolve_station_personality("neuralforge")

        with patch(
            "neuralcast.pipelines.host_orchestrator.generation.gemini_generate_text",
            return_value="Historia breve del proximo tema.",
        ) as mock_generate:
            script, segment_metadata, archetype_used = generate_archetype_script(
                archetype=Archetype.SHORT_STORY,
                station_name="NeuralForge",
                personality=personality,
                current_track=self._queue_track("Amorphis", "Black Winter Day"),
                next_track=self._queue_track("Sentenced", "Noose"),
                upcoming_tracks=[],
                current_meta=self._track_meta(album="Tales from the Thousand Lakes"),
                next_meta=self._track_meta(album="Down"),
                angle=None,
                hook="historia breve alrededor del tema",
                banned_list=[],
                schedule_context=None,
                state=state,
                rng=rng,
                forced_mode=False,
                forced_track_focus=TrackFocus.NEXT,
            )

        self.assertEqual(archetype_used, Archetype.SHORT_STORY)
        self.assertEqual(segment_metadata.track_focus, TrackFocus.NEXT)
        self.assertIn("Historia breve", script)
        prompt = mock_generate.call_args.kwargs["prompt"]
        self.assertIn("Short-story focus mode", prompt)
        self.assertIn("proximo (tema que va a sonar ahora)", prompt)

    def test_validate_runtime_args_requires_force_archetype_for_track_focus(self) -> None:
        with self.assertRaises(ArgumentValidationError):
            validate_runtime_args(
                argparse.Namespace(
                    force_archetype=None,
                    force_track_focus=TrackFocus.CURRENT.value,
                )
            )

    def test_validate_runtime_args_rejects_non_story_force_focus(self) -> None:
        with self.assertRaises(ArgumentValidationError):
            validate_runtime_args(
                argparse.Namespace(
                    force_archetype=Archetype.BACK_SELL.value,
                    force_track_focus=TrackFocus.CURRENT.value,
                )
            )

    def test_validate_runtime_args_accepts_story_force_focus(self) -> None:
        focus = validate_runtime_args(
            argparse.Namespace(
                force_archetype=Archetype.DEEP_DIVE.value,
                force_track_focus=TrackFocus.NEXT.value,
            )
        )

        self.assertEqual(focus, TrackFocus.NEXT)

    def test_validate_runtime_args_accepts_album_spotlight_focus(self) -> None:
        focus = validate_runtime_args(
            argparse.Namespace(
                force_archetype=Archetype.ALBUM_SPOTLIGHT.value,
                force_track_focus=TrackFocus.CURRENT.value,
            )
        )

        self.assertEqual(focus, TrackFocus.CURRENT)

    def test_select_archetype_skips_forced_mode_without_required_lead_time(self) -> None:
        rng = __import__("random").Random(31)
        state = default_state(time.time(), __import__("random").Random(32))
        playback = PlaybackContext(
            current_track=self._queue_track("Opeth", "The Leper Affinity"),
            current_remaining=5,
            current_key="opeth|the leper affinity",
            listener_count=10,
        )
        queue_context = QueueContext(
            upcoming_tracks=[self._queue_track("Agalloch", "Falling Snow")],
            next_track=self._queue_track("Agalloch", "Falling Snow"),
            schedule_context=None,
            schedule_reference_ts=time.time() + 5,
        )

        selected = _select_archetype(
            args=argparse.Namespace(force_archetype=Archetype.DEEP_DIVE.value),
            state=state,
            playback=playback,
            queue_context=queue_context,
            forced_archetype=Archetype.DEEP_DIVE,
            auto_forced_block_intro=False,
            forced_track_focus=None,
            rng=rng,
        )

        self.assertIsNone(selected)

    def test_select_archetype_returns_forced_archetype_when_lead_time_is_sufficient(
        self,
    ) -> None:
        rng = __import__("random").Random(33)
        state = default_state(time.time(), __import__("random").Random(34))
        playback = PlaybackContext(
            current_track=self._queue_track("Opeth", "The Leper Affinity"),
            current_remaining=600,
            current_key="opeth|the leper affinity",
            listener_count=10,
        )
        queue_context = QueueContext(
            upcoming_tracks=[self._queue_track("Agalloch", "Falling Snow")],
            next_track=self._queue_track("Agalloch", "Falling Snow"),
            schedule_context=None,
            schedule_reference_ts=time.time() + 600,
        )

        selected = _select_archetype(
            args=argparse.Namespace(force_archetype=Archetype.BACK_SELL.value),
            state=state,
            playback=playback,
            queue_context=queue_context,
            forced_archetype=Archetype.BACK_SELL,
            auto_forced_block_intro=False,
            forced_track_focus=TrackFocus.NEXT,
            rng=rng,
        )

        self.assertEqual(selected, Archetype.BACK_SELL)

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
            mention_state={
                block_key: {
                    "mid": True,
                    "speak_count": 1,
                    "mid_mention_count": 1,
                    "last_mid_speak_count": 1,
                    "updated_at": now_local.timestamp(),
                }
            },
        )
        self.assertIsNotNone(already_mentioned_context)
        assert already_mentioned_context is not None
        self.assertIsNone(already_mentioned_context.mention_intent)

    def test_schedule_context_start_intent_late_fallback_within_window(self) -> None:
        tz = ZoneInfo("Europe/Zurich")
        start_local = dt.datetime(2026, 2, 16, 20, 0, tzinfo=tz)
        now_local = start_local + dt.timedelta(minutes=4, seconds=17)
        date_local = now_local.date().isoformat()
        block_key = f"{date_local}|0|20:00|22:00|playlist|10"
        schedule_state = {
            "timezone": "Europe/Zurich",
            "expanded_blocks": [
                {
                    "block_key": block_key,
                    "date_local": date_local,
                    "start_time_local": "20:00",
                    "end_time_local": "22:00",
                    "mode": "playlist",
                    "section_label": "Metal sinfonico",
                    "genre_labels": ["symphonic", "metal"],
                    "playlist_id": "10",
                    "playlist_name": "Symphonic Metal",
                }
            ],
        }

        context = resolve_schedule_context(
            schedule_state=schedule_state,
            ts=now_local.timestamp(),
            mention_state={},
        )
        self.assertIsNotNone(context)
        assert context is not None
        self.assertEqual(context.phase, "start")
        self.assertEqual(context.mention_intent, "start")

    def test_schedule_context_start_intent_late_fallback_not_repeated(self) -> None:
        tz = ZoneInfo("Europe/Zurich")
        start_local = dt.datetime(2026, 2, 16, 20, 0, tzinfo=tz)
        now_local = start_local + dt.timedelta(minutes=5)
        date_local = now_local.date().isoformat()
        block_key = f"{date_local}|0|20:00|22:00|playlist|10"
        schedule_state = {
            "timezone": "Europe/Zurich",
            "expanded_blocks": [
                {
                    "block_key": block_key,
                    "date_local": date_local,
                    "start_time_local": "20:00",
                    "end_time_local": "22:00",
                    "mode": "playlist",
                    "section_label": "Metal sinfonico",
                    "genre_labels": ["symphonic", "metal"],
                    "playlist_id": "10",
                    "playlist_name": "Symphonic Metal",
                }
            ],
        }

        context = resolve_schedule_context(
            schedule_state=schedule_state,
            ts=now_local.timestamp(),
            mention_state={block_key: {"start": True, "updated_at": now_local.timestamp()}},
        )
        self.assertIsNotNone(context)
        assert context is not None
        self.assertIsNone(context.mention_intent)

    def test_schedule_context_start_intent_late_fallback_expires(self) -> None:
        tz = ZoneInfo("Europe/Zurich")
        start_local = dt.datetime(2026, 2, 16, 20, 0, tzinfo=tz)
        now_local = start_local + dt.timedelta(minutes=11)
        date_local = now_local.date().isoformat()
        block_key = f"{date_local}|0|20:00|22:00|playlist|10"
        schedule_state = {
            "timezone": "Europe/Zurich",
            "expanded_blocks": [
                {
                    "block_key": block_key,
                    "date_local": date_local,
                    "start_time_local": "20:00",
                    "end_time_local": "22:00",
                    "mode": "playlist",
                    "section_label": "Metal sinfonico",
                    "genre_labels": ["symphonic", "metal"],
                    "playlist_id": "10",
                    "playlist_name": "Symphonic Metal",
                }
            ],
        }

        context = resolve_schedule_context(
            schedule_state=schedule_state,
            ts=now_local.timestamp(),
            mention_state={},
        )
        self.assertIsNotNone(context)
        assert context is not None
        self.assertEqual(context.phase, "start")
        self.assertIsNone(context.mention_intent)

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

    def test_seconds_until_schedule_block_change_uses_context_end_time(self) -> None:
        context = ScheduleContext(
            block_key="2026-02-16|0|12:00|13:00|playlist|10",
            section_label="Prog Dawn",
            genre_labels=["prog", "metal"],
            mode="playlist",
            playlist_name="Prog Metal",
            progress_ratio=0.5,
            phase="middle",
            mention_intent=None,
            next_section_label="Open Rotation",
            start_local_iso="2026-02-16T12:00:00+01:00",
            end_local_iso="2026-02-16T13:00:00+01:00",
        )

        ts = dt.datetime(2026, 2, 16, 12, 45, tzinfo=ZoneInfo("Europe/Zurich")).timestamp()

        seconds_until = seconds_until_schedule_block_change(context, ts)

        assert seconds_until is not None
        self.assertAlmostEqual(seconds_until, 15 * 60, delta=1)

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
        self.assertIn(context.block_key, state.schedule_block_mentions)
        self.assertEqual(state.schedule_block_mentions[context.block_key]["speak_count"], 1)
        self.assertNotIn("start", state.schedule_block_mentions[context.block_key])

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

    def test_ensure_schedule_genre_reference_injects_for_block_intro(self) -> None:
        context = ScheduleContext(
            block_key="2026-02-16|0|06:00|09:00|playlist|10",
            section_label="Power y sinfonico",
            genre_labels=["power metal", "metal sinfonico"],
            mode="playlist",
            playlist_name="Power Metal",
            progress_ratio=0.02,
            phase="start",
            mention_intent="start",
            next_section_label="Cruce folk",
            start_local_iso="2026-02-16T06:00:00+01:00",
            end_local_iso="2026-02-16T09:00:00+01:00",
        )
        rng = __import__("random").Random(11)
        script = "Arranca Power y sinfonico en NeuralForge, bien arriba para esta hora."

        out = ensure_schedule_genre_reference(script, Archetype.BLOCK_INTRO, context, rng)

        self.assertIn("Power y sinfonico", out)
        self.assertTrue(
            ("power metal" in out.lower()) or ("metal sinfonico" in out.lower())
        )

    def test_ensure_schedule_genre_reference_noops_when_genre_already_present(self) -> None:
        context = ScheduleContext(
            block_key="2026-02-16|1|12:00|15:00|playlist|11",
            section_label="Progresivo e instrumental",
            genre_labels=["metal progresivo", "prog instrumental"],
            mode="playlist",
            playlist_name="Prog Metal",
            progress_ratio=0.50,
            phase="mid",
            mention_intent="mid",
            next_section_label="n/a",
            start_local_iso="2026-02-16T12:00:00+01:00",
            end_local_iso="2026-02-16T15:00:00+01:00",
        )
        rng = __import__("random").Random(12)
        script = "Seguimos en Progresivo e instrumental, con ese clima de metal progresivo."

        out = ensure_schedule_genre_reference(script, Archetype.BACK_SELL, context, rng)

        self.assertEqual(out, script)

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
