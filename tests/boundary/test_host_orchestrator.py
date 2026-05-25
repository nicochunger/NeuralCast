"""Offline boundary tests for the host-orchestrator runtime flow."""

from __future__ import annotations

import argparse
from types import SimpleNamespace

from neuralcast.pipelines.host_orchestrator import main as host_main
from neuralcast.pipelines.host_orchestrator.models import (
    Archetype,
    QueueTrack,
    StoryAssets,
    TrackMetadata,
)
from neuralcast.pipelines.host_orchestrator.state import default_state


class FakeLock:
    def __init__(self, _path) -> None:
        self.released = False

    def acquire(self) -> bool:
        return True

    def release(self) -> None:
        self.released = True


def test_host_orchestrator_run_dry_run_generates_assets_without_publish(tmp_path, monkeypatch) -> None:
    station_dir = tmp_path / "Station"
    metadata_dir = station_dir / "metadata"
    metadata_dir.mkdir(parents=True)
    current = QueueTrack("current", "1", "Ghost", "Rats", 240)
    next_track = QueueTrack("next", "2", "Opeth", "Harvest", 300)
    saved_states: list[object] = []

    args = argparse.Namespace(
        station="neuralforge",
        dry_run=True,
        force_archetype=None,
        force_track_focus=None,
    )

    monkeypatch.setattr(host_main, "configure_logging", lambda: None)
    monkeypatch.setattr(host_main, "_load_required_api_key", lambda: "api-key")
    monkeypatch.setattr(host_main, "now_ts", lambda: 1000.0)
    monkeypatch.setattr(
        host_main,
        "station_state_paths",
        lambda _station: (station_dir, metadata_dir / "state.json", metadata_dir / "lock"),
    )
    monkeypatch.setattr(
        host_main,
        "configure_station_file_logging",
        lambda _metadata_dir: (_metadata_dir / "main.log", _metadata_dir / "segments.log"),
    )
    monkeypatch.setattr(host_main, "StationLock", FakeLock)
    monkeypatch.setattr(
        host_main,
        "load_state",
        lambda _path, ts, rng: default_state(ts, rng),
    )
    monkeypatch.setattr(
        host_main,
        "save_state_atomic",
        lambda _path, state: saved_states.append(state),
    )
    monkeypatch.setattr(host_main, "prune_schedule_block_mentions", lambda mentions, _ts: mentions)
    monkeypatch.setattr(
        host_main,
        "_load_station_runtime",
        lambda **_kwargs: host_main.StationRuntime(
            station_dir=station_dir,
            client=SimpleNamespace(),
            station_id=1,
            generation_station_name="NeuralForge",
            station_personality=SimpleNamespace(script_profile="", tts_profile=""),
            schedule_state=None,
        ),
    )
    monkeypatch.setattr(
        host_main,
        "_fetch_playback_context",
        lambda *_args, **_kwargs: host_main.PlaybackContext(
            current_track=current,
            current_remaining=180,
            current_key="ghost|rats",
            listener_count=7,
        ),
    )
    monkeypatch.setattr(
        host_main,
        "_fetch_queue_context",
        lambda *_args, **_kwargs: host_main.QueueContext(
            upcoming_tracks=[next_track],
            next_track=next_track,
            schedule_context=None,
            schedule_reference_ts=1000.0,
        ),
    )
    monkeypatch.setattr(
        host_main,
        "_resolve_effective_forced_archetype",
        lambda **_kwargs: (None, False),
    )
    monkeypatch.setattr(
        host_main,
        "_select_archetype",
        lambda **_kwargs: Archetype.BACK_SELL,
    )
    monkeypatch.setattr(
        host_main,
        "_build_generation_context",
        lambda **_kwargs: host_main.GenerationContext(
            selected_archetype=Archetype.BACK_SELL,
            angle=None,
            hook="hook",
            banned_list=[],
            current_meta=TrackMetadata(album="Prequelle"),
            next_meta=TrackMetadata(album="Harvest"),
            forced_news_mode=False,
        ),
    )
    monkeypatch.setattr(
        host_main,
        "generate_archetype_script",
        lambda **_kwargs: ("Generated script", None, Archetype.BACK_SELL),
    )
    monkeypatch.setattr(host_main, "build_tts_instructions", lambda _personality: "tts")
    monkeypatch.setattr(host_main, "run_with_retries", lambda _label, func: func())
    monkeypatch.setattr(
        host_main,
        "ensure_story_assets",
        lambda **_kwargs: StoryAssets(
            text_path=tmp_path / "script.txt",
            audio_path=tmp_path / "script.mp3",
            story_text="Generated script",
            remote_path="AI Stories/script.mp3",
        ),
    )
    monkeypatch.setattr(
        host_main,
        "_publish_segment",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("dry-run published")),
    )

    host_main.run(args)

    assert saved_states
