#!/usr/bin/env python3
"""Unit tests for AI client defaults."""

from __future__ import annotations

import datetime as dt
import pathlib
import tempfile
import unittest
from unittest.mock import patch

from neuralcast.pipelines.host_orchestrator.assets import ensure_story_assets  # noqa: E402
from neuralcast.pipelines.host_orchestrator.models import (  # noqa: E402
    Archetype,
    QueueTrack,
)
from neuralcast.services.ai_client import DEFAULT_GEMINI_TTS_MODEL  # noqa: E402


class AIClientDefaultsTest(unittest.TestCase):
    def test_default_gemini_tts_model_matches_expected_model_id(self) -> None:
        self.assertEqual(DEFAULT_GEMINI_TTS_MODEL, "gemini-3.1-flash-tts-preview")

    def test_ensure_story_assets_uses_default_gemini_tts_model(self) -> None:
        track = QueueTrack(
            queue_id="q1",
            song_id=None,
            artist="Artist",
            title="Title",
            duration=240,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch(
                "neuralcast.pipelines.host_orchestrator.assets.STORY_OUTPUT_DIR",
                pathlib.Path(tmpdir),
            ), patch(
                "neuralcast.pipelines.host_orchestrator.assets.synthesize_speech"
            ) as synthesize_mock, patch(
                "neuralcast.pipelines.host_orchestrator.assets.apply_replaygain"
            ):
                assets = ensure_story_assets(
                    station_slug="neuralforge",
                    current_track=track,
                    archetype=Archetype.SHORT_STORY,
                    script_text="Hola mundo",
                    tts_instructions="Natural",
                )

        synthesize_mock.assert_called_once()
        self.assertEqual(
            synthesize_mock.call_args.kwargs["gemini_model"],
            DEFAULT_GEMINI_TTS_MODEL,
        )
        self.assertTrue(assets.audio_path.name.endswith(".mp3"))
        self.assertEqual(
            assets.remote_path,
            "/".join(
                [
                    "AI Stories",
                    dt.datetime.now().strftime("%Y-%m-%d"),
                    assets.audio_path.name,
                ]
            ),
        )


if __name__ == "__main__":
    unittest.main()
