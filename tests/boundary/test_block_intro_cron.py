"""Exercise preparation, disk state, later cron runs and final queue insertion."""

import datetime as dt
import random

import pytest

from neuralcast.pipelines.host_orchestrator import main as host
from neuralcast.pipelines.host_orchestrator.models import Archetype, StoryAssets
from neuralcast.pipelines.host_orchestrator.state import load_state
from neuralcast.services.azuracast_config import AzuraCastSettings

START = dt.datetime(2026, 9, 12, 18, tzinfo=dt.timezone.utc).timestamp()


def row(name, at):
    return {
        "song": {"id": name, "artist": "Artist", "title": name},
        "played_at": at,
        "duration": 180,
        "playlist": "Irrelevant old playlist",
    }


@pytest.fixture
def scenario(tmp_path, monkeypatch):
    class Client:
        now = START - 300
        current = row("current", START - 480)
        remaining = 60
        queue = [
            row("one", START - 240),
            row("two", START - 60),
            row("three", START + 120),
        ]
        uploads = 0
        commands = []
        after_upload = None

        def get_stations(self):
            return [{"id": 2, "shortcode": "neuralforge", "name": "NeuralForge"}]

        def get_now_playing(self, _station):
            return {
                "listeners": {"current": 4},
                "now_playing": {**self.current, "remaining": self.remaining},
            }

        def get_upcoming_queue(self, _station):
            return self.queue

        def upload_media(self, *_args, **_kwargs):
            self.uploads += 1
            if self.after_upload:
                self.after_upload()
            return {"id": 999, "song_id": "intro", "path": "intro.mp3", "length": 20}

        def send_telnet_command(self, _station_id, command):
            self.commands.append(command)
            return {}

    client = Client()
    calls = []
    schedule = {
        "timezone": "UTC",
        "expanded_blocks": [
            {
                "block_key": "2026-09-12|evening",
                "date_local": "2026-09-12",
                "start_time_local": "18:00",
                "end_time_local": "19:00",
                "section_label": "Evening",
                "mode": "playlist",
                "playlist_name": "New playlist",
            }
        ],
    }
    monkeypatch.setattr(host, "load_schedule_state_payload", lambda _: schedule)
    monkeypatch.setattr(host, "now_ts", lambda: client.now)
    monkeypatch.setattr(host, "load_station_track_metadata", lambda *_: {})
    monkeypatch.setattr(host, "cleanup_local_stories", lambda *_: None)
    monkeypatch.setattr(host, "cleanup_remote_stories", lambda *_, **__: None)
    monkeypatch.setattr(host, "log_segment_event", lambda **_: None)

    def generate(**kwargs):
        calls.append(kwargs)
        return f"Coming up: {kwargs['next_track'].title}", None, Archetype.BLOCK_INTRO

    def assets(**kwargs):
        text = tmp_path / f"intro-{len(calls)}.txt"
        audio = text.with_suffix(".mp3")
        text.write_text(kwargs["script_text"])
        audio.write_bytes(b"fake audio")
        return StoryAssets(text, audio, kwargs["script_text"], audio.name)

    state_path = tmp_path / "metadata" / "state.json"
    deps = host.HostRuntimeDependencies(
        configure_logging=lambda: None,
        load_settings=lambda *_: AzuraCastSettings(
            "https://radio.invalid", "key", "neuralforge"
        ),
        create_client=lambda *_: client,
        station_state_paths=lambda _: (tmp_path, state_path, tmp_path / "lock"),
        configure_station_file_logging=lambda _: (
            tmp_path / "main.log",
            tmp_path / "segments.log",
        ),
        now=lambda: client.now,
        make_rng=lambda: random.Random(1),
        generate_script=generate,
        create_story_assets=assets,
    )
    runtime = host.HostOrchestratorRuntime(deps)

    def cycle(*, normal=False, dry_run=False):
        return runtime.run_cycle(
            host.HostCycleRequest(
                station="neuralforge",
                base_url=None,
                dry_run=dry_run,
                scheduled_block_intros_only=not normal,
            )
        )

    def state():
        return load_state(state_path, client.now, random.Random(1))

    def ready():
        client.now = START - 30
        client.current = row("two", START - 60)
        client.remaining = 150
        client.queue = [row("three", START + 120)]

    return client, calls, cycle, state, ready, state_path


def test_cron_prepares_for_third_song_reuses_and_publishes_once(scenario):
    client, calls, cycle, state, ready, _ = scenario
    cycle()
    assert calls[0]["next_track"].title == "three"
    assert calls[0]["current_track"].title == "two"
    assert [t.title for t in calls[0]["upcoming_tracks"]] == ["three"]
    assert client.uploads == 0
    assert state().pending_block_intro is not None
    assert not state().schedule_block_mentions
    deadline = state().next_speak_deadline_ts

    # A normal host job must respect the pending intro too.
    client.now = START - 210
    client.current = row("one", START - 240)
    client.remaining = 150
    client.queue = client.queue[1:]
    cycle(normal=True)
    assert len(calls) == 1
    assert client.commands == []
    assert state().next_speak_deadline_ts == deadline

    ready()
    result = cycle()
    assert result.status == "published"
    assert len(calls) == 1
    assert len(client.commands) == 1
    assert client.commands[0].startswith("requests.push ")
    assert state().pending_block_intro is None
    assert state().schedule_block_mentions["2026-09-12|evening"]["start"] is True
    cycle()
    assert len(client.commands) == 1


def test_changed_target_regenerates_instead_of_announcing_old_song(scenario):
    client, calls, cycle, state, ready, _ = scenario
    cycle()
    client.queue[-1] = row("replacement", START + 120)
    cycle()
    assert len(calls) == 2
    assert calls[-1]["next_track"].title == "replacement"
    assert state().pending_block_intro["target"]["title"] == "replacement"
    assert not client.commands


def test_missed_boundary_discards_intro(scenario):
    client, calls, cycle, state, _, _ = scenario
    cycle()
    client.now = START + 150
    client.current = row("three", START + 120)
    client.remaining = 150
    client.queue = [row("four", START + 300)]
    cycle()
    assert state().pending_block_intro is None
    assert len(calls) == 1
    assert not client.commands


def test_dry_run_does_not_persist_or_publish_preparation(scenario):
    client, calls, cycle, state, _, _ = scenario
    assert cycle(dry_run=True).status == "generated"
    assert state().pending_block_intro is None
    assert client.uploads == 0
    assert not client.commands


def test_ready_dry_run_preserves_pending_intro(scenario):
    client, calls, cycle, state, ready, _ = scenario
    cycle()
    pending = state().pending_block_intro
    ready()
    cycle(dry_run=True)
    assert state().pending_block_intro == pending
    assert len(calls) == 1
    assert client.uploads == 0


def test_queue_change_during_upload_does_not_submit(scenario):
    client, calls, cycle, state, ready, _ = scenario
    cycle()
    ready()
    client.after_upload = lambda: setattr(
        client, "queue", [row("replacement", START + 120)]
    )
    result = cycle()
    assert result.reason == "block intro boundary changed during upload"
    assert not client.commands
    assert not state().schedule_block_mentions


def test_prepared_intro_can_submit_below_generation_lead_time(scenario):
    client, calls, cycle, state, ready, _ = scenario
    cycle()
    ready()
    client.now = START + 100
    client.remaining = 20
    assert cycle().status == "published"
    assert len(calls) == 1


def test_too_close_to_boundary_waits_without_submitting(scenario):
    client, calls, cycle, state, ready, _ = scenario
    cycle()
    ready()
    client.now = START + 110
    client.remaining = 10
    cycle()
    assert not client.commands
    assert client.uploads == 0


def test_missing_queue_time_preserves_pending_and_reserves_normal_host(scenario):
    client, calls, cycle, state, _, _ = scenario
    cycle()
    pending = state().pending_block_intro
    client.queue[0].pop("played_at")
    result = cycle(normal=True)
    assert result.reason == "waiting for block intro queue timestamps"
    assert state().pending_block_intro == pending
    assert len(calls) == 1
    assert not client.commands


def test_delayed_queue_retargets_first_boundary_after_start(scenario):
    client, calls, cycle, state, _, _ = scenario
    cycle()
    client.queue[1]["played_at"] = START + 20
    cycle()
    assert calls[-1]["next_track"].title == "two"
    assert calls[-1]["current_track"].title == "one"
    assert state().pending_block_intro["target"]["title"] == "two"


def test_uncertain_submission_is_not_retried_on_next_cron(scenario):
    client, calls, cycle, state, ready, _ = scenario
    cycle()
    ready()

    def timeout(_station_id, command):
        client.commands.append(command)
        raise TimeoutError("Response lost after submission")

    client.send_telnet_command = timeout
    with pytest.raises(TimeoutError):
        cycle()
    assert state().pending_block_intro["submission_attempted"] is True
    assert cycle().reason == "block intro submission already attempted"
    assert len(client.commands) == 1


def test_queue_estimate_cannot_cause_insertion_before_block_start(scenario):
    client, calls, cycle, state, ready, _ = scenario
    cycle()
    ready()
    client.now = START - 120
    client.current["played_at"] = START - 180
    client.remaining = 60
    cycle()
    assert not client.commands
    assert client.uploads == 0
