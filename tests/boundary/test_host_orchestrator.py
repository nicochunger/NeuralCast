"""Offline boundary tests for the host-orchestrator runtime flow."""

from __future__ import annotations

import argparse
import random

from neuralcast.pipelines.host_orchestrator import main as host_main
from neuralcast.pipelines.host_orchestrator.models import (
    Archetype,
    QueueTrack,
    StoryAssets,
)
from neuralcast.pipelines.host_orchestrator.state import default_state


class FakeLock:
    def __init__(self, _path) -> None:
        self.released = False

    def acquire(self) -> bool:
        return True

    def release(self) -> None:
        self.released = True


class FakeHostAzuraCastClient:
    def __init__(
        self,
        *,
        current: QueueTrack,
        next_track: QueueTrack,
    ) -> None:
        self.current = current
        self.next_track = next_track
        self.uploads: list[object] = []
        self.telnet_commands: list[str] = []

    def get_stations(self) -> list[dict[str, object]]:
        return [{"id": 1, "shortcode": "neuralforge", "name": "NeuralForge"}]

    def get_now_playing(self, _station: str) -> dict[str, object]:
        return {
            "listeners": {"current": 7},
            "now_playing": {
                "remaining": 180,
                "song": {
                    "id": self.current.song_id,
                    "artist": self.current.artist,
                    "title": self.current.title,
                    "length": self.current.duration,
                },
            },
        }

    def get_upcoming_queue(self, _station: str) -> list[dict[str, object]]:
        return [
            {
                "id": self.current.queue_id,
                "duration": self.current.duration,
                "song": {
                    "id": self.current.song_id,
                    "artist": self.current.artist,
                    "title": self.current.title,
                },
            },
            {
                "id": self.next_track.queue_id,
                "duration": self.next_track.duration,
                "song": {
                    "id": self.next_track.song_id,
                    "artist": self.next_track.artist,
                    "title": self.next_track.title,
                },
            },
        ]

    def upload_media(self, *_args, **_kwargs):  # pragma: no cover - dry-run guard
        self.uploads.append((_args, _kwargs))
        raise AssertionError("dry-run uploaded media")

    def send_telnet_command(self, *_args, **_kwargs):  # pragma: no cover - dry-run guard
        self.telnet_commands.append(str(_args))
        raise AssertionError("dry-run queued media")


def test_host_orchestrator_runtime_dry_run_generates_assets_without_publish(tmp_path) -> None:
    station_dir = tmp_path / "Station"
    metadata_dir = station_dir / "metadata"
    metadata_dir.mkdir(parents=True)
    current = QueueTrack("current", "1", "Ghost", "Rats", 240)
    next_track = QueueTrack("next", "2", "Opeth", "Harvest", 300)
    saved_states: list[object] = []
    fake_client = FakeHostAzuraCastClient(current=current, next_track=next_track)
    state_path = metadata_dir / "state.json"
    lock_path = metadata_dir / "lock"

    deps = host_main.HostRuntimeDependencies(
        configure_logging=lambda: None,
        load_required_api_key=lambda: "api-key",
        create_client=lambda _base_url, _api_key, _verify_tls: fake_client,
        station_state_paths=lambda _station: (station_dir, state_path, lock_path),
        configure_station_file_logging=lambda _metadata_dir: (
            _metadata_dir / "main.log",
            _metadata_dir / "segments.log",
        ),
        create_lock=FakeLock,
        load_state=lambda _path, ts, rng, _cadence_settings=None: default_state(
            ts, rng
        ),
        save_state=lambda _path, state: saved_states.append(state),
        make_rng=lambda: random.Random(1),
        now=lambda: 1000.0,
        generate_script=lambda **_kwargs: (
            "Generated script",
            None,
            Archetype.BACK_SELL,
        ),
        create_story_assets=lambda **_kwargs: StoryAssets(
            text_path=tmp_path / "script.txt",
            audio_path=tmp_path / "script.mp3",
            story_text="Generated script",
            remote_path="AI Stories/script.mp3",
        ),
        publish_segment=lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("dry-run published")
        ),
    )
    runtime = host_main.HostOrchestratorRuntime(deps)

    result = runtime.run_cycle(
        host_main.HostCycleRequest(
            station="neuralforge",
            base_url="https://azuracast.local",
            dry_run=True,
            force_archetype=Archetype.BACK_SELL,
        )
    )

    assert result.status == "generated"
    assert result.current_track is not None
    assert result.current_track.artist == current.artist
    assert result.current_track.title == current.title
    assert result.next_track is not None
    assert result.next_track.artist == next_track.artist
    assert result.next_track.title == next_track.title
    assert result.used_archetype == Archetype.BACK_SELL
    assert result.segment_title == "Puente musical: Ghost - Rats -> Opeth - Harvest"
    assert result.assets is not None
    assert fake_client.uploads == []
    assert fake_client.telnet_commands == []
    assert saved_states


def test_host_orchestrator_run_preserves_argument_validation() -> None:
    args = argparse.Namespace(
        station="neuralforge",
        base_url="https://azuracast.local",
        dry_run=True,
        min_listeners=1,
        force_archetype=Archetype.BACK_SELL.value,
        force_track_focus=None,
        verify_tls=False,
        keep_local_days=3,
        keep_remote_days=7,
    )

    request = host_main._cycle_request_from_args(args)

    assert request.station == "neuralforge"
    assert request.force_archetype == Archetype.BACK_SELL
