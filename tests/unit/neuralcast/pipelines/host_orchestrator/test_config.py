"""Unit tests for host orchestrator configuration helpers."""

from __future__ import annotations

from neuralcast.pipelines.host_orchestrator import config
from neuralcast.pipelines.host_orchestrator.models import Archetype


def test_lead_time_seconds_for_archetype_uses_specific_and_default_values() -> None:
    assert config.lead_time_seconds_for_archetype(Archetype.DEEP_DIVE) >= 120
    assert config.lead_time_seconds_for_archetype(Archetype.BACK_SELL) > 0


def test_cadence_settings_for_station_uses_default_for_neuralforge() -> None:
    settings = config.cadence_settings_for_station("neuralforge")

    assert settings.wait_range_songs == (2, 5)
    assert settings.speak_deadline_minutes == 45
    assert settings.cooldown_multiplier == 1.0


def test_cadence_settings_for_station_slows_neuralcast_down() -> None:
    settings = config.cadence_settings_for_station("neuralcast")

    assert settings.wait_range_songs == (5, 10)
    assert settings.speak_deadline_minutes == 90
    assert settings.cooldown_multiplier == 2.0
    assert (
        config.cooldown_seconds_for_archetype(Archetype.SHORT_STORY, settings)
        == config.COOLDOWN_SECONDS[Archetype.SHORT_STORY] * 2
    )


def test_archetype_settings_for_station_disable_neuralcast_specific_archetypes() -> None:
    settings = config.archetype_settings_for_station("neuralcast")

    assert settings.disabled_archetypes == frozenset(
        {
            Archetype.DEEP_DIVE,
            Archetype.ERA_SNAPSHOT,
            Archetype.CONCERT_CHECK,
        }
    )
    assert config.archetype_settings_for_station("neuralforge").disabled_archetypes == frozenset()


def test_get_prompt_template_substitutes_template_variables(monkeypatch) -> None:
    monkeypatch.setattr(config, "load_prompt_templates", lambda: {"test": "Hello {name}"})

    assert config.get_prompt_template("test", name="NeuralCast") == "Hello NeuralCast"
