"""Basic cleanup for generated host scripts."""

from __future__ import annotations

import re


def cleanup_generated_script(raw: str) -> str:
    text = raw.strip()
    text = re.sub(r"\[([^\]]+)\]\(\s*https?://[^\)]+\)", r"\1", text)
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"\[\s*\d+\s*\]", "", text)
    text = re.sub(r"\s{2,}", " ", text)
    text = text.replace("```", "")
    return text.strip()
