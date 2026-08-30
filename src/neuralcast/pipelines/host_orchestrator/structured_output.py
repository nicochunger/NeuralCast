"""Parsing helpers shared by structured host-generation outputs."""

from __future__ import annotations

import datetime as dt
import json
import re
from email.utils import parsedate_to_datetime
from typing import Any, Mapping, Optional, Tuple

from .script_processing import cleanup_generated_script


def parse_structured_script_and_meta(
    raw: str, pattern: re.Pattern[str]
) -> Tuple[Optional[str], Optional[Mapping[str, Any]], str]:
    text = raw.strip()
    if text == "NO_SCRIPT":
        return None, None, "NO_SCRIPT"

    match = pattern.search(text)
    if not match:
        return None, None, "invalid format"

    script = cleanup_generated_script(match.group("script"))
    meta_raw = match.group("meta").strip()

    if meta_raw.startswith("```"):
        meta_raw = re.sub(r"^```(?:json)?", "", meta_raw, flags=re.IGNORECASE).strip()
        meta_raw = re.sub(r"```$", "", meta_raw).strip()

    try:
        meta = json.loads(meta_raw)
    except json.JSONDecodeError:
        return None, None, "invalid json"

    if not isinstance(meta, Mapping):
        return None, None, "meta must be object"

    if not script:
        return None, None, "script is empty"

    return script, meta, "ok"


def parse_timestamp(value: Optional[str]) -> Optional[dt.datetime]:
    if not value:
        return None

    text = value.strip()
    if not text:
        return None

    try:
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed.astimezone(dt.timezone.utc)
    except ValueError:
        pass

    try:
        parsed = parsedate_to_datetime(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed.astimezone(dt.timezone.utc)
    except (TypeError, ValueError):
        return None



__all__ = ["parse_structured_script_and_meta", "parse_timestamp"]
