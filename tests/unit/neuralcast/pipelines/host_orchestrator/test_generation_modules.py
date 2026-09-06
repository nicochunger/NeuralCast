"""Boundary tests for the decomposed host-generation modules."""

from __future__ import annotations

import datetime as dt
import re
from types import SimpleNamespace

import pytest

from neuralcast.pipelines.host_orchestrator import (
    concert_generation,
    generation,
    news_generation,
    prompts,
    script_processing,
    structured_output,
    text_generation,
)
from neuralcast.pipelines.host_orchestrator.models import (
    OrchestratorState,
    QueueTrack,
)


_PATTERN = re.compile(
    r"SCRIPT:\s*(?P<script>.*?)\s*META\s*\(JSON\):\s*(?P<meta>\{.*\})$",
    re.DOTALL,
)


def _state(*, recent_news_dedup: list[dict] | None = None) -> OrchestratorState:
    return OrchestratorState(
        state_version=2,
        last_seen_track_key=None,
        last_seen_ts=None,
        songs_since_last_spoken=0,
        songs_until_next_speak=0,
        next_speak_deadline_ts=0,
        last_spoken_track_key=None,
        last_spoken_ts=None,
        last_spoken_expected_end_ts=None,
        cooldown_until={},
        recent_archetypes=[],
        recent_hooks=[],
        last_angle_by_archetype={},
        recent_news_dedup=recent_news_dedup or [],
        recent_scripts=[],
        schedule_block_mentions={},
    )


def test_generation_facade_reexports_owned_functions() -> None:
    assert generation.build_prompt is prompts.build_prompt
    assert generation.gemini_generate_text is text_generation.gemini_generate_text
    assert (
        generation.ensure_schedule_genre_reference
        is script_processing.ensure_schedule_genre_reference
    )
    assert generation.parse_news_output is news_generation.parse_news_output
    assert generation.parse_concert_output is concert_generation.parse_concert_output
    assert (
        generation.parse_structured_script_and_meta
        is structured_output.parse_structured_script_and_meta
    )


@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        ("NO_SCRIPT", "NO_SCRIPT"),
        ("not structured", "invalid format"),
        ("SCRIPT: hello META (JSON): {nope}", "invalid json"),
        ("SCRIPT:   META (JSON): {}", "script is empty"),
    ],
)
def test_structured_output_rejects_invalid_contracts(raw: str, reason: str) -> None:
    _script, _meta, actual_reason = structured_output.parse_structured_script_and_meta(
        raw, _PATTERN
    )

    assert actual_reason == reason


def test_structured_output_accepts_fenced_json_and_normalizes_timestamps() -> None:
    fenced_pattern = re.compile(
        r"SCRIPT:\s*(?P<script>.*?)\s*META\s*\(JSON\):\s*(?P<meta>.*)$",
        re.DOTALL,
    )
    script, meta, reason = structured_output.parse_structured_script_and_meta(
        'SCRIPT: Hello   world\nMETA (JSON): ```json\n{"value": 1}\n```',
        fenced_pattern,
    )

    assert (script, meta, reason) == ("Hello world", {"value": 1}, "ok")
    assert structured_output.parse_timestamp("2026-09-01T12:00:00Z") == dt.datetime(
        2026, 9, 1, 12, tzinfo=dt.timezone.utc
    )
    assert structured_output.parse_timestamp("Tue, 01 Sep 2026 12:00:00 GMT") == dt.datetime(
        2026, 9, 1, 12, tzinfo=dt.timezone.utc
    )
    assert structured_output.parse_timestamp("not a timestamp") is None


def test_news_output_and_freshness_validation_cover_valid_duplicate_and_stale_paths() -> None:
    raw = (
        "SCRIPT: Headlines now. META (JSON): "
        '{"story_count": 1, "language": "es-AR", "stories": ['
        '{"topic": "Science", "headline": "Discovery", '
        '"source_url": "https://example.test/story", '
        '"published_at": "2026-09-01T11:00:00Z"}]}'
    )
    segment, reason = news_generation.parse_news_output(raw)

    assert reason == "ok"
    assert segment is not None
    assert news_generation.validate_news_freshness_and_dedup(
        segment, _state(), dt.datetime(2026, 9, 1, 12, tzinfo=dt.timezone.utc).timestamp()
    ) == (True, "ok")
    duplicate_state = _state(
        recent_news_dedup=[
            {
                "key": news_generation.build_news_dedup_key(
                    "Science", "Discovery", "https://example.test/story"
                ),
                "ts": dt.datetime(2026, 9, 1, 11, tzinfo=dt.timezone.utc).timestamp(),
            }
        ]
    )
    assert news_generation.validate_news_freshness_and_dedup(
        segment, duplicate_state, dt.datetime(2026, 9, 1, 12, tzinfo=dt.timezone.utc).timestamp()
    )[0] is False
    segment.stories[0].published_at = "2020-01-01T00:00:00Z"
    assert news_generation.validate_news_freshness_and_dedup(
        segment, _state(), dt.datetime(2026, 9, 1, 12, tzinfo=dt.timezone.utc).timestamp()
    )[0] is False


def test_concert_output_and_validation_accepts_future_target_artist() -> None:
    raw = (
        "SCRIPT: Próximo show. META (JSON): "
        '{"language": "es-AR", "events": [{"artist": "Ghost", '
        '"country": "Argentina", "city": "Buenos Aires", "venue": "Arena", '
        '"event_date": "2099-01-02", "source_url": "https://example.test/event"}]}'
    )
    segment, reason = concert_generation.parse_concert_output(raw)

    assert reason == "ok"
    assert segment is not None
    assert concert_generation.validate_concert_segment(
        segment,
        QueueTrack("1", None, "Ghost", "Rats", 200),
        QueueTrack("2", None, "Opeth", "Harvest", 300),
    ) == (True, "ok")
    segment.events[0].source_url = "not-a-url"
    assert concert_generation.validate_concert_segment(
        segment,
        QueueTrack("1", None, "Ghost", "Rats", 200),
        QueueTrack("2", None, "Opeth", "Harvest", 300),
    )[0] is False


def test_channel_scoped_news_and_concert_validation_rejects_out_of_scope_facts() -> None:
    news_raw = (
        "SCRIPT: Une information. META (JSON): "
        '{"story_count": 1, "language": "fr-CH", "stories": ['
        '{"topic_id": "argentina_politics_general", "topic": "Argentine", '
        '"headline": "Actualité", "source_url": "https://example.test/news", '
        '"published_at": "2026-09-01T11:00:00Z"}]}'
    )
    news_segment, reason = news_generation.parse_news_output(news_raw, "fr-CH")
    assert reason == "ok"
    assert news_segment is not None
    assert news_generation.validate_news_freshness_and_dedup(
        news_segment,
        _state(),
        dt.datetime(2026, 9, 1, 12, tzinfo=dt.timezone.utc).timestamp(),
        allowed_topic_ids=("science", "switzerland_general"),
    )[0] is False

    concert_raw = (
        "SCRIPT: Un concert. META (JSON): "
        '{"language": "fr-CH", "events": [{"artist": "Ghost", '
        '"country_code": "AR", "country": "Argentine", "city": "Buenos Aires", '
        '"venue": "Arena", "event_date": "2099-01-02", '
        '"source_url": "https://example.test/event"}]}'
    )
    concert_segment, reason = concert_generation.parse_concert_output(
        concert_raw, "fr-CH"
    )
    assert reason == "ok"
    assert concert_segment is not None
    assert concert_generation.validate_concert_segment(
        concert_segment,
        QueueTrack("1", None, "Ghost", "Rats", 200),
        QueueTrack("2", None, "Opeth", "Harvest", 300),
        allowed_country_codes=("CH",),
    )[0] is False


def test_gemini_text_generation_handles_text_empty_and_grounded_no_script(monkeypatch) -> None:
    calls: list[dict] = []

    class Client:
        models = SimpleNamespace(
            generate_content=lambda **kwargs: calls.append(kwargs)
            or SimpleNamespace(text="Generated script")
        )

    monkeypatch.setattr(text_generation, "get_gemini_client", lambda: Client())
    assert text_generation.gemini_generate_text("prompt", "system", 0.3, 0.8, False) == "Generated script"
    assert calls[0]["contents"] == "prompt"

    class EmptyClient:
        models = SimpleNamespace(
            generate_content=lambda **_kwargs: SimpleNamespace(text=" ")
        )

    monkeypatch.setattr(text_generation, "get_gemini_client", lambda: EmptyClient())
    with pytest.raises(RuntimeError, match="empty text"):
        text_generation.gemini_generate_text("prompt", "system", 0.3, 0.8, False)

    grounded = SimpleNamespace(
        text="NO_SCRIPT",
        response_id="response-1",
        candidates=[SimpleNamespace(finish_reason="STOP", grounding_metadata=None)],
        usage_metadata=SimpleNamespace(total_token_count=4),
    )
    monkeypatch.setattr(
        text_generation,
        "get_gemini_client",
        lambda: SimpleNamespace(models=SimpleNamespace(generate_content=lambda **_kwargs: grounded)),
    )
    assert text_generation.gemini_generate_text("prompt", "system", 0.3, 0.8, True) == "NO_SCRIPT"
