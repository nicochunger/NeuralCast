"""Render complete recordings with sample-counted chapters and one final encoding."""

from __future__ import annotations

from array import array
import json
import math
import subprocess
import sys
import wave
from pathlib import Path
from typing import Any

from neuralcast.pipelines.host_orchestrator.assets import tag_story_audio
from neuralcast.audio.album_art import embed_local_cover_art
from neuralcast.config import ASSETS_ROOT

RATE = 44100
HOST_LEAD_IN_SECONDS = 1.5

COMMENTARY_AUDIO_FILTER = "highpass=f=80,loudnorm=I=-16:TP=-1.5:LRA=7"
MUSIC_AUDIO_FILTER = "loudnorm=I=-18.5:TP=-1.5:LRA=11"

SILENCE_THRESHOLD_DBFS = -50.0
SILENCE_WINDOW_MS = 20
MUSIC_LEADING_PAD_SECONDS = 0.05
MUSIC_LEADING_FADE_SECONDS = 0.02
MUSIC_TRAILING_PAD_SECONDS = 0.2
MUSIC_TRAILING_FADE_SECONDS = 0.05

RENDER_POLICY: dict[str, Any] = {
    "host_lead_in_seconds": HOST_LEAD_IN_SECONDS,
    "commentary_filter": COMMENTARY_AUDIO_FILTER,
    "music_filter": MUSIC_AUDIO_FILTER,
    "music_silence_trim": {
        "threshold_dbfs": SILENCE_THRESHOLD_DBFS,
        "window_ms": SILENCE_WINDOW_MS,
        "leading_pad_seconds": MUSIC_LEADING_PAD_SECONDS,
        "leading_fade_seconds": MUSIC_LEADING_FADE_SECONDS,
        "trailing_pad_seconds": MUSIC_TRAILING_PAD_SECONDS,
        "trailing_fade_seconds": MUSIC_TRAILING_FADE_SECONDS,
    },
}


def audio_filter_for(kind: str) -> str:
    return COMMENTARY_AUDIO_FILTER if kind == "commentary" else MUSIC_AUDIO_FILTER


def _window_rms(raw: bytes) -> float:
    if not raw:
        return 0.0
    samples = array("h")
    samples.frombytes(raw)
    if sys.byteorder != "little":
        samples.byteswap()
    return math.sqrt(sum(sample * sample for sample in samples) / len(samples))


def trim_wav_silence(
    path: Path,
    *,
    threshold_dbfs: float = SILENCE_THRESHOLD_DBFS,
    window_ms: int = SILENCE_WINDOW_MS,
    leading_pad_seconds: float = MUSIC_LEADING_PAD_SECONDS,
    leading_fade_seconds: float = MUSIC_LEADING_FADE_SECONDS,
    trailing_pad_seconds: float = MUSIC_TRAILING_PAD_SECONDS,
    trailing_fade_seconds: float = MUSIC_TRAILING_FADE_SECONDS,
) -> tuple[float, float]:
    """Trim dead leading/trailing silence from a 16-bit PCM WAV in place.

    Scans only from the outer edges inward so quiet passages inside a recording
    are never affected. Returns (trimmed_leading_seconds, trimmed_trailing_seconds).
    """
    rms_threshold = 32768.0 * (10.0 ** (threshold_dbfs / 20.0))
    with wave.open(str(path), "rb") as audio:
        channels = audio.getnchannels()
        sampwidth = audio.getsampwidth()
        rate = audio.getframerate()
        total_frames = audio.getnframes()
        if sampwidth != 2 or total_frames <= 0 or rate <= 0:
            return 0.0, 0.0
        window_frames = max(1, round(rate * window_ms / 1000))

        first_audible: int | None = None
        position = 0
        while position < total_frames:
            raw = audio.readframes(window_frames)
            if not raw:
                break
            if _window_rms(raw) >= rms_threshold:
                first_audible = position
                break
            position += len(raw) // (channels * sampwidth)

        if first_audible is None:
            return 0.0, 0.0

        last_audible_end = total_frames
        end = total_frames
        while end > first_audible:
            start = max(first_audible, end - window_frames)
            audio.setpos(start)
            raw = audio.readframes(end - start)
            if _window_rms(raw) >= rms_threshold:
                last_audible_end = end
                break
            end = start

        trim_start = max(0, first_audible - round(leading_pad_seconds * rate))
        trim_end = min(total_frames, last_audible_end + round(trailing_pad_seconds * rate))
        if trim_start == 0 and trim_end == total_frames:
            return 0.0, 0.0
        if trim_end <= trim_start:
            return 0.0, 0.0

        kept_frames = trim_end - trim_start
        fade_in_frames = (
            min(kept_frames, round(leading_fade_seconds * rate))
            if trim_start > 0
            else 0
        )
        fade_out_frames = (
            min(kept_frames, round(trailing_fade_seconds * rate))
            if trim_end < total_frames
            else 0
        )

        temporary = path.with_suffix(".trimmed.wav")
        try:
            with wave.open(str(temporary), "wb") as out:
                out.setnchannels(channels)
                out.setsampwidth(sampwidth)
                out.setframerate(rate)
                audio.setpos(trim_start)
                written = 0
                chunk_frames = 65536
                while written < kept_frames:
                    to_read = min(chunk_frames, kept_frames - written)
                    raw = audio.readframes(to_read)
                    if not raw:
                        break
                    chunk_len = len(raw) // (channels * sampwidth)
                    needs_fade_in = written < fade_in_frames
                    needs_fade_out = (
                        fade_out_frames > 0
                        and written + chunk_len > kept_frames - fade_out_frames
                    )
                    if needs_fade_in or needs_fade_out:
                        samples = array("h")
                        samples.frombytes(raw)
                        if sys.byteorder != "little":
                            samples.byteswap()
                        fade_out_start = kept_frames - fade_out_frames
                        for offset in range(chunk_len):
                            frame_idx = written + offset
                            gain = 1.0
                            if frame_idx < fade_in_frames:
                                gain *= frame_idx / fade_in_frames
                            if fade_out_frames > 0 and frame_idx >= fade_out_start:
                                gain *= (kept_frames - 1 - frame_idx) / fade_out_frames
                            if gain != 1.0:
                                base = offset * channels
                                for ch in range(channels):
                                    samples[base + ch] = round(samples[base + ch] * gain)
                        if sys.byteorder != "little":
                            samples.byteswap()
                        raw = samples.tobytes()
                    out.writeframes(raw)
                    written += chunk_len
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    return trim_start / rate, (total_frames - trim_end) / rate


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
) -> tuple[Path, list[dict[str, Any]], float]:
    directory.mkdir(parents=True, exist_ok=True)
    chapters = []
    elapsed = 0.0
    paths = []
    silence = None
    try:
        for index, part in enumerate(parts):
            lead_in = 0.0
            if part["kind"] == "commentary":
                frames = round(HOST_LEAD_IN_SECONDS * RATE)
                lead_in = frames / RATE
                if silence is None:
                    silence = directory / "host-lead-in.wav"
                    # Register before writing so failed writes are cleaned too.
                    paths.append(silence)
                    with wave.open(str(silence), "wb") as audio:
                        audio.setnchannels(2)
                        audio.setsampwidth(2)
                        audio.setframerate(RATE)
                        audio.writeframes(b"\0" * frames * 4)
                else:
                    paths.append(silence)
            pcm = directory / f"part-{index:03d}.wav"
            paths.append(pcm)
            # Apply role-specific loudness normalization to samples, not just ReplayGain tags.
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
                    audio_filter_for(part["kind"]),
                    "-ar",
                    str(RATE),
                    "-ac",
                    "2",
                    "-c:a",
                    "pcm_s16le",
                    str(pcm),
                ]
            )
            if part["kind"] == "music":
                trim_wav_silence(pcm)
            with wave.open(str(pcm), "rb") as audio:
                seconds = audio.getnframes() / audio.getframerate()
            if seconds <= 0:
                raise ValueError("Empty episode segment")
            chapter = {key: value for key, value in part.items() if key != "path"}
            chapters.append(
                {**chapter, "start": elapsed, "end": elapsed + lead_in + seconds}
            )
            elapsed += lead_in + seconds
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
