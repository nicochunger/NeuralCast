"""Bounded Gemini editorial requests with opt-in server-side research tools."""

import json
from pathlib import Path
from time import monotonic

from google.genai import types

from .ai_client import DEFAULT_GEMINI_TEXT_MODEL, get_gemini_client
from .editorial_agent import write_json

MODEL = DEFAULT_GEMINI_TEXT_MODEL


def response_schema(schema: dict) -> dict:
    """Keep the provider grammar small; Pydantic enforces array counts locally."""

    def convert(value):
        if isinstance(value, dict):
            return {
                key: convert(item)
                for key, item in value.items()
                if key not in {"minItems", "maxItems"}
            }
        if isinstance(value, list):
            return [convert(item) for item in value]
        return value

    return convert(schema)


def run_stage(
    prompt: str,
    context: dict,
    schema: dict,
    attempt: Path,
    *,
    search: bool = False,
    thinking_level: str = "medium",
    max_output_tokens: int = 32768,
    timeout_seconds: int = 900,
) -> dict:
    """Return structured output, optionally researching with server-side tools."""
    attempt.mkdir(parents=True, exist_ok=False)
    (attempt / "prompt.md").write_text(prompt, encoding="utf-8")
    write_json(attempt / "context.json", context)
    write_json(attempt / "validation_schema.json", schema)
    schema = response_schema(schema)
    write_json(attempt / "schema.json", schema)
    started = monotonic()
    metrics = {
        "provider": "gemini",
        "model": MODEL,
        "thinking_level": thinking_level,
        "max_output_tokens": max_output_tokens,
        "search": search,
        "usage": None,
    }
    try:
        try:
            response = get_gemini_client().models.generate_content(
                model=MODEL,
                contents=json.dumps(context, ensure_ascii=False),
                config=types.GenerateContentConfig(
                    system_instruction=prompt,
                    response_mime_type="application/json",
                    response_json_schema=schema,
                    thinking_config=types.ThinkingConfig(
                        thinking_level=thinking_level.upper()
                    ),
                    max_output_tokens=max_output_tokens,
                    tools=(
                        [
                            types.Tool(google_search=types.GoogleSearch()),
                            types.Tool(url_context=types.UrlContext()),
                        ]
                        if search
                        else []
                    ),
                    automatic_function_calling=types.AutomaticFunctionCallingConfig(
                        disable=True
                    ),
                    http_options=types.HttpOptions(
                        timeout=timeout_seconds * 1000,
                        retry_options=types.HttpRetryOptions(attempts=1),
                    ),
                ),
            )
        except Exception as exc:
            code = getattr(exc, "code", None)
            category = (
                "configuration"
                if isinstance(exc, (ValueError, RuntimeError))
                or code in (400, 401, 403, 404)
                else "provider"
            )
            write_json(
                attempt / "failure.json",
                {
                    "category": category,
                    "status": "failed",
                    "http_status": code if isinstance(code, int) else None,
                },
            )
            raise RuntimeError(
                f"Gemini editorial request failed ({category}); inspect provider setup or retry the failed job."
            ) from None
        if search:
            evidence = []
            for candidate in response.candidates or []:
                evidence.append(
                    {
                        key: value.model_dump(mode="json", exclude_none=True)
                        for key in ("grounding_metadata", "url_context_metadata")
                        if (value := getattr(candidate, key, None)) is not None
                    }
                )
            write_json(attempt / "grounding.json", {"candidates": evidence})
        if response.usage_metadata:
            metrics["usage"] = {
                key: value
                for key, value in response.usage_metadata.model_dump(
                    exclude_none=True
                ).items()
                if type(value) is int
            }
        reason = (
            str(response.candidates[0].finish_reason)
            if response.candidates
            else "MISSING"
        )
        metrics["finish_reason"] = reason
        raw = response.text or ""
        (attempt / "response.json").write_text(raw, encoding="utf-8")
        if reason not in ("FinishReason.STOP", "STOP"):
            write_json(
                attempt / "failure.json",
                {"category": "incomplete", "status": "failed", "finish_reason": reason},
            )
            raise RuntimeError(
                "Gemini editorial response did not finish; inspect its output limit or safety status before retrying."
            )
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise ValueError("Gemini returned a non-object document")
        return result
    finally:
        metrics["elapsed_seconds"] = round(monotonic() - started, 3)
        write_json(attempt / "metrics.json", metrics)
