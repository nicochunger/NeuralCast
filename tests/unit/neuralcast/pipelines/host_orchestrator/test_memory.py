"""Recent host memory selection and persistence, with provider calls mocked."""

import random
from unittest.mock import Mock

import pytest

from neuralcast.pipelines.host_orchestrator import memory
from neuralcast.pipelines.host_orchestrator.models import Archetype
from neuralcast.pipelines.host_orchestrator.state import (
    apply_success_state_update,
    default_state,
    load_state,
    migrate_state,
    save_state_atomic,
)
from neuralcast.services.typesafe import DecisionUnavailable

NOW = 1_800_000_000.0


def entry(identity="a", age=60, **kwargs):
    return {
        "id": identity,
        "script": f"Earlier observation {identity}",
        "expected_play_at": NOW - age,
        **kwargs,
    }


def answer(choice="yes", probability=0.9):
    return {
        "type": "choice",
        "choice": choice,
        "probabilities": {"yes": probability, "no": 1 - probability},
    }


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    provider = Mock()
    monkeypatch.setattr(memory.typesafe, "evaluate_questions", provider)
    return provider


def test_filters_old_future_and_used_memories_and_batches(configured):
    state = default_state(NOW, random.Random(1))
    state.broadcast_memory = [
        entry("old", 7201),
        entry("future", -30),
        entry("used", 500),
        entry("a", 300),
        entry("b", 200),
        entry("c", 100, callback_source_id="used"),
    ]
    configured.return_value = {
        "answers": {
            "callback_0": answer("no", 0.1),
            "callback_1": answer("yes", 0.8),
            "callback_2": answer("yes", 0.95),
        }
    }
    selected = memory.select_callback(
        state,
        {"draft": "An intimate recording."},
        now=NOW,
        archetype=Archetype.SHORT_STORY,
    )
    assert selected["id"] == "a"
    configured.assert_called_once()
    request, questions = configured.call_args.args
    assert [item["id"] for item in request["memories"]] == ["c", "b", "a"]
    assert len(questions) == 3
    assert "memories[2].script" in questions["callback_2"]["instructions"]
    assert len(state.broadcast_memory) == 6  # Selection is read-only.


def test_all_no_and_provider_failure_continue_without_callback(configured):
    state = default_state(NOW, random.Random(1))
    state.broadcast_memory = [entry()]
    configured.return_value = {"answers": {"callback_0": answer("no", 0.1)}}
    assert (
        memory.select_callback(state, {}, now=NOW, archetype=Archetype.BACK_SELL)
        is None
    )
    configured.side_effect = DecisionUnavailable("timeout")
    assert (
        memory.select_callback(state, {}, now=NOW, archetype=Archetype.BACK_SELL)
        is None
    )


@pytest.mark.parametrize(
    "bad_answer",
    [
        {},
        {"type": "choice", "choice": "maybe"},
        {"type": "choice", "choice": []},
        answer("yes", float("nan")),
        answer("yes", 2),
        {"type": "choice", "choice": "yes", "probabilities": {"yes": 0.9}},
    ],
)
def test_invalid_answers_are_not_used(configured, bad_answer):
    state = default_state(NOW, random.Random(1))
    state.broadcast_memory = [entry()]
    configured.return_value = {"answers": {"callback_0": bad_answer}}
    assert (
        memory.select_callback(state, {}, now=NOW, archetype=Archetype.BACK_SELL)
        is None
    )


@pytest.mark.parametrize(
    "archetype",
    [
        Archetype.NEWS,
        Archetype.CONCERT_CHECK,
        Archetype.BLOCK_INTRO,
        Archetype.ULTRA_MINIMAL,
    ],
)
def test_ineligible_archetypes_do_not_call_provider(configured, archetype):
    state = default_state(NOW, random.Random(1))
    state.broadcast_memory = [entry()]
    assert memory.select_callback(state, {}, now=NOW, archetype=archetype) is None
    configured.assert_not_called()


def test_missing_key_and_empty_history_skip_provider(configured, monkeypatch):
    state = default_state(NOW, random.Random(1))
    assert (
        memory.select_callback(state, {}, now=NOW, archetype=Archetype.BACK_SELL)
        is None
    )
    state.broadcast_memory = [entry()]
    monkeypatch.delenv("TYPESAFE_API_KEY")
    assert (
        memory.select_callback(state, {}, now=NOW, archetype=Archetype.BACK_SELL)
        is None
    )
    configured.assert_not_called()


def test_candidate_limit(configured):
    state = default_state(NOW, random.Random(1))
    state.broadcast_memory = [entry(str(i), i * 60) for i in range(20)]
    configured.return_value = {
        "answers": {f"callback_{i}": answer("no", 0.1) for i in range(6)}
    }
    memory.select_callback(state, {}, now=NOW, archetype=Archetype.BACK_SELL)
    assert len(configured.call_args.args[1]) == 6


def test_journal_migration_prunes_invalid_and_expired_entries():
    raw = [
        entry(),
        entry("expired", 8 * 86400),
        {},
        None,
        entry("nan", float("nan")),
        entry("bad", callback_source_id=[]),
    ]
    journal = memory.normalize_journal(raw, NOW)
    assert {item["id"] for item in journal} == {"a", "bad"}
    assert journal[1]["callback_source_id"] is None
    old = migrate_state(
        {"recent_scripts": ["legacy without timestamp"]}, NOW, random.Random(1)
    )
    assert old.broadcast_memory == []
    assert old.recent_scripts == ["legacy without timestamp"]


def test_queued_segment_round_trips_existing_atomic_state(tmp_path):
    state = default_state(NOW, random.Random(1))
    apply_success_state_update(
        state,
        NOW,
        "artist|title",
        120,
        Archetype.BACK_SELL,
        "",
        None,
        None,
        "A new observation.",
        None,
        random.Random(1),
        memory_media_id="42",
        callback_source_id="earlier",
    )
    path = tmp_path / "state.json"
    save_state_atomic(path, state)
    loaded = load_state(path, NOW + 30, random.Random(1))
    assert loaded.broadcast_memory == state.broadcast_memory
    record = loaded.broadcast_memory[0]
    assert record["expected_play_at"] == NOW + 120
    assert record["media_id"] == "42"
    assert record["callback_source_id"] == "earlier"
    assert record["script"] == "A new observation."


@pytest.mark.parametrize(
    "locale, phrase",
    [("en", "new listener"), ("fr-CH", "vient d'arriver"), ("es-AR", "recién llegue")],
)
def test_callback_prompt_preserves_language_and_age(locale, phrase):
    prompt = memory.callback_prompt(entry(age=600), NOW, locale, "A researched draft.")
    assert phrase in prompt
    assert '"minutes_ago": 10' in prompt
    assert "Earlier observation a" in prompt


@pytest.mark.parametrize(
    "edited",
    [
        "As we heard earlier, the room matters. First sentence. Second sentence.",
        "First sentence. As we heard earlier, the room matters. Second sentence.",
        "First sentence. Second sentence. As we heard earlier, the room matters.",
    ],
)
def test_callback_accepts_one_short_insertion(edited):
    assert (
        memory.apply_callback_edit("First sentence. Second sentence.", edited) == edited
    )


@pytest.mark.parametrize(
    "edited",
    [
        "Changed sentence. Second sentence. Earlier, the room mattered.",
        "First Earlier sentence. Second sentence.",
        "Earlier. First sentence. More. Second sentence.",
        "First sentence. Second sentence. " + "word " * 40,
        "First sentence. Second sentence. <unknown>Earlier.</unknown>",
        "First sentence. Second sentence. https://example.com",
        "First sentence. Second sentence. [laughs] Earlier.",
    ],
)
def test_callback_rejects_rewrites_multiple_insertions_and_invalid_speech(edited):
    with pytest.raises(ValueError):
        memory.apply_callback_edit("First sentence. Second sentence.", edited)


@pytest.mark.parametrize("edited", ["NO_CALLBACK", "NO_SCRIPT", "First sentence."])
def test_callback_can_be_omitted(edited):
    assert memory.apply_callback_edit("First sentence.", edited) is None


def test_callback_preserves_vocal_tags_and_protected_titles():
    draft = "<chuckle> That was [Song]. Here comes the next track."
    edited = draft + " Like earlier, its restraint speaks volumes."
    assert (
        memory.apply_callback_edit(draft, edited, protected_texts=("[Song]",)) == edited
    )


def test_callback_budget_scales_with_draft_and_is_capped():
    assert memory.callback_word_budget("Brief draft.") == 12
    assert memory.callback_word_budget("word " * 100) == 15
    assert memory.callback_word_budget("word " * 400) == 35
