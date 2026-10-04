"""Unit tests for validated and inheritable host archetype policies."""

from __future__ import annotations

import json

import pytest

from neuralcast.pipelines.host_orchestrator.archetype_policies import (
    ARCHETYPE_POLICY_CONFIG_PATH,
    load_archetype_policy_registry,
)
from neuralcast.pipelines.host_orchestrator.models import Archetype


def test_profile_inheritance_preserves_station_archetype_disables() -> None:
    registry = load_archetype_policy_registry()
    neuralcast = registry.profiles["neuralcast"]

    assert Archetype.DEEP_DIVE in neuralcast.disabled_archetypes
    assert Archetype.CONCERT_CHECK in neuralcast.disabled_archetypes
    assert Archetype.NEWS not in neuralcast.disabled_archetypes


def test_list_overrides_are_validated_and_do_not_mutate_parent() -> None:
    registry = load_archetype_policy_registry()
    parent = registry.profiles["neuralforge"]
    resolved = registry.resolve(
        "neuralforge",
        {
            "news": {
                "news": {
                    "topics": {"remove": ["argentina_politics_general"]}
                }
            },
            "concert_check": {
                "concert_check": {"countries": {"replace": ["CH"]}}
            },
        },
        resolved_name="test-channel",
    )

    parent_news = parent.for_archetype(Archetype.NEWS).news
    resolved_news = resolved.for_archetype(Archetype.NEWS).news
    resolved_concert = resolved.for_archetype(
        Archetype.CONCERT_CHECK
    ).concert_check
    assert parent_news is not None
    assert resolved_news is not None
    assert resolved_concert is not None
    assert "argentina_politics_general" in parent_news.topic_ids
    assert "argentina_politics_general" not in resolved_news.topic_ids
    assert resolved_concert.country_codes == ("CH",)


def test_unknown_override_identifier_fails_fast(tmp_path) -> None:
    payload = json.loads(ARCHETYPE_POLICY_CONFIG_PATH.read_text(encoding="utf-8"))
    payload["profiles"]["neuralforge"]["archetype_overrides"] = {
        "news": {"news": {"topics": {"remove": ["unknown_topic"]}}}
    }
    path = tmp_path / "archetype_profiles.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="unknown values: unknown_topic"):
        load_archetype_policy_registry(path)
