"""Episode document constraints, host reuse, and sample-counted rendering."""

import wave
from pathlib import Path
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from neuralcast.pipelines.episode_producer import narration, rendering
from neuralcast.pipelines.episode_producer.models import (
    Proposal,
    Plan,
    Music,
    Commentary,
    Source,
)
from neuralcast.pipelines.episode_producer.storage import Project, digest, write_json
from neuralcast.pipelines.host_orchestrator.channels import get_channel_registry


@pytest.fixture
def proposal():
    return Proposal(
        episode_id="journey",
        channel="neuralforge-fr",
        title="Journey",
        premise="A theme",
        outline=["Folk roots", "Modern music"],
    )


def test_proposal_revision_invalidates_approval(tmp_path, proposal):
    project = Project(
        "neuralforge",
        proposal.episode_id,
        station_root=tmp_path / "station",
        runtime_root=tmp_path / "runtime",
    )
    write_json(project.documents / "proposal.json", proposal.model_dump())
    fingerprint = digest(proposal.model_dump())
    with pytest.raises(ValueError):
        project.approve_proposal("wrong")
    project.approve_proposal(fingerprint)
    assert project.approved_proposal()[1] == fingerprint
    write_json(
        project.documents / "proposal.json",
        {**proposal.model_dump(), "premise": "Changed direction"},
    )
    with pytest.raises(ValueError, match="not been approved"):
        project.approved_proposal()


@pytest.mark.parametrize(
    "playlist", ["../Other", "Folk/Metal", "Folk\\Metal", "Folk.csv"]
)
def test_recordings_require_playlist_stems(playlist):
    with pytest.raises(ValidationError):
        Music(
            kind="music",
            id="song",
            playlist=playlist,
            artist="A",
            title="B",
            rationale="Fits",
        )


def test_plan_rejects_duplicate_ids_and_requires_opening_and_closing():
    music = {
        "kind": "music",
        "id": "song",
        "playlist": "Folk Metal",
        "artist": "A",
        "title": "B",
        "rationale": "Fits",
    }
    intro = {"kind": "commentary", "id": "intro", "role": "opening", "brief": "Open"}
    outro = {"kind": "commentary", "id": "outro", "role": "closing", "brief": "Close"}
    assert (
        len(Plan(proposal_hash="a" * 64, segments=[intro, music, outro]).segments) == 3
    )
    for parts in (
        [music, intro, outro],
        [intro, music, intro],
        [intro, music, music, outro],
    ):
        with pytest.raises(ValidationError):
            Plan(proposal_hash="a" * 64, segments=parts)
    with pytest.raises(ValidationError):
        Source(url="javascript:alert(1)", claim="Fact")


def test_approved_script_channel_voice_and_companion_are_reused(
    tmp_path, monkeypatch, proposal
):
    channel = get_channel_registry().channels[proposal.channel]
    metadata = {
        "title": "Une histoire",
        "summary": "Une connexion musicale.",
        "key_points": [],
    }
    segment = Commentary(
        kind="commentary",
        id="intro",
        role="opening",
        brief="Open the theme",
        script="Une histoire.",
        companion=metadata,
    )

    def synthesize(**kwargs):
        Path(kwargs["outfile"]).write_bytes(b"narration")

    tts = Mock(side_effect=synthesize)
    monkeypatch.setattr(narration, "synthesize_speech", tts)
    settings = narration.writing_settings(channel)
    assert settings["tts_model"] == "gemini-3.8-flash-lite-tts"
    output = narration.prepare_narration(segment, channel, settings, tmp_path, ())
    assert output["transcript"] == "Une histoire."
    assert output["companion"] == metadata
    assert tts.call_args.kwargs["gemini_voice"] == channel.tts_voice
    assert "<COMPANION>" not in tts.call_args.kwargs["text"]
    assert not hasattr(narration, "gemini_generate_text")
    assert (
        narration.prepare_narration(segment, channel, settings, tmp_path, ()) == output
    )
    assert tts.call_count == 1
    # Metadata changes must not synthesize identical speech again or return old
    # reading summaries/sources from the cached narration receipt.
    changed = segment.model_copy(
        update={
            "companion": segment.companion.model_copy(
                update={"summary": "A revised reading summary."}
            )
        }
    )
    reused = narration.prepare_narration(changed, channel, settings, tmp_path, ())
    assert reused["companion"]["summary"] == "A revised reading summary."
    assert tts.call_count == 1
    flash = "gemini-3.8-flash-tts"
    flash_settings = narration.writing_settings(channel, tts_model=flash)
    alternative = narration.prepare_narration(
        segment, channel, flash_settings, tmp_path, ()
    )
    assert alternative["path"] != output["path"]
    assert tts.call_count == 2
    assert tts.call_args.kwargs["gemini_model"] == flash
    assert tts.call_args.kwargs["gemini_voice"] == channel.tts_voice
    assert (
        narration.prepare_narration(segment, channel, flash_settings, tmp_path, ())
        == alternative
    )
    assert tts.call_count == 2


def test_explicit_tts_model_requires_a_supported_gemini_backend(monkeypatch, proposal):
    channel = get_channel_registry().channels[proposal.channel]
    with pytest.raises(ValueError, match="Unsupported episode TTS model"):
        narration.writing_settings(channel, tts_model="wrong-model")
    monkeypatch.setenv("TTS_PROVIDER", "openai")
    with pytest.raises(ValueError, match="requires TTS_PROVIDER=gemini"):
        narration.writing_settings(channel, tts_model="gemini-3.8-flash-lite-tts")


def test_missing_script_stops_production_before_tts(tmp_path, monkeypatch, proposal):
    channel = get_channel_registry().channels[proposal.channel]
    segment = Commentary(
        kind="commentary", id="intro", role="opening", brief="Introduce"
    )
    tts = Mock()
    monkeypatch.setattr(narration, "synthesize_speech", tts)
    with pytest.raises(ValueError, match="complete script"):
        narration.prepare_narration(
            segment,
            channel,
            narration.writing_settings(channel),
            tmp_path,
            (),
        )
    tts.assert_not_called()


def test_render_has_exact_chapter_order_budget_and_cleans_scratch(
    tmp_path, monkeypatch
):
    commands = []

    def process(arguments):
        commands.append(arguments)
        destination = Path(arguments[-1])
        if destination.suffix == ".wav":
            with wave.open(str(destination), "wb") as stream:
                stream.setnchannels(2)
                stream.setsampwidth(2)
                stream.setframerate(44100)
                stream.writeframes(b"\0" * 44100 * 4)
        else:
            destination.write_bytes(b"episode")

    monkeypatch.setattr(rendering, "run_audio", process)
    monkeypatch.setattr(rendering, "tag_story_audio", lambda *args, **kwargs: None)
    monkeypatch.setattr(rendering, "embed_local_cover_art", lambda *args: None)
    monkeypatch.setattr(rendering, "audio_duration", lambda path: 3.0)
    parts = [
        {
            "id": str(index),
            "kind": kind,
            "path": f"/source with spaces/{index}.mp3",
            "title": kind,
        }
        for index, kind in enumerate(["commentary", "music", "commentary"])
    ]
    output, chapters, seconds = rendering.render(
        parts, tmp_path, "Episode", "fr-CH", 3, 2
    )
    assert seconds == 3
    assert [(chapter["start"], chapter["end"]) for chapter in chapters] == [
        (0, 1),
        (1, 2),
        (2, 3),
    ]
    assert [chapter["kind"] for chapter in chapters] == [
        "commentary",
        "music",
        "commentary",
    ]
    assert not list(tmp_path.glob("*.wav"))
    assert not (tmp_path / "concat.txt").exists()
    assert output.read_bytes() == b"episode"
    for command in commands[:3]:
        assert "loudnorm=I=-18:TP=-1.5:LRA=11" in command
    with pytest.raises(ValueError, match="budget"):
        rendering.render(parts, tmp_path, "Episode", "fr-CH", 3, 1)
    assert not list(tmp_path.glob("*.wav"))


def test_project_lock_prevents_concurrent_production(tmp_path):
    project = Project(
        "neuralforge", "locked", station_root=tmp_path, runtime_root=tmp_path
    )
    with project.lock():
        with pytest.raises(RuntimeError, match="Another episode"):
            with project.lock():
                pass
