#!/usr/bin/env python3
"""Unit tests for AI client defaults."""

from __future__ import annotations

import datetime as dt
import pathlib
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import pytest

from neuralcast.pipelines.host_orchestrator.assets import ensure_story_assets  # noqa: E402
from neuralcast.pipelines.host_orchestrator.models import (  # noqa: E402
    Archetype,
    QueueTrack,
)
from neuralcast.services import ai_client
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


def test_build_tts_prompt_wraps_instructions_without_reading_them() -> None:
    prompt = ai_client._build_tts_prompt(" Hola ", " Natural ")

    assert "INSTRUCCIONES (NO LEER EN VOZ ALTA):" in prompt
    assert "Natural" in prompt
    assert prompt.endswith("Hola")


def test_get_openai_client_requires_package_and_key(monkeypatch) -> None:
    monkeypatch.setattr(ai_client, "openai", None)

    with pytest.raises(RuntimeError, match="openai package"):
        ai_client.get_openai_client()

    monkeypatch.setattr(ai_client, "openai", object())
    monkeypatch.setattr(ai_client, "_OPENAI_KEY", "")

    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        ai_client.get_openai_client()


def test_get_gemini_client_requires_key(monkeypatch) -> None:
    monkeypatch.setattr(ai_client, "_GEMINI_KEY", "")
    monkeypatch.setattr(ai_client, "_GEMINI_CLIENT", None)

    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        ai_client.get_gemini_client()


def test_convert_wav_to_mp3_raises_with_ffmpeg_detail(tmp_path, monkeypatch) -> None:
    wav_path = tmp_path / "input.wav"
    mp3_path = tmp_path / "output.mp3"
    wav_path.write_bytes(b"wav")
    monkeypatch.setattr(
        ai_client.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            args=[],
            returncode=1,
            stdout="",
            stderr="broken codec",
        ),
    )

    with pytest.raises(RuntimeError, match="broken codec"):
        ai_client._convert_wav_to_mp3(wav_path, mp3_path)


def test_write_pcm_audio_file_writes_wav_and_removes_temp_mp3_source(tmp_path, monkeypatch) -> None:
    wav_path = tmp_path / "speech.wav"
    mp3_path = tmp_path / "speech.mp3"
    converted: list[tuple[pathlib.Path, pathlib.Path]] = []

    def fake_convert(temp_path: pathlib.Path, target_path: pathlib.Path) -> None:
        assert temp_path.exists()
        target_path.write_bytes(b"mp3")
        converted.append((temp_path, target_path))

    ai_client._write_pcm_audio_file(str(wav_path), b"\x00\x00" * 10)
    monkeypatch.setattr(ai_client, "_convert_wav_to_mp3", fake_convert)
    ai_client._write_pcm_audio_file(str(mp3_path), b"\x00\x00" * 10)

    assert wav_path.exists()
    assert mp3_path.read_bytes() == b"mp3"
    assert converted
    assert not converted[0][0].exists()


def test_synthesize_speech_routes_provider_and_rejects_unknown(monkeypatch, tmp_path) -> None:
    calls: list[str] = []
    monkeypatch.setattr(ai_client, "openai_speech", lambda **_kwargs: calls.append("openai"))
    monkeypatch.setattr(ai_client, "gemini_speech", lambda **_kwargs: calls.append("gemini"))

    ai_client.synthesize_speech("text", str(tmp_path / "a.mp3"), provider="openai")
    ai_client.synthesize_speech("text", str(tmp_path / "b.mp3"), provider="gemini")

    assert calls == ["openai", "gemini"]
    with pytest.raises(ValueError, match="Unsupported TTS provider"):
        ai_client.synthesize_speech("text", str(tmp_path / "c.mp3"), provider="bad")


def test_gemini_text_completion_rejects_empty_response(monkeypatch) -> None:
    class FakeModels:
        def generate_content(self, **_kwargs):
            return type("Response", (), {"text": ""})()

    monkeypatch.setattr(ai_client, "get_gemini_client", lambda: type("Client", (), {"models": FakeModels()})())

    with pytest.raises(RuntimeError, match="did not return any text"):
        ai_client.gemini_text_completion("prompt")
