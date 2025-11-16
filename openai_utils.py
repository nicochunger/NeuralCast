"""Utilities for interacting with OpenAI APIs."""
from __future__ import annotations

import os
import pathlib
from typing import Optional

import openai
from dotenv import load_dotenv

load_dotenv()

_OPENAI_KEY = os.getenv("OPENAI_API_KEY")
_OPENAI_CLIENT: Optional[openai.OpenAI] = None
_MODULE_DIR = pathlib.Path(__file__).resolve().parent
_HOST_INSTRUCTIONS_PATH = _MODULE_DIR / "host_instructions_prompt.txt"


def get_openai_client() -> openai.OpenAI:
    if _OPENAI_KEY is None or not _OPENAI_KEY.strip():
        raise RuntimeError(
            "OPENAI_API_KEY is not configured. Please set it in your environment."
        )

    global _OPENAI_CLIENT
    if _OPENAI_CLIENT is None:
        _OPENAI_CLIENT = openai.OpenAI(api_key=_OPENAI_KEY)
    return _OPENAI_CLIENT


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


def make_fun_fact(artist: str, title: str) -> str:
    prompt = (
        f"In one short, upbeat radio-host sentence (≤25 words), share a fun fact about the song '{title}' by {artist}."
        " Write it in argentinian spanish. "
    )
    return openai_text_completion(prompt).strip('"\n ')


def tts(text: str, outfile: str):
    instruction_prompt = _HOST_INSTRUCTIONS_PATH.read_text(encoding="utf-8").strip()
    openai_speech(
        text=text,
        outfile=outfile,
        model="gpt-4o-mini-tts",
        voice="ash",
        instructions=instruction_prompt,
    )


__all__ = [
    "get_openai_client",
    "openai_text_completion",
    "openai_speech",
    "make_fun_fact",
    "tts",
]
