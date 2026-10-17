"""Synthesize frozen manuscript passages with the configured host voice."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from neuralcast.services.ai_client import synthesize_speech, DEFAULT_GEMINI_TTS_MODEL
from neuralcast.pipelines.host_orchestrator.channels import HostChannel
from neuralcast.pipelines.host_orchestrator.models import Archetype, StationPersonality
from neuralcast.pipelines.host_orchestrator.prompts import (
    build_system_prompt,
    build_tts_instructions,
    station_name_for_generation,
)
from neuralcast.pipelines.host_orchestrator.speech import validate_speech_transcript
from neuralcast.pipelines.host_orchestrator.utils import run_with_retries
from neuralcast.config import ASSETS_ROOT
from .models import Commentary
from .storage import digest, read_json, write_json, file_hash

TEMPLATE = ASSETS_ROOT / "stories" / "prompts" / "episode_commentary.md"


def writing_settings(
    channel: HostChannel, archetype: Archetype = Archetype.SHORT_STORY
) -> dict[str, str]:
    personality = StationPersonality(
        script_profile=channel.script_style_override or channel.brand.script_style,
        tts_profile=channel.brand.tts_style,
    )
    return {
        "system": build_system_prompt(
            station_name_for_generation(
                channel.brand.personality_station, channel.brand.key
            ),
            personality,
            locale=channel.locale,
        ),
        "tts": build_tts_instructions(
            personality,
            locale=channel.locale,
            override_path=channel.tts_instructions_override_path,
            archetype=archetype,
        ),
        "template": TEMPLATE.read_text(encoding="utf-8"),
        "voice": channel.tts_voice,
        "tts_model": DEFAULT_GEMINI_TTS_MODEL,
        "provider": os.getenv("TTS_PROVIDER", "gemini").strip().lower(),
    }


def prepare_narration(
    segment: Commentary,
    channel: HostChannel,
    settings: dict[str, str],
    workspace: Path,
    protected: tuple[str, ...],
) -> dict[str, Any]:
    settings = {
        **settings,
        "tts": writing_settings(channel, Archetype(segment.archetype))["tts"],
    }
    if not segment.script or not segment.companion:
        raise ValueError(f"Commentary {segment.id} needs an approved complete script")
    speech = validate_speech_transcript(segment.script, protected_texts=protected)
    if len(speech.plain_text.split()) > segment.max_words:
        raise ValueError(f"Commentary {segment.id} exceeds its word budget")
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
        if (directory / "speech.mp3").exists() and saved.get("audio_hash") == file_hash(
            directory / "speech.mp3"
        ):
            return {**saved, **metadata, "path": str(directory / "speech.mp3")}
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "script.txt").write_text(speech.tts_text + "\n", encoding="utf-8")
    temporary = directory / "speech.partial.mp3"
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
        temporary.replace(directory / "speech.mp3")
    finally:
        temporary.unlink(missing_ok=True)
    saved = {
        **metadata,
        "audio_hash": file_hash(directory / "speech.mp3"),
    }
    write_json(receipt, saved)
    return {**saved, "path": str(directory / "speech.mp3")}
