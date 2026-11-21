"""Synthesize frozen manuscript passages with the configured host voice."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
import wave

from neuralcast.services.ai_client import synthesize_speech, DEFAULT_GEMINI_TTS_MODEL
from neuralcast.pipelines.host_orchestrator.channels import HostChannel
from neuralcast.pipelines.host_orchestrator.models import Archetype, StationPersonality
from neuralcast.pipelines.host_orchestrator.prompts import build_tts_instructions
from neuralcast.pipelines.host_orchestrator.speech import validate_speech_transcript
from neuralcast.pipelines.host_orchestrator.utils import run_with_retries
from neuralcast.config import ASSETS_ROOT
from .models import Commentary
from .storage import digest, read_json, write_json, file_hash

TTS_INSTRUCTIONS = ASSETS_ROOT / "stories" / "prompts" / "episode_tts_instructions.md"
SUPPORTED_TTS_MODELS = ("gemini-3.8-flash-tts", "gemini-3.8-flash-lite-tts")


def writing_settings(
    channel: HostChannel,
    archetype: Archetype = Archetype.SHORT_STORY,
    *,
    tts_model: str | None = None,
) -> dict[str, str]:
    provider = os.getenv("TTS_PROVIDER", "gemini").strip().lower()
    if tts_model is not None:
        if tts_model not in SUPPORTED_TTS_MODELS:
            raise ValueError(f"Unsupported episode TTS model: {tts_model}")
        if provider != "gemini":
            raise ValueError("--tts-model requires TTS_PROVIDER=gemini")
    personality = StationPersonality(
        script_profile=channel.script_style_override or channel.brand.script_style,
        tts_profile=channel.brand.tts_style,
    )
    return {
        "tts": build_tts_instructions(
            personality,
            locale=channel.locale,
            override_path=TTS_INSTRUCTIONS,
            archetype=archetype,
        ),
        "voice": channel.tts_voice,
        "tts_model": tts_model or DEFAULT_GEMINI_TTS_MODEL,
        "provider": provider,
    }


def _cached_audio_path(directory: Path, expected_hash: str | None) -> Path | None:
    if not expected_hash:
        return None
    for filename in ("speech.wav", "speech.mp3"):
        candidate = directory / filename
        if candidate.exists() and file_hash(candidate) == expected_hash:
            return candidate
    return None


def _verify_synthesized_audio(path: Path, plain_text: str) -> None:
    if not path.is_file() or path.stat().st_size == 0:
        raise RuntimeError("Episode TTS produced an empty audio file")
    with path.open("rb") as handle:
        header = handle.read(4)
    if header == b"RIFF":
        with wave.open(str(path), "rb") as wav:
            rate = wav.getframerate()
            duration = wav.getnframes() / rate if rate > 0 else 0.0
        words = len(plain_text.split())
        minimum_seconds = max(0.5, words / 8.0)
        if duration < minimum_seconds:
            raise RuntimeError(
                f"Episode TTS audio looks truncated ({duration:.2f}s for {words} words)"
            )


def prepare_narration(
    segment: Commentary,
    channel: HostChannel,
    settings: dict[str, str],
    workspace: Path,
    protected: tuple[str, ...],
) -> dict[str, Any]:
    settings = {
        **settings,
        "tts": writing_settings(channel, Archetype(segment.archetype))["tts"]
        + (("\n" + settings["delivery"]) if settings.get("delivery") else ""),
    }
    if not segment.script or not segment.companion:
        raise ValueError(f"Commentary {segment.id} needs an approved complete script")
    speech = validate_speech_transcript(segment.script, protected_texts=protected)
    metadata = {
        "script": speech.tts_text,
        "transcript": speech.plain_text,
        "companion": segment.companion.model_dump(),
        "sources": [source.model_dump() for source in segment.sources],
    }
    fingerprint = digest(
        {
            "script": speech.tts_text,
            "settings": {
                key: settings[key] for key in ("tts", "voice", "tts_model", "provider")
            },
        }
    )
    directory = workspace / "narration" / fingerprint
    receipt = directory / "narration.json"
    if receipt.exists():
        saved = read_json(receipt)
        cached = _cached_audio_path(directory, saved.get("audio_hash"))
        if cached is not None:
            return {**saved, **metadata, "path": str(cached)}
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "script.txt").write_text(speech.tts_text + "\n", encoding="utf-8")
    extension = ".wav" if settings["provider"] == "gemini" else ".mp3"
    target = directory / f"speech{extension}"
    temporary = directory / f"speech.partial{extension}"
    try:
        run_with_retries(
            "Episode TTS",
            lambda: synthesize_speech(
                text=speech.tts_text,
                provider=settings["provider"],
                outfile=str(temporary),
                instructions=settings["tts"],
                gemini_model=settings["tts_model"],
                gemini_voice=channel.tts_voice,
            ),
        )
        _verify_synthesized_audio(temporary, speech.plain_text)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    saved = {
        **metadata,
        "audio_hash": file_hash(target),
    }
    write_json(receipt, saved)
    return {**saved, "path": str(target)}
