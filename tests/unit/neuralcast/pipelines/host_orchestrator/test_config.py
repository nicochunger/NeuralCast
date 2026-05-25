"""Unit tests for host orchestrator configuration helpers."""

from __future__ import annotations

from neuralcast.pipelines.host_orchestrator import config
from neuralcast.pipelines.host_orchestrator.models import Archetype


def test_lead_time_seconds_for_archetype_uses_specific_and_default_values() -> None:
    assert config.lead_time_seconds_for_archetype(Archetype.DEEP_DIVE) >= 120
    assert config.lead_time_seconds_for_archetype(Archetype.BACK_SELL) > 0


def test_get_prompt_template_substitutes_template_variables(monkeypatch) -> None:
    monkeypatch.setattr(config, "load_prompt_templates", lambda: {"test": "Hello {name}"})

    assert config.get_prompt_template("test", name="NeuralCast") == "Hello NeuralCast"
