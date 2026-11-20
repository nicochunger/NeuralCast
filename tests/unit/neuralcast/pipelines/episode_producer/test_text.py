"""Gemini editorial requests are direct, bounded, schema-constrained and tool-free."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from neuralcast.services import editorial_text as gemini


def response(
    text='{"strengths":[],"findings":[],"additional_sources":[]}', reason="STOP"
):
    return SimpleNamespace(
        text=text,
        candidates=[SimpleNamespace(finish_reason=reason)],
        usage_metadata=SimpleNamespace(
            model_dump=lambda **kwargs: {
                "prompt_token_count": 120,
                "candidates_token_count": 30,
                "thoughts_token_count": 15,
                "private": "not metrics",
            }
        ),
    )


def test_gemini_request_is_direct_bounded_and_keeps_usage(tmp_path, monkeypatch):
    client = Mock()
    client.models.generate_content.return_value = response()
    monkeypatch.setattr(gemini, "get_gemini_client", lambda: client)
    attempt = tmp_path / "attempt"
    schema = {"type": "object", "properties": {"findings": {"type": "array"}}}
    result = gemini.run_stage(
        "Assess the voice",
        {"source_plan": {"segments": []}},
        schema,
        attempt,
        thinking_level="low",
        max_output_tokens=8192,
        timeout_seconds=300,
    )
    kwargs = client.models.generate_content.call_args.kwargs
    assert kwargs["model"] == "gemini-4-neon"
    assert json.loads(kwargs["contents"]) == {"source_plan": {"segments": []}}
    config = kwargs["config"]
    assert config.system_instruction == "Assess the voice"
    assert config.response_mime_type == "application/json"
    assert config.response_json_schema == schema
    assert not config.tools
    assert config.automatic_function_calling.disable
    assert config.max_output_tokens == 8192
    assert config.http_options.timeout == 300000
    assert config.http_options.retry_options.attempts == 1
    assert str(config.thinking_config.thinking_level).endswith("LOW")
    assert result["findings"] == []
    metrics = json.loads((attempt / "metrics.json").read_text())
    assert metrics["usage"]["thoughts_token_count"] == 15
    assert "private" not in metrics["usage"]
    assert metrics["elapsed_seconds"] >= 0
    assert (attempt / "context.json").exists() and (attempt / "response.json").exists()


@pytest.mark.parametrize("reason", ["MAX_TOKENS", "SAFETY", "MISSING"])
def test_partial_or_blocked_response_is_not_accepted(tmp_path, monkeypatch, reason):
    client = Mock()
    client.models.generate_content.return_value = response(reason=reason)
    monkeypatch.setattr(gemini, "get_gemini_client", lambda: client)
    with pytest.raises(RuntimeError, match="did not finish"):
        gemini.run_stage("Edit", {}, {}, tmp_path / "attempt")
    assert (
        json.loads((tmp_path / "attempt" / "failure.json").read_text())["category"]
        == "incomplete"
    )
    assert (tmp_path / "attempt" / "metrics.json").exists()


@pytest.mark.parametrize("body", ["not JSON", "[]"])
def test_invalid_response_is_available_for_targeted_repair(tmp_path, monkeypatch, body):
    client = Mock()
    client.models.generate_content.return_value = response(text=body)
    monkeypatch.setattr(gemini, "get_gemini_client", lambda: client)
    with pytest.raises(ValueError):
        gemini.run_stage("Edit", {}, {}, tmp_path / "attempt")
    assert (tmp_path / "attempt" / "response.json").read_text() == body


def test_provider_error_does_not_expose_credentials(tmp_path, monkeypatch):
    class ProviderError(Exception):
        code = 401

    client = Mock()
    client.models.generate_content.side_effect = ProviderError(
        "Bearer private-api-key; secret provider body"
    )
    monkeypatch.setattr(gemini, "get_gemini_client", lambda: client)
    with pytest.raises(RuntimeError, match="configuration") as error:
        gemini.run_stage("Review", {}, {}, tmp_path / "attempt")
    saved = "".join(path.read_text() for path in (tmp_path / "attempt").iterdir())
    assert "private-api-key" not in saved + str(error.value)
    assert "secret provider" not in saved + str(error.value)
    assert (
        json.loads((tmp_path / "attempt" / "metrics.json").read_text())["usage"] is None
    )


def test_edit_schema_avoids_nested_array_grammar_limits_but_preserves_local_bounds(
    tmp_path, monkeypatch
):
    from neuralcast.pipelines.episode_producer.editorial_models import Edit
    from neuralcast.services.editorial_agent import output_schema

    schema = output_schema(Edit)
    client = Mock()
    client.models.generate_content.return_value = response(
        text='{"passages":[],"resolutions":[],"open_questions":[],"production_notes":[],"listening_notes":[]}'
    )
    monkeypatch.setattr(gemini, "get_gemini_client", lambda: client)
    result = gemini.run_stage("Edit", {}, schema, tmp_path / "attempt")
    sent = client.models.generate_content.call_args.kwargs[
        "config"
    ].response_json_schema

    def check(value):
        if isinstance(value, dict):
            assert "maxItems" not in value and "minItems" not in value
            for item in value.values():
                check(item)
        elif isinstance(value, list):
            for item in value:
                check(item)

    check(sent)
    assert sent["required"] == schema["required"]
    assert sent["$defs"]["Resolution"]["properties"]["status"]["enum"] == [
        "resolved",
        "dismissed",
        "unresolved",
    ]
    assert schema["properties"]["passages"]["minItems"] == 1
    assert schema["properties"]["passages"]["maxItems"] == 40
    assert json.loads((tmp_path / "attempt" / "schema.json").read_text()) == sent
    assert (
        json.loads((tmp_path / "attempt" / "validation_schema.json").read_text())
        == schema
    )
    with pytest.raises(ValueError, match="at least 1"):
        Edit.model_validate(result)


def test_research_enables_search_url_context_and_saves_grounding(tmp_path, monkeypatch):
    client = Mock()
    result = response()
    result.candidates[0].grounding_metadata = SimpleNamespace(
        model_dump=lambda **kwargs: {"web_search_queries": ["band interview"]}
    )
    client.models.generate_content.return_value = result
    monkeypatch.setattr(gemini, "get_gemini_client", lambda: client)
    gemini.run_stage(
        "Research", {}, {}, tmp_path / "attempt", search=True, thinking_level="high"
    )
    config = client.models.generate_content.call_args.kwargs["config"]
    assert config.tools[0].google_search is not None
    assert config.tools[1].url_context is not None
    assert config.response_mime_type == "application/json"
    assert json.loads((tmp_path / "attempt" / "grounding.json").read_text())[
        "candidates"
    ][0]["grounding_metadata"]["web_search_queries"] == ["band interview"]


def test_editorial_jobs_reserve_full_neon_output_headroom(tmp_path, monkeypatch):
    from neuralcast.pipelines.episode_producer.editorial import job_settings

    client = Mock()
    client.models.generate_content.return_value = response()
    monkeypatch.setattr(gemini, "get_gemini_client", lambda: client)
    for pipeline in ("hybrid", "gemini"):
        for name, settings in job_settings(pipeline).items():
            if settings["provider"] != "gemini":
                continue
            gemini.run_stage(
                name,
                {},
                {},
                tmp_path / f"{pipeline}-{name}",
                **{k: v for k, v in settings.items() if k not in {"provider", "model"}},
            )
            assert (
                client.models.generate_content.call_args.kwargs[
                    "config"
                ].max_output_tokens
                == 65536
            )


def test_transient_errors_retry_with_backoff_and_succeed(tmp_path, monkeypatch):
    class RateLimitError(Exception):
        code = 429

    sleeps = []
    monkeypatch.setattr(gemini, "sleep", lambda seconds: sleeps.append(seconds))
    client = Mock()
    client.models.generate_content.side_effect = [
        RateLimitError("429"),
        RateLimitError("429"),
        response(),
    ]
    monkeypatch.setattr(gemini, "get_gemini_client", lambda: client)
    attempt = tmp_path / "attempt"
    result = gemini.run_stage("Review", {}, {}, attempt)
    assert result["findings"] == []
    assert client.models.generate_content.call_count == 3
    assert len(sleeps) == 2 and sleeps[1] > sleeps[0]
    metrics = json.loads((attempt / "metrics.json").read_text())
    assert metrics["transport_attempts"] == 3


def test_400_configuration_error_records_sanitized_detail_without_secrets(
    tmp_path, monkeypatch
):
    class BadRequestError(Exception):
        code = 400

    client = Mock()
    client.models.generate_content.side_effect = BadRequestError(
        "Invalid schema at field foo; Bearer secret-token-123"
    )
    monkeypatch.setattr(gemini, "get_gemini_client", lambda: client)
    attempt = tmp_path / "attempt"
    with pytest.raises(RuntimeError, match="configuration"):
        gemini.run_stage("Edit", {}, {}, attempt)
    failure = json.loads((attempt / "failure.json").read_text())
    assert failure["http_status"] == 400
    assert "Invalid schema at field foo" in failure["detail"]
    assert "secret-token-123" not in failure["detail"]


def test_writing_guide_is_sent_as_leading_unescaped_markdown_part(
    tmp_path, monkeypatch
):
    client = Mock()
    client.models.generate_content.return_value = response()
    monkeypatch.setattr(gemini, "get_gemini_client", lambda: client)
    gemini.run_stage(
        "Edit",
        {
            "writing_references": {
                "writing_guide.md": "# Guide\nLine 1\nLine 2",
                "voice_examples": [{"id": "ex-1"}],
            },
            "proposal": {"title": "Test"},
        },
        {},
        tmp_path / "attempt",
    )
    contents = client.models.generate_content.call_args.kwargs["contents"]
    assert isinstance(contents, list) and len(contents) == 2
    assert "# Guide\nLine 1\nLine 2" in contents[0]
    payload = json.loads(contents[1])
    assert "writing_guide.md" not in payload["writing_references"]
    assert payload["writing_references"]["voice_examples"] == [{"id": "ex-1"}]


def test_grounding_summary_captures_queries_and_failed_urls(tmp_path, monkeypatch):
    client = Mock()
    result = response()
    result.candidates[0].grounding_metadata = SimpleNamespace(
        model_dump=lambda **kwargs: {
            "web_search_queries": ["q1", "q2"],
            "grounding_chunks": [{"web": {"uri": "https://example.com/search-hit"}}],
        }
    )
    result.candidates[0].url_context_metadata = SimpleNamespace(
        model_dump=lambda **kwargs: {
            "url_metadata": [
                {
                    "retrieved_url": "https://example.com/ok",
                    "url_retrieval_status": "URL_RETRIEVAL_STATUS_SUCCESS",
                },
                {
                    "retrieved_url": "https://example.com/broken",
                    "url_retrieval_status": "URL_RETRIEVAL_STATUS_ERROR",
                },
            ]
        }
    )
    client.models.generate_content.return_value = result
    monkeypatch.setattr(gemini, "get_gemini_client", lambda: client)
    attempt = tmp_path / "attempt"
    gemini.run_stage("Research", {}, {}, attempt, search=True)
    saved = json.loads((attempt / "grounding.json").read_text())["summary"]
    assert saved["web_search_queries"] == 2
    assert saved["url_context_success"] == 1
    assert saved["url_context_failed"] == 1
    assert "https://example.com/broken" in saved["failed_urls"]
    metrics = json.loads((attempt / "metrics.json").read_text())
    assert metrics["grounding"]["web_search_queries"] == 2
