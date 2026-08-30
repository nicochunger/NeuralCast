"""Gemini text-generation boundary for AI host scripts."""

from __future__ import annotations

from typing import Any, Dict, List

from neuralcast.services.ai_client import (
    DEFAULT_GEMINI_TEXT_MODEL,
    get_gemini_client,
)


def gemini_generate_text(
    prompt: str,
    system_prompt: str,
    temperature: float,
    top_p: float,
    with_search: bool,
    model: str = DEFAULT_GEMINI_TEXT_MODEL,
) -> str:
    client = get_gemini_client()
    try:
        from google.genai import types
    except ImportError as exc:  # pragma: no cover - environment guard
        raise RuntimeError(
            "Gemini client is not installed. Install with: pip install google-genai"
        ) from exc

    config_kwargs: Dict[str, Any] = {
        "system_instruction": system_prompt,
        "temperature": temperature,
        "top_p": top_p,
    }
    if with_search:
        # Explicit Google Search grounding for research-backed generations.
        grounding_tool = types.Tool(google_search=types.GoogleSearch())
        config_kwargs["tools"] = [grounding_tool]

    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(**config_kwargs),
    )
    text = (response.text or "").strip()
    if with_search and text == "NO_SCRIPT":
        try:
            candidates = getattr(response, "candidates", None) or []
            finish_reason = None
            grounding_titles: List[str] = []
            grounding_chunk_count = 0
            if candidates:
                candidate0 = candidates[0]
                finish = getattr(candidate0, "finish_reason", None)
                finish_reason = str(finish) if finish is not None else None
                grounding = getattr(candidate0, "grounding_metadata", None)
                chunks = getattr(grounding, "grounding_chunks", None) or []
                grounding_chunk_count = len(chunks)
                for chunk in chunks[:5]:
                    web = getattr(chunk, "web", None)
                    title = (getattr(web, "title", None) or "").strip() if web else ""
                    if title:
                        grounding_titles.append(title)
            usage = getattr(response, "usage_metadata", None)
            total_tokens = getattr(usage, "total_token_count", None)
            LOGGER.warning(
                "[gemini/search] NO_SCRIPT response_id=%s finish=%s grounding_chunks=%s grounding_titles=%s total_tokens=%s",
                getattr(response, "response_id", None),
                finish_reason,
                grounding_chunk_count,
                grounding_titles,
                total_tokens,
            )
        except Exception:  # noqa: BLE001
            LOGGER.debug("[gemini/search] Failed to summarize grounding metadata for NO_SCRIPT.")
    if not text:
        raise RuntimeError("Gemini returned an empty text response.")
    return text


__all__ = ["gemini_generate_text"]
