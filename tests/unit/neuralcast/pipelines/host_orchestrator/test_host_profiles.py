"""Combined host profiles, live reload, and durable profile transitions."""

import json
import random

import pytest

from neuralcast.pipelines.host_orchestrator import archetype_policies, channels
from neuralcast.pipelines.host_orchestrator.models import Archetype
from neuralcast.pipelines.host_orchestrator.state import (
    StationLock,
    default_state,
    load_state,
    reconcile_host_profile,
    save_state_atomic,
    should_speak_now,
)


def test_profile_inheritance_combines_cadence_and_archetypes(tmp_path):
    payload = json.loads(archetype_policies.ARCHETYPE_POLICY_CONFIG_PATH.read_text())
    payload["profiles"]["custom_station"] = {
        "extends": "relaxed",
        "cadence": {"wait_range_songs": [4, 8]},
        "archetype_overrides": {"news": {"enabled": False}},
    }
    path = tmp_path / "profiles.json"
    path.write_text(json.dumps(payload))
    registry = archetype_policies.load_archetype_policy_registry(path)
    custom = registry.profiles["custom_station"]
    assert custom.cadence.wait_range_songs == (4, 8)
    assert custom.cadence.speak_deadline_minutes == 120
    assert custom.cadence.cooldown_multiplier == 2
    assert Archetype.NEWS in custom.disabled_archetypes
    assert Archetype.DEEP_DIVE in custom.disabled_archetypes
    assert Archetype.NEWS not in registry.profiles["relaxed"].disabled_archetypes


@pytest.mark.parametrize(
    "cadence",
    [
        {"wait_range_songs": [0, 5]},
        {"wait_range_songs": [5, 2]},
        {"wait_range_songs": [2.5, 5]},
        {"speak_deadline_minutes": 0},
        {"cooldown_multiplier": -1},
        {"cooldown_multiplier": float("nan")},
        {"unexpected": 1},
    ],
)
def test_invalid_cadence_fails_fast(tmp_path, cadence):
    payload = json.loads(archetype_policies.ARCHETYPE_POLICY_CONFIG_PATH.read_text())
    payload["profiles"]["invalid"] = {"extends": "base", "cadence": cadence}
    path = tmp_path / "profiles.json"
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError):
        archetype_policies.load_archetype_policy_registry(path)


def test_profile_and_channel_changes_reload_in_same_process(tmp_path, monkeypatch):
    policy_path = tmp_path / "profiles.json"
    channel_path = tmp_path / "channels.json"
    policy_path.write_bytes(
        archetype_policies.ARCHETYPE_POLICY_CONFIG_PATH.read_bytes()
    )
    channel_path.write_bytes(channels.CHANNEL_CONFIG_PATH.read_bytes())
    monkeypatch.setattr(archetype_policies, "ARCHETYPE_POLICY_CONFIG_PATH", policy_path)
    monkeypatch.setattr(channels, "ARCHETYPE_POLICY_CONFIG_PATH", policy_path)
    monkeypatch.setattr(channels, "CHANNEL_CONFIG_PATH", channel_path)
    first = channels.resolve_host_channel(channel_key="neuralcast-es")
    assert first.host_profile == "relaxed"
    payload = json.loads(channel_path.read_text())
    payload["channels"]["neuralcast-es"]["host_profile"] = "frequent"
    channel_path.write_text(json.dumps(payload))
    second = channels.resolve_host_channel(channel_key="neuralcast-es")
    assert second.archetype_policy.cadence.wait_range_songs == (2, 5)
    assert not second.archetype_policy.disabled_archetypes
    policies = json.loads(policy_path.read_text())
    policies["profiles"]["frequent"]["cadence"] = {"wait_range_songs": [3, 6]}
    policy_path.write_text(json.dumps(policies))
    third = channels.resolve_host_channel(channel_key="neuralcast-es")
    assert third.archetype_policy.cadence.wait_range_songs == (3, 6)
    assert first.archetype_policy.cadence.wait_range_songs == (7, 12)
    policies["profiles"]["frequent"]["cadence"] = {"wait_range_songs": [6, 3]}
    policy_path.write_text(json.dumps(policies))
    with pytest.raises(ValueError):
        channels.resolve_host_channel(channel_key="neuralcast-es")


@pytest.mark.parametrize(
    "change",
    [
        {"host_profile": "missing"},
        {"cadence_profile": "neuralforge"},
        {"archetype_profile": "neuralforge"},
    ],
)
def test_channel_rejects_unknown_or_split_profile_selection(tmp_path, change):
    payload = json.loads(channels.CHANNEL_CONFIG_PATH.read_text())
    payload["channels"]["neuralcast-es"].update(change)
    path = tmp_path / "channels.json"
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError):
        channels.load_channel_registry(path)


def test_switch_reconciles_timing_once_and_preserves_history(tmp_path):
    registry = archetype_policies.get_archetype_policy_registry()
    relaxed, frequent = registry.profiles["relaxed"], registry.profiles["frequent"]
    state = default_state(1000, random.Random(1), relaxed.cadence)
    state.last_spoken_ts = 1000
    state.last_archetype_ts["short_story"] = 900
    state.songs_until_next_speak = 12
    state.songs_since_last_spoken = 3
    state.recent_scripts = ["Keep this history"]
    state.pending_block_intro = {"prepared": True}
    reconcile_host_profile(state, "relaxed", relaxed, 1100)
    assert state.next_speak_deadline_ts == 8200
    assert state.cooldown_until["short_story"] == 8100
    reconcile_host_profile(state, "frequent", frequent, 1200)
    assert state.songs_until_next_speak == 5
    assert state.next_speak_deadline_ts == 3700
    assert state.cooldown_until["short_story"] == 4500
    assert state.songs_since_last_spoken == 3
    assert state.recent_scripts == ["Keep this history"]
    assert state.pending_block_intro == {"prepared": True}
    path = tmp_path / "state.json"
    save_state_atomic(path, state)
    restored = load_state(path, 1250, random.Random(5), frequent.cadence)
    reconcile_host_profile(restored, "frequent", frequent, 1300)
    assert restored.to_dict() == state.to_dict()
    reconcile_host_profile(restored, "relaxed", relaxed, 1400)
    assert restored.songs_until_next_speak == 7
    assert restored.next_speak_deadline_ts == 8200
    assert restored.cooldown_until["short_story"] == 8100


def test_legacy_state_adopts_profile_without_losing_unknown_cooldowns():
    policy = archetype_policies.get_archetype_policy_registry().profiles["relaxed"]
    state = default_state(1000, random.Random(1))
    state.last_spoken_ts = 500
    state.cooldown_until["news"] = 15000
    reconcile_host_profile(state, "relaxed", policy, 1100)
    assert state.cooldown_until["news"] == 15000
    assert state.next_speak_deadline_ts == 7700
    assert state.songs_until_next_speak == 7
    assert not should_speak_now(state, "track", 1200)[0]


def test_live_lock_does_not_expire_while_cycle_runs(tmp_path):
    path = tmp_path / "host.lock"
    first = StationLock(path, stale_seconds=0)
    second = StationLock(path, stale_seconds=0)
    assert first.acquire()
    try:
        assert not second.acquire()
    finally:
        first.release()
    assert second.acquire()
    second.release()
