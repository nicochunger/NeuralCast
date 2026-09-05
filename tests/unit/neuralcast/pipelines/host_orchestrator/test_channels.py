"""Unit tests for multilingual host-channel configuration."""

from __future__ import annotations

from neuralcast.pipelines.host_orchestrator.channels import (
    get_channel_registry,
    resolve_host_channel,
)
from neuralcast.pipelines.host_orchestrator.config import (
    archetype_settings_for_station,
    cadence_settings_for_station,
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
    assert channel.cadence_profile == "neuralforge"
    assert channel.archetype_profile == "neuralforge"


def test_english_channel_uses_neuralforge_runtime_policies() -> None:
    channel = resolve_host_channel(channel_key="neuralcast-en")

    assert cadence_settings_for_station(
        channel.cadence_profile
    ) == cadence_settings_for_station("neuralforge")
    assert archetype_settings_for_station(
        channel.archetype_profile
    ) == archetype_settings_for_station("neuralforge")


def test_french_channel_reuses_neuralforge_content_and_media_root() -> None:
    channel = resolve_host_channel(channel_key="neuralforge-fr")

    assert channel.azuracast_station == "neuralforge_fr"
    assert channel.azuracast_station_id == 4
    assert channel.content_station == "neuralforge"
    assert channel.locale.tag == "fr-CH"
    assert channel.locale.prompt_directory.name == "fr-CH"
    assert channel.liquidsoap_media_root == (
        "/var/azuracast/stations/neuralforge/media"
    )
    assert channel.remote_prefix == "AI Stories/neuralforge/fr-CH"
    assert channel.script_style_override is not None
    assert channel.tts_instructions_override_path is not None


def test_legacy_station_resolution_preserves_spanish_channels() -> None:
    assert resolve_host_channel(station_slug="neuralcast").key == "neuralcast-es"
    assert resolve_host_channel(station_slug="neuralforge").key == "neuralforge-es"


def test_neuralforge_spanish_has_channel_specific_tts_instructions() -> None:
    neuralforge = resolve_host_channel(channel_key="neuralforge-es")
    neuralcast = resolve_host_channel(channel_key="neuralcast-es")

    assert neuralforge.tts_instructions_override_path is not None
    assert neuralforge.tts_instructions_override_path.name == (
        "neuralforge_tts_instructions.md"
    )
    assert neuralcast.tts_instructions_override_path is None


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
    assert args.cadence_profile == "neuralforge"
    assert args.archetype_profile == "neuralforge"


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
        "neuralforge-fr",
    }
    for channel in registry.channels.values():
        assert channel.brand.key in registry.brands
        assert channel.locale.tag in registry.locales
        assert channel.brand.script_style
        assert channel.brand.tts_style
