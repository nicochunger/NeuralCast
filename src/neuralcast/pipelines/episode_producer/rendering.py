"""Render complete recordings with sample-counted chapters and one final encoding."""

from __future__ import annotations

import json
import subprocess
import wave
from pathlib import Path
from typing import Any

from neuralcast.pipelines.host_orchestrator.assets import tag_story_audio
from neuralcast.audio.album_art import embed_local_cover_art
from neuralcast.config import ASSETS_ROOT

RATE = 44100


def run_audio(arguments: list[str], *, timeout: int = 1800) -> None:
    result = subprocess.run(arguments, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(
            f"Audio processing failed ({arguments[0]}): {result.stderr[-1500:]}"
        )


def audio_duration(path: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode:
        raise ValueError(f"Cannot measure audio: {path.name}")
    duration = float(json.loads(result.stdout)["format"]["duration"])
    if not 0 < duration < 14400:
        raise ValueError("Invalid audio duration")
    return duration


def render(
    parts: list[dict[str, Any]],
    directory: Path,
    title: str,
    language: str,
    target_seconds: int,
    speech_budget: int,
) -> tuple[Path, list[dict[str, Any]], float]:
    directory.mkdir(parents=True, exist_ok=True)
    chapters = []
    elapsed = 0.0
    speech_seconds = 0.0
    paths = []
    try:
        for index, part in enumerate(parts):
            pcm = directory / f"part-{index:03d}.wav"
            paths.append(pcm)
            # Apply a common loudness target to samples, not just ReplayGain tags.
            run_audio(
                [
                    "ffmpeg",
                    "-nostdin",
                    "-v",
                    "error",
                    "-y",
                    "-i",
                    part["path"],
                    "-vn",
                    "-af",
                    "loudnorm=I=-18:TP=-1.5:LRA=11",
                    "-ar",
                    str(RATE),
                    "-ac",
                    "2",
                    "-c:a",
                    "pcm_s16le",
                    str(pcm),
                ]
            )
            with wave.open(str(pcm), "rb") as audio:
                seconds = audio.getnframes() / audio.getframerate()
            if seconds <= 0:
                raise ValueError("Empty episode segment")
            chapter = {key: value for key, value in part.items() if key != "path"}
            chapters.append({**chapter, "start": elapsed, "end": elapsed + seconds})
            elapsed += seconds
            if part["kind"] == "commentary":
                speech_seconds += seconds
        if speech_seconds > speech_budget:
            raise ValueError(
                f"Narration is {speech_seconds:.0f}s, exceeding the approved {speech_budget}s budget"
            )
        if elapsed > target_seconds * 1.15 or elapsed < target_seconds * 0.75:
            raise ValueError(
                f"Episode is {elapsed:.0f}s; adjust the approved target or song selection ({target_seconds}s target, allowed -25%/+15%)"
            )
        listing = directory / "concat.txt"
        # Generated basenames only; source paths never enter concat-file syntax.
        listing.write_text(
            "\n".join(f"file '{path.name}'" for path in paths) + "\n", encoding="utf-8"
        )
        temporary = directory / "episode.partial.mp3"
        run_audio(
            [
                "ffmpeg",
                "-nostdin",
                "-v",
                "error",
                "-y",
                "-f",
                "concat",
                "-safe",
                "1",
                "-i",
                str(listing),
                "-c:a",
                "libmp3lame",
                "-b:a",
                "192k",
                "-ar",
                str(RATE),
                str(temporary),
            ]
        )
        tag_story_audio(temporary, title, language=language)
        cover = ASSETS_ROOT / "images" / "Thumbnail_logo.png"
        if cover.exists():
            embed_local_cover_art(temporary, cover)
        duration = audio_duration(temporary)
        if abs(duration - elapsed) > 0.25:
            raise ValueError("Encoded duration does not match chapter timings")
        destination = directory / "episode.mp3"
        temporary.replace(destination)
        return destination, chapters, duration
    finally:
        for path in paths:
            path.unlink(missing_ok=True)
        (directory / "concat.txt").unlink(missing_ok=True)
        (directory / "episode.partial.mp3").unlink(missing_ok=True)
