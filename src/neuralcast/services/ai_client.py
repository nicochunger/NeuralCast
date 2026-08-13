"""Utilities for interacting with OpenAI APIs and Gemini TTS."""
from __future__ import annotations

import os
import pathlib
import subprocess
import tempfile
import wave
from typing import Any, Optional

try:
    import openai
except ModuleNotFoundError:  # pragma: no cover - dependency guard
    openai = None  # type: ignore[assignment]

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:  # pragma: no cover - dependency guard
    def load_dotenv(*_args: Any, **_kwargs: Any) -> bool:
        return False

from neuralcast.config import ASSETS_ROOT

load_dotenv()

_OPENAI_KEY = os.getenv("OPENAI_API_KEY")
_OPENAI_CLIENT: Optional[Any] = None
_GEMINI_KEY = os.getenv("GEMINI_API_KEY")
_GEMINI_CLIENT = None
_HOST_INSTRUCTIONS_PATH = ASSETS_ROOT / "host_instructions_prompt.txt"
_DEFAULT_TTS_PROVIDER = os.getenv("TTS_PROVIDER", "gemini").strip().lower()
DEFAULT_GEMINI_TEXT_MODEL = os.getenv("GEMINI_TEXT_MODEL", "gemini-3.7-flash")
# Keep the private name for callers that imported it before the public constant
# was introduced.
_DEFAULT_GEMINI_TEXT_MODEL = DEFAULT_GEMINI_TEXT_MODEL
DEFAULT_GEMINI_TTS_MODEL = os.getenv(
    "GEMINI_TTS_MODEL", "gemini-3.1-flash-tts-preview"
)


def get_openai_client() -> openai.OpenAI:
    if openai is None:
        raise RuntimeError(
            "openai package is not installed. Install with: pip install openai"
        )
    if _OPENAI_KEY is None or not _OPENAI_KEY.strip():
        raise RuntimeError(
            "OPENAI_API_KEY is not configured. Please set it in your environment."
        )

    global _OPENAI_CLIENT
    if _OPENAI_CLIENT is None:
        _OPENAI_CLIENT = openai.OpenAI(api_key=_OPENAI_KEY)
    return _OPENAI_CLIENT


def get_gemini_client():
    if _GEMINI_KEY is None or not _GEMINI_KEY.strip():
        raise RuntimeError(
            "GEMINI_API_KEY is not configured. Please set it in your environment."
        )

    global _GEMINI_CLIENT
    if _GEMINI_CLIENT is None:
        try:
            from google import genai
        except ImportError as exc:
            raise RuntimeError(
                "Gemini client is not installed. Install with: pip install google-genai"
            ) from exc
        _GEMINI_CLIENT = genai.Client(api_key=_GEMINI_KEY)
    return _GEMINI_CLIENT


def openai_text_completion(
    prompt: str,
    system_prompt: Optional[str] = None,
    model: str = "gpt-4o",
    response_format=None,
):
    client = get_openai_client()
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    if response_format:
        completion = client.beta.chat.completions.parse(
            model=model,
            messages=messages,
            response_format=response_format,
        )
        return completion.choices[0].message.parsed

    completion = client.chat.completions.create(
        model=model,
        messages=messages,
    )
    return completion.choices[0].message.content


def openai_speech(
    text: str,
    outfile: str,
    model: str = "gpt-4o-mini-tts",
    voice: str = "ash",
    instructions: Optional[str] = None,
):
    client = get_openai_client()
    kwargs = {
        "model": model,
        "voice": voice,
        "input": text,
    }
    if instructions:
        kwargs["instructions"] = instructions
    with client.audio.speech.with_streaming_response.create(**kwargs) as response:
        response.stream_to_file(outfile)


def _build_tts_prompt(text: str, instructions: Optional[str]) -> str:
    cleaned_text = text.strip()
    if not instructions:
        return cleaned_text
    cleaned_instructions = instructions.strip()
    return (
        "INSTRUCCIONES (NO LEER EN VOZ ALTA):\n"
        f"{cleaned_instructions}\n\n"
        "TEXTO A LEER EN VOZ ALTA:\n"
        f"{cleaned_text}"
    )


def _write_pcm_wave(
    outfile: pathlib.Path,
    pcm: bytes,
    channels: int = 1,
    rate: int = 24000,
    sample_width: int = 2,
) -> None:
    with wave.open(str(outfile), "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sample_width)
        wf.setframerate(rate)
        wf.writeframes(pcm)


def _convert_wav_to_mp3(wav_path: pathlib.Path, mp3_path: pathlib.Path) -> None:
    result = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(wav_path),
            "-codec:a",
            "libmp3lame",
            "-b:a",
            "192k",
            str(mp3_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"ffmpeg failed while converting TTS audio: {detail}")


def _write_pcm_audio_file(
    outfile: str,
    pcm: bytes,
    channels: int = 1,
    rate: int = 24000,
    sample_width: int = 2,
) -> None:
    target = pathlib.Path(outfile)
    suffix = target.suffix.lower()
    if suffix == ".wav":
        _write_pcm_wave(target, pcm, channels=channels, rate=rate, sample_width=sample_width)
        return
    if suffix == ".mp3":
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as handle:
            temp_path = pathlib.Path(handle.name)
        try:
            _write_pcm_wave(
                temp_path,
                pcm,
                channels=channels,
                rate=rate,
                sample_width=sample_width,
            )
            _convert_wav_to_mp3(temp_path, target)
        finally:
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass
        return
    _write_pcm_wave(target, pcm, channels=channels, rate=rate, sample_width=sample_width)


def gemini_speech(
    text: str,
    outfile: str,
    model: str = DEFAULT_GEMINI_TTS_MODEL,
    voice: str = "Enceladus",
    instructions: Optional[str] = None,
):
    client = get_gemini_client()
    try:
        from google.genai import types
    except ImportError as exc:
        raise RuntimeError(
            "Gemini client is not installed. Install with: pip install google-genai"
        ) from exc

    prompt = _build_tts_prompt(text, instructions)
    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice)
                )
            ),
        ),
    )
    data = response.candidates[0].content.parts[0].inline_data.data
    _write_pcm_audio_file(outfile, data)


def gemini_text_completion(prompt: str, model: Optional[str] = None) -> str:
    client = get_gemini_client()
    try:
        from google.genai import types
    except ImportError as exc:
        raise RuntimeError(
            "Gemini client is not installed. Install with: pip install google-genai"
        ) from exc

    grounding_tool = types.Tool(google_search=types.GoogleSearch())
    config = types.GenerateContentConfig(tools=[grounding_tool])
    response = client.models.generate_content(
        model=(model or _DEFAULT_GEMINI_TEXT_MODEL),
        contents=prompt,
        config=config,
    )
    if not response or not response.text:
        raise RuntimeError("Gemini did not return any text for the story prompt.")
    return response.text


def synthesize_speech(
    text: str,
    outfile: str,
    provider: Optional[str] = None,
    instructions: Optional[str] = None,
    openai_model: str = "gpt-4o-mini-tts",
    openai_voice: str = "ash",
    gemini_model: str = DEFAULT_GEMINI_TTS_MODEL,
    gemini_voice: str = "Enceladus",
):
    resolved_provider = (provider or _DEFAULT_TTS_PROVIDER).strip().lower()
    if resolved_provider == "openai":
        openai_speech(
            text=text,
            outfile=outfile,
            model=openai_model,
            voice=openai_voice,
            instructions=instructions,
        )
        return
    if resolved_provider == "gemini":
        gemini_speech(
            text=text,
            outfile=outfile,
            model=gemini_model,
            voice=gemini_voice,
            instructions=instructions,
        )
        return
    raise ValueError(
        f"Unsupported TTS provider '{resolved_provider}'. Use 'gemini' or 'openai'."
    )


def make_fun_fact(artist: str, title: str) -> str:
    prompt = (
        f"In one short, upbeat radio-host sentence (≤25 words), share a fun fact about the song '{title}' by {artist}."
        " Write it in argentinian spanish. "
    )
    return openai_text_completion(prompt).strip('"\n ')


def tts(text: str, outfile: str):
    instruction_prompt = _HOST_INSTRUCTIONS_PATH.read_text(encoding="utf-8").strip()
    synthesize_speech(
        text=text,
        outfile=outfile,
        instructions=instruction_prompt,
    )


__all__ = [
    "DEFAULT_GEMINI_TTS_MODEL",
    "DEFAULT_GEMINI_TEXT_MODEL",
    "get_openai_client",
    "openai_text_completion",
    "openai_speech",
    "gemini_speech",
    "gemini_text_completion",
    "make_fun_fact",
    "synthesize_speech",
    "tts",
]
