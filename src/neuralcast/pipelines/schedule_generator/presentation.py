"""Editorial presentation metadata for generated station schedules.

The scheduler owns the factual playlist assignment.  This module adds short,
cached listener-facing labels without allowing an unavailable LLM to affect a
schedule application.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
from typing import Any, Mapping, Sequence

from neuralcast.services.ai_client import DEFAULT_GEMINI_TEXT_MODEL

from .models import DailyTemplateBlock, WeeklySchedulePlan

LOGGER = logging.getLogger("schedule_generator")
PRESENTATION_VERSION = 1
MAX_TITLE_WORDS = 4
MAX_DESCRIPTION_WORDS = 7


def build_schedule_presentation(plan: WeeklySchedulePlan) -> dict[str, Any]:
    """Build short bilingual copy for each unique scheduled playlist set.

    A deterministic fallback is always available.  Generation failures are
    deliberately contained here so the radio schedule can still be applied.
    """

    units = _presentation_units(plan.daily_template)
    fallback_blocks = [_fallback_block(unit) for unit in units]
    generated = _generate_copy(units)
    blocks = _merge_generated_copy(fallback_blocks, generated)
    return {
        "version": PRESENTATION_VERSION,
        "plan_hash": plan.plan_hash,
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "blocks": blocks,
    }


def presentation_matches_plan(value: object, plan_hash: str) -> bool:
    return isinstance(value, Mapping) and value.get("plan_hash") == plan_hash


def _presentation_units(
    blocks: Sequence[DailyTemplateBlock],
) -> list[dict[str, Any]]:
    unique: dict[str, dict[str, Any]] = {}
    for block in blocks:
        if block.mode != "playlist" or not block.playlist_names:
            continue
        playlist_ids = sorted(str(item) for item in block.playlist_ids)
        playlist_names = sorted(str(item).strip() for item in block.playlist_names if str(item).strip())
        if not playlist_names:
            continue
        key = _block_key(playlist_ids, playlist_names)
        unique.setdefault(
            key,
            {
                "key": key,
                "playlist_ids": playlist_ids,
                "playlist_names": playlist_names,
                "kind": "single" if len(playlist_names) == 1 else "combo",
            },
        )
    return list(unique.values())


def _fallback_block(unit: Mapping[str, Any]) -> dict[str, Any]:
    names = list(unit["playlist_names"])
    single = len(names) == 1
    title = names[0] if single else f"{names[0]} Mix"
    en_description = (
        f"A focused selection of {names[0]}."
        if single
        else "A powerful blend of complementary sounds."
    )
    es_description = (
        f"Una selección intensa de {names[0]}."
        if single
        else "Una mezcla potente de sonidos complementarios."
    )
    return {
        **dict(unit),
        "translations": {
            "en": {"description": en_description, **({} if single else {"title": title})},
            "es": {"description": es_description, **({} if single else {"title": title})},
        },
    }


def _generate_copy(units: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
    if not units or not os.getenv("GEMINI_API_KEY", "").strip():
        return {}
    try:
        from google import genai
        from google.genai import types

        prompt = _build_prompt(units)
        client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
        response = client.models.generate_content(
            model=os.getenv("SCHEDULE_PRESENTATION_MODEL", DEFAULT_GEMINI_TEXT_MODEL),
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.4,
            ),
        )
        parsed = json.loads(response.text or "{}")
        return parsed if isinstance(parsed, Mapping) else {}
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("[presentation] LLM copy unavailable; using fallback copy: %s", exc)
        return {}


def _build_prompt(units: Sequence[Mapping[str, Any]]) -> str:
    return (
        "Create concise public radio schedule copy. Return JSON only in exactly this shape: "
        '{"blocks":[{"key":"...","en":{"title":"optional","description":"..."},'
        '"es":{"title":"optional","description":"..."}}]}. '
        "For every key below, write English and Rioplatense-neutral Spanish. Descriptions must be "
        f"between 5 and {MAX_DESCRIPTION_WORDS} words. For multi-playlist blocks, titles must be "
        f"at most {MAX_TITLE_WORDS} words. Do not invent artists, songs, or factual claims. "
        "For single-playlist blocks omit title.\n\n"
        f"Blocks: {json.dumps(list(units), ensure_ascii=False)}"
    )


def _merge_generated_copy(
    fallback_blocks: Sequence[dict[str, Any]], generated: Mapping[str, Any]
) -> list[dict[str, Any]]:
    raw_blocks = generated.get("blocks") if isinstance(generated, Mapping) else None
    generated_by_key = {
        item.get("key"): item
        for item in raw_blocks
        if isinstance(item, Mapping) and isinstance(item.get("key"), str)
    } if isinstance(raw_blocks, list) else {}
    result: list[dict[str, Any]] = []
    for fallback in fallback_blocks:
        candidate = generated_by_key.get(fallback["key"])
        translations = dict(fallback["translations"])
        if isinstance(candidate, Mapping):
            for locale in ("en", "es"):
                copy = candidate.get(locale)
                if not isinstance(copy, Mapping):
                    continue
                description = _valid_description(copy.get("description"))
                title = _valid_title(copy.get("title")) if fallback["kind"] == "combo" else None
                if description:
                    translations[locale] = {"description": description}
                    if title:
                        translations[locale]["title"] = title
        result.append({**fallback, "translations": translations})
    return result


def _valid_description(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.split())
    words = cleaned.split()
    return cleaned if 5 <= len(words) <= MAX_DESCRIPTION_WORDS else None


def _valid_title(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.split())
    return cleaned if cleaned and len(cleaned.split()) <= MAX_TITLE_WORDS else None


def _block_key(playlist_ids: Sequence[str], playlist_names: Sequence[str]) -> str:
    return "ids:" + ",".join(playlist_ids) if playlist_ids else "names:" + ",".join(
        name.casefold() for name in playlist_names
    )
