"""Unit tests for multilingual host-channel configuration."""

from __future__ import annotations

from neuralcast.pipelines.host_orchestrator.channels import (
    get_channel_registry,
    resolve_host_channel,
)
from neuralcast.pipelines.host_orchestrator.main import (
    HostCycleRequest,
    _args_from_cycle_request,
)
from neuralcast.pipelines.host_orchestrator.utils import station_state_paths


def test_english_channel_reuses_neuralcast_content_and_media_root() -> None:
    channel = resolve_host_channel(channel_key="neuralcast-en")

    assert channel.azuracast_station == "neuralcast_shared_media_test"
    assert channel.azuracast_station_id == 3
    assert channel.content_station == "neuralcast"
    assert channel.locale.tag == "en"
    assert channel.liquidsoap_media_root == (
        "/var/azuracast/stations/neuralcast/media"
    )
    assert channel.remote_prefix == "AI Stories/neuralcast/en"


def test_legacy_station_resolution_preserves_spanish_channels() -> None:
    assert resolve_host_channel(station_slug="neuralcast").key == "neuralcast-es"
    assert resolve_host_channel(station_slug="neuralforge").key == "neuralforge-es"


def test_channel_request_overrides_legacy_station_default() -> None:
    args = _args_from_cycle_request(
        HostCycleRequest(
            station="neuralforge",
            channel="neuralcast-en",
            base_url="https://azuracast.local",
        )
    )

    assert args.channel == "neuralcast-en"
    assert args.station == "neuralcast_shared_media_test"
    assert args.content_station == "neuralcast"
    assert args.brand_station == "neuralcast"


def test_variant_channel_has_isolated_state_paths() -> None:
    station_dir, state_path, lock_path = station_state_paths("neuralcast-en")

    assert station_dir.name == "NeuralCast"
    assert state_path.parent.parts[-3:] == (
        "metadata",
        "host_channels",
        "neuralcast-en",
    )
    assert lock_path.parent == state_path.parent


def test_all_configured_channels_reference_loaded_brand_and_locale() -> None:
    registry = get_channel_registry()

    assert set(registry.channels) == {
        "neuralcast-es",
        "neuralcast-en",
        "neuralforge-es",
    }
    for channel in registry.channels.values():
        assert channel.brand.key in registry.brands
        assert channel.locale.tag in registry.locales
        assert channel.brand.script_style
        assert channel.brand.tts_style
