"""Opt-in local ffmpeg render; no credentials, providers, uploads or station state."""

import math
import struct
import subprocess
import wave

import pytest

from neuralcast.pipelines.episode_producer.rendering import (
    HOST_LEAD_IN_SECONDS,
    audio_duration,
    render,
)


@pytest.mark.integration
@pytest.mark.allow_external_calls
@pytest.mark.parametrize(
    "kinds",
    [
        ["commentary", "music", "commentary"],
        ["commentary", "commentary", "music", "commentary"],
    ],
)
def test_real_short_episode_render(tmp_path, kinds):
    parts = []
    for index, kind in enumerate(kinds):
        source = tmp_path / f"source-{index}.wav"
        with wave.open(str(source), "wb") as stream:
            stream.setnchannels(1)
            stream.setsampwidth(2)
            stream.setframerate(44100)
            stream.writeframes(
                b"".join(
                    struct.pack(
                        "<h",
                        int(
                            3000
                            * math.sin(
                                2 * math.pi * (220 + index * 110) * sample / 44100
                            )
                        ),
                    )
                    for sample in range(44100)
                )
            )
        parts.append(
            {"id": str(index), "kind": kind, "path": str(source), "title": kind}
        )
    speech_count = kinds.count("commentary")
    expected = len(parts) + speech_count * HOST_LEAD_IN_SECONDS
    output, chapters, duration = render(
        parts,
        tmp_path / "render",
        "Offline episode test",
        "es-AR",
        round(expected),
        speech_count,
    )
    assert output.exists()
    assert abs(audio_duration(output) - expected) < 0.15
    assert abs(chapters[-1]["end"] - expected) < 0.05
    assert len(chapters) == len(parts)
    assert [chapter["kind"] for chapter in chapters] == kinds
    for previous, current in zip(chapters, chapters[1:]):
        assert previous["end"] == current["start"]
    assert duration > 0
    assert not list((tmp_path / "render").glob("*.wav"))
    decoded = tmp_path / "decoded.wav"
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-i",
            str(output),
            "-ac",
            "1",
            "-ar",
            "44100",
            "-c:a",
            "pcm_s16le",
            str(decoded),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    with wave.open(str(decoded), "rb") as stream:
        samples = struct.unpack(
            "<" + "h" * stream.getnframes(), stream.readframes(stream.getnframes())
        )

    def rms(start, end):
        window = samples[round(start * 44100) : round(end * 44100)]
        return math.sqrt(sum(value * value for value in window) / len(window))

    for chapter in chapters:
        start = chapter["start"]
        if chapter["kind"] == "commentary":
            assert abs(chapter["end"] - start - 2.5) < 0.05
            assert rms(start + 0.2, start + 1.2) < 20
            assert rms(start + 1.7, start + 2.2) > 100
        else:
            assert abs(chapter["end"] - start - 1) < 0.05
            assert rms(start + 0.2, start + 0.7) > 100
