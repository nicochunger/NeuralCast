"""Explicit provisioning and inspection commands for Gemini custom TTS voices."""

from __future__ import annotations

import argparse
import base64
import pathlib
from collections.abc import Sequence
from typing import Any

from neuralcast.services.ai_client import DEFAULT_GEMINI_TTS_MODEL, get_gemini_client


def _audio_bytes(data: Any) -> bytes:
    if isinstance(data, bytes):
        return data
    if isinstance(data, str):
        return base64.b64decode(data)
    raise RuntimeError("Voice response did not contain preview audio data.")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create and manage persistent Gemini 3.8 TTS voice designs."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    create = subparsers.add_parser(
        "create", help="Create one persistent designed voice."
    )
    create.add_argument("--display-name", required=True)
    create.add_argument("--language-code", required=True, help="BCP-47 tag, e.g. es-AR")
    create.add_argument(
        "--prompt",
        required=True,
        help="One or two sentences describing permanent voice traits.",
    )
    create.add_argument("--gender", choices=("female", "male", "neutral"))
    create.add_argument("--model", default=DEFAULT_GEMINI_TTS_MODEL)
    create.add_argument(
        "--preview",
        type=pathlib.Path,
        help="Optional WAV destination for the returned preview.",
    )

    inspect = subparsers.add_parser(
        "inspect", help="Inspect one configured voice resource."
    )
    inspect.add_argument("voice_id")

    subparsers.add_parser("list", help="List stored voices in this Gemini project.")
    return parser


def run(args: argparse.Namespace) -> int:
    client = get_gemini_client()
    if args.command == "create":
        voice: dict[str, Any] = {
            "model": args.model,
            "type": "prompted",
            "display_name": args.display_name,
            "language_code": args.language_code,
            "prompted": {"input": args.prompt},
        }
        if args.gender:
            voice["gender"] = args.gender
        created = client.voices.create(store=True, voice=voice)
        print(f"voice_id={created.id}")
        print(f"expires_at={getattr(created, 'expire_time', None) or 'unknown'}")
        if args.preview:
            sample = getattr(created, "sample_audio", None)
            data = getattr(sample, "data", None)
            if data is None:
                raise RuntimeError("Gemini did not return a preview audio sample.")
            args.preview.parent.mkdir(parents=True, exist_ok=True)
            args.preview.write_bytes(_audio_bytes(data))
            print(f"preview={args.preview}")
        return 0

    if args.command == "inspect":
        voice = client.voices.get(id=args.voice_id)
        print(f"voice_id={voice.id}")
        print(f"display_name={getattr(voice, 'display_name', '')}")
        print(f"type={getattr(voice, 'type', '')}")
        print(f"language_code={getattr(voice, 'language_code', '')}")
        print(f"expires_at={getattr(voice, 'expire_time', '')}")
        return 0

    response = client.voices.list()
    for voice in response.voices or []:
        print(
            "\t".join(
                (
                    str(getattr(voice, "id", "")),
                    str(getattr(voice, "type", "")),
                    str(getattr(voice, "language_code", "")),
                    str(getattr(voice, "display_name", "")),
                    str(getattr(voice, "expire_time", "")),
                )
            )
        )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
