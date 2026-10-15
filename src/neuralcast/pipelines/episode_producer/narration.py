"""Episode writing uses the existing host personality, speech and TTS boundaries."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from neuralcast.services.ai_client import synthesize_speech, DEFAULT_GEMINI_TTS_MODEL
from neuralcast.pipelines.host_orchestrator.channels import HostChannel
from neuralcast.pipelines.host_orchestrator.companion import (
    companion_prompt,
    split_companion_output,
)
from neuralcast.pipelines.host_orchestrator.models import Archetype, StationPersonality
from neuralcast.pipelines.host_orchestrator.prompts import (
    build_system_prompt,
    build_tts_instructions,
    station_name_for_generation,
)
from neuralcast.pipelines.host_orchestrator.speech import validate_speech_transcript
from neuralcast.pipelines.host_orchestrator.text_generation import gemini_generate_text
from neuralcast.pipelines.host_orchestrator.utils import run_with_retries
from neuralcast.config import ASSETS_ROOT
from .models import Commentary, Proposal, Summary
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
        )
        + companion_prompt(),
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
    proposal: Proposal,
    sequence: list[dict[str, Any]],
    previous: list[str],
    channel: HostChannel,
    settings: dict[str, str],
    workspace: Path,
    protected: tuple[str, ...],
) -> dict[str, Any]:
    settings = {
        **settings,
        "tts": writing_settings(channel, Archetype(segment.archetype))["tts"],
    }
    context = {
        "episode": proposal.model_dump(),
        "sequence": sequence,
        "intervention": segment.model_dump(),
        "previous_interventions": previous,
    }
    fingerprint = digest({"context": context, "settings": settings})
    directory = workspace / "narration" / fingerprint
    receipt = directory / "narration.json"
    if receipt.exists():
        saved = read_json(receipt)
        if (directory / "speech.mp3").exists() and saved.get("audio_hash") == file_hash(
            directory / "speech.mp3"
        ):
            return {**saved, "path": str(directory / "speech.mp3")}
    directory.mkdir(parents=True, exist_ok=True)
    if segment.script:
        script = segment.script
        summary = segment.companion
    else:
        prompt = (
            settings["template"].replace(
                "{context_json}", json.dumps(context, ensure_ascii=False)
            )
            + companion_prompt()
        )
        raw = run_with_retries(
            "Episode commentary",
            lambda: gemini_generate_text(prompt, settings["system"], 0.75, 0.9, False),
        )
        script, metadata = split_companion_output(raw)
        if script == "NO_SCRIPT" or metadata is None:
            raise ValueError(
                f"Commentary {segment.id} needs revision: no usable script/summary"
            )
        summary = Summary.model_validate(metadata)
    speech = validate_speech_transcript(script, protected_texts=protected)
    if len(speech.plain_text.split()) > segment.max_words:
        raise ValueError(f"Commentary {segment.id} exceeds its word budget")
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
        "script": speech.tts_text,
        "transcript": speech.plain_text,
        "companion": summary.model_dump(),
        "sources": [source.model_dump() for source in segment.sources],
        "audio_hash": file_hash(directory / "speech.mp3"),
    }
    write_json(receipt, saved)
    return {**saved, "path": str(directory / "speech.mp3")}
