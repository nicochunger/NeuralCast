"""Opt-in local ffmpeg render; no credentials, providers, uploads or station state."""

import math
import struct
import wave

import pytest

from neuralcast.pipelines.episode_producer.rendering import render, audio_duration


@pytest.mark.integration
@pytest.mark.allow_external_calls
def test_real_short_episode_render(tmp_path):
    parts = []
    for index, kind in enumerate(["commentary", "music", "commentary"]):
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
    output, chapters, duration = render(
        parts, tmp_path / "render", "Offline episode test", "en", 3, 3
    )
    assert output.exists()
    assert abs(audio_duration(output) - 3) < 0.15
    assert abs(chapters[-1]["end"] - 3) < 0.05
    assert duration > 0
    assert not list((tmp_path / "render").glob("*.wav"))
