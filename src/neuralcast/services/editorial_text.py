"""Bounded Gemini editorial requests with opt-in server-side research tools."""

import json
from pathlib import Path
import random
import re
from time import monotonic, sleep as _sleep
from typing import Any

from google.genai import types

from .ai_client import DEFAULT_GEMINI_TEXT_MODEL, get_gemini_client
from .editorial_agent import write_json

MODEL = DEFAULT_GEMINI_TEXT_MODEL
DEFAULT_MAX_OUTPUT_TOKENS = 65536
TRANSIENT_STATUS_CODES = frozenset({408, 429, 500, 502, 503, 504})
TRANSIENT_ATTEMPTS = 3
BASE_BACKOFF_SECONDS = 2.0
sleep = _sleep


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


def _format_contents(context: dict) -> str | list[str]:
    """Place shared Markdown references in a leading content part when present."""
    refs = context.get("writing_references")
    if isinstance(refs, dict) and isinstance(refs.get("writing_guide.md"), str):
        rest_refs = {k: v for k, v in refs.items() if k != "writing_guide.md"}
        rest = {
            **{k: v for k, v in context.items() if k != "writing_references"},
            **({"writing_references": rest_refs} if rest_refs else {}),
        }
        return [
            f"# Reference: writing_guide.md\n\n{refs['writing_guide.md']}",
            json.dumps(rest, ensure_ascii=False),
        ]
    return json.dumps(context, ensure_ascii=False)


def _is_transient_error(exc: Exception, code: Any) -> bool:
    if isinstance(code, int) and code in TRANSIENT_STATUS_CODES:
        return True
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True
    return False


def _sanitize_error_detail(exc: Exception, code: Any) -> str | None:
    """Return a safe diagnostic hint for non-auth 400 errors without leaking secrets."""
    if code != 400:
        return None
    raw = str(getattr(exc, "message", "") or exc)
    sanitized = re.sub(
        r"(?i)(bearer\s+\S+|api[_-]?key\s*[:=]\s*\S+|key=\S+|token\s*[:=]\s*\S+)",
        "[REDACTED]",
        raw,
    )
    sanitized = " ".join(sanitized.split())
    return sanitized[:240] or None


def summarize_grounding(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    """Extract compact, checkable grounding stats from Gemini tool metadata."""
    web_queries: list[str] = []
    grounding_urls: list[str] = []
    retrieved_urls: list[str] = []
    failed_urls: list[str] = []
    for candidate in candidates:
        gm = candidate.get("grounding_metadata") or {}
        for query in gm.get("web_search_queries") or []:
            if isinstance(query, str) and query.strip():
                web_queries.append(query.strip())
        for chunk in gm.get("grounding_chunks") or []:
            web = chunk.get("web") if isinstance(chunk, dict) else None
            uri = web.get("uri") if isinstance(web, dict) else None
            if isinstance(uri, str) and uri.strip():
                grounding_urls.append(uri.strip())
        uc = candidate.get("url_context_metadata") or {}
        for item in uc.get("url_metadata") or []:
            if not isinstance(item, dict):
                continue
            url = item.get("retrieved_url")
            status = str(item.get("url_retrieval_status") or "")
            if not isinstance(url, str) or not url.strip():
                continue
            if status.endswith("SUCCESS"):
                retrieved_urls.append(url.strip())
            else:
                failed_urls.append(url.strip())
    return {
        "web_search_queries": len(web_queries),
        "grounding_chunks": len(grounding_urls),
        "url_context_success": len(retrieved_urls),
        "url_context_failed": len(failed_urls),
        "retrieved_urls": list(dict.fromkeys([*grounding_urls, *retrieved_urls])),
        "failed_urls": list(dict.fromkeys(failed_urls)),
    }


def run_stage(
    prompt: str,
    context: dict,
    schema: dict,
    attempt: Path,
    *,
    search: bool = False,
    thinking_level: str = "medium",
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
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
    metrics: dict[str, Any] = {
        "provider": "gemini",
        "model": MODEL,
        "thinking_level": thinking_level,
        "max_output_tokens": max_output_tokens,
        "search": search,
        "transport_attempts": 0,
        "usage": None,
    }
    config = types.GenerateContentConfig(
        system_instruction=prompt,
        response_mime_type="application/json",
        response_json_schema=schema,
        thinking_config=types.ThinkingConfig(thinking_level=thinking_level.upper()),
        max_output_tokens=max_output_tokens,
        tools=(
            [
                types.Tool(google_search=types.GoogleSearch()),
                types.Tool(url_context=types.UrlContext()),
            ]
            if search
            else []
        ),
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        http_options=types.HttpOptions(
            timeout=timeout_seconds * 1000,
            retry_options=types.HttpRetryOptions(attempts=1),
        ),
    )
    contents = _format_contents(context)
    try:
        response = None
        for transport_attempt in range(1, TRANSIENT_ATTEMPTS + 1):
            metrics["transport_attempts"] = transport_attempt
            try:
                response = get_gemini_client().models.generate_content(
                    model=MODEL,
                    contents=contents,
                    config=config,
                )
                break
            except Exception as exc:
                code = getattr(exc, "code", None)
                if transport_attempt < TRANSIENT_ATTEMPTS and _is_transient_error(
                    exc, code
                ):
                    delay = BASE_BACKOFF_SECONDS * (2 ** (transport_attempt - 1))
                    delay += random.uniform(0.0, 0.5)
                    sleep(delay)
                    continue
                category = (
                    "configuration"
                    if isinstance(exc, (ValueError, RuntimeError))
                    or code in (400, 401, 403, 404)
                    else "provider"
                )
                failure_payload: dict[str, Any] = {
                    "category": category,
                    "status": "failed",
                    "http_status": code if isinstance(code, int) else None,
                }
                detail = _sanitize_error_detail(exc, code)
                if detail:
                    failure_payload["detail"] = detail
                write_json(attempt / "failure.json", failure_payload)
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
            summary = summarize_grounding(evidence)
            write_json(
                attempt / "grounding.json",
                {"candidates": evidence, "summary": summary},
            )
            metrics["grounding"] = {
                key: summary[key]
                for key in (
                    "web_search_queries",
                    "grounding_chunks",
                    "url_context_success",
                    "url_context_failed",
                )
            }
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
                f"Gemini editorial response did not finish ({reason}; max_output_tokens={max_output_tokens}); inspect its output limit or safety status before retrying."
            )
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise ValueError("Gemini returned a non-object document")
        return result
    finally:
        metrics["elapsed_seconds"] = round(monotonic() - started, 3)
        write_json(attempt / "metrics.json", metrics)
