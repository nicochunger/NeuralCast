"""Prompt loading, Gemini generation, and weekly plan assembly."""

from __future__ import annotations

import datetime as dt
import json
import pathlib
import random
import re
from typing import Any, Dict, List, Mapping, Optional, Sequence

from neuralcast.services.openai_client import get_gemini_client

from .config import (
    GENERATION_MAX_ATTEMPTS,
    LOGGER,
    SCHEDULE_SYSTEM_PROMPT_PATH,
    SCHEDULE_USER_PROMPT_PATH,
)
from .models import ScheduleValidationError, StationPlaylist, WeeklySchedulePlan
from .state import run_with_retries
from .template import (
    build_deterministic_daily_template,
    build_plan_hash,
    expand_daily_template_to_week,
    format_seed_template_for_prompt,
    validate_daily_template,
)

def strip_code_fences(text: str) -> str:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?", "", cleaned, flags=re.IGNORECASE).strip()
    cleaned = re.sub(r"```$", "", cleaned).strip()
    return cleaned


def extract_json_object(text: str) -> Mapping[str, Any]:
    cleaned = strip_code_fences(text)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end < 0 or end <= start:
        raise ScheduleValidationError("No JSON object found in model output.")

    candidate = cleaned[start : end + 1]
    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise ScheduleValidationError(f"Model output contains invalid JSON: {exc}") from exc

    if not isinstance(payload, Mapping):
        raise ScheduleValidationError("Top-level model output must be a JSON object.")
    return payload



def load_prompt(path: pathlib.Path) -> str:
    if not path.is_file():
        raise FileNotFoundError(f"Missing schedule prompt template: {path}")
    return path.read_text(encoding="utf-8").strip()


def build_playlist_catalog(playlists: Sequence[StationPlaylist]) -> str:
    lines: List[str] = []
    for playlist in sorted(playlists, key=lambda item: item.name.lower()):
        lines.append(
            f"- id={playlist.id}; name={playlist.name}; weight={playlist.weight:.2f}; enabled={playlist.is_enabled}"
        )
    return "\n".join(lines)



def gemini_generate_schedule_text(
    prompt: str,
    system_prompt: str,
    model: str,
    temperature: float = 0.4,
    top_p: float = 0.85,
) -> str:
    client = get_gemini_client()
    try:
        from google.genai import types
    except ImportError as exc:
        raise RuntimeError(
            "Gemini client is not installed. Install with: pip install google-genai"
        ) from exc

    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=temperature,
            top_p=top_p,
        ),
    )
    text = (response.text or "").strip()
    if not text:
        raise RuntimeError("Gemini returned an empty response while generating schedule.")
    return text


def build_weekly_plan_with_llm(
    station_slug: str,
    station_name: str,
    timezone_name: str,
    week_start: dt.date,
    week_end: dt.date,
    playlists: Sequence[StationPlaylist],
    open_ratio_min: float,
    open_ratio_max: float,
    min_block_minutes: int,
    max_block_minutes: int,
    model: str,
) -> WeeklySchedulePlan:
    system_prompt = load_prompt(SCHEDULE_SYSTEM_PROMPT_PATH)
    user_template = load_prompt(SCHEDULE_USER_PROMPT_PATH)

    enabled_playlists = [playlist for playlist in playlists if playlist.is_enabled]
    playlist_by_id = {playlist.id: playlist for playlist in enabled_playlists}
    if not playlist_by_id:
        raise RuntimeError("No enabled playlists available for schedule generation.")

    deterministic_template = build_deterministic_daily_template(
        playlist_by_id=playlist_by_id,
        open_ratio_min=open_ratio_min,
        open_ratio_max=open_ratio_max,
        min_block_minutes=min_block_minutes,
        max_block_minutes=max_block_minutes,
    )
    prompt = user_template.format(
        station_slug=station_slug,
        station_name=station_name,
        timezone=timezone_name,
        week_start=week_start.isoformat(),
        week_end=week_end.isoformat(),
        open_ratio_min=f"{open_ratio_min:.2f}",
        open_ratio_max=f"{open_ratio_max:.2f}",
        min_block_minutes=min_block_minutes,
        max_block_minutes=max_block_minutes,
        playlist_catalog=build_playlist_catalog(enabled_playlists),
        deterministic_seed_template=format_seed_template_for_prompt(
            deterministic_template
        ),
    )

    # Use slight randomization in retries to avoid repeating invalid shape.
    rng = random.Random()
    last_error: Optional[Exception] = None
    output = ""
    for attempt in range(1, GENERATION_MAX_ATTEMPTS + 1):
        if attempt == 1:
            output = run_with_retries(
                "Generate weekly schedule",
                lambda: gemini_generate_schedule_text(
                    prompt=prompt,
                    system_prompt=system_prompt,
                    model=model,
                    temperature=0.35,
                    top_p=0.82,
                ),
            )
        else:
            repair_prompt = (
                "Tu salida anterior no cumplio el contrato JSON o las reglas de validacion. "
                "Reescribi la salida usando exactamente el mismo esquema requerido. "
                "No agregues texto fuera del JSON. "
                f"Error de validacion: {last_error}\n"
                "Salida anterior:\n"
                f"{output}"
            )
            output = run_with_retries(
                "Repair weekly schedule JSON",
                lambda: gemini_generate_schedule_text(
                    prompt=repair_prompt,
                    system_prompt=system_prompt,
                    model=model,
                    temperature=0.20 + rng.uniform(0.0, 0.1),
                    top_p=0.7,
                ),
            )

        try:
            payload = extract_json_object(output)
            raw_blocks = payload.get("daily_template")
            if not isinstance(raw_blocks, list):
                raise ScheduleValidationError(
                    "JSON must include a 'daily_template' array."
                )

            daily_template = validate_daily_template(
                raw_blocks=raw_blocks,
                playlist_by_id=playlist_by_id,
                open_ratio_min=open_ratio_min,
                open_ratio_max=open_ratio_max,
                min_block_minutes=min_block_minutes,
                max_block_minutes=max_block_minutes,
            )
            expanded = expand_daily_template_to_week(daily_template, week_start)
            plan_hash = build_plan_hash(
                station=station_slug,
                timezone_name=timezone_name,
                week_start=week_start,
                daily_template=daily_template,
            )
            rationale = str(payload.get("rationale") or "").strip()
            if not rationale:
                rationale = "LLM-generated weekly fixed daily template."

            return WeeklySchedulePlan(
                station=station_slug,
                station_name=station_name,
                timezone=timezone_name,
                week_start_local_date=week_start.isoformat(),
                week_end_local_date=week_end.isoformat(),
                generated_at_utc=dt.datetime.now(dt.timezone.utc).isoformat(),
                open_ratio_min=open_ratio_min,
                open_ratio_max=open_ratio_max,
                daily_template=daily_template,
                expanded_blocks=expanded,
                rationale=rationale,
                plan_hash=plan_hash,
            )
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            LOGGER.warning(
                "[llm] Schedule validation failed (%s/%s): %s",
                attempt,
                GENERATION_MAX_ATTEMPTS,
                exc,
            )

    LOGGER.warning(
        "[llm] Falling back to deterministic weekly template after %s failed attempt(s): %s",
        GENERATION_MAX_ATTEMPTS,
        last_error,
    )
    expanded = expand_daily_template_to_week(deterministic_template, week_start)
    plan_hash = build_plan_hash(
        station=station_slug,
        timezone_name=timezone_name,
        week_start=week_start,
        daily_template=deterministic_template,
    )
    rationale = "Deterministic weekly template fallback."
    if last_error is not None:
        rationale = f"{rationale} LLM error: {last_error}"

    return WeeklySchedulePlan(
        station=station_slug,
        station_name=station_name,
        timezone=timezone_name,
        week_start_local_date=week_start.isoformat(),
        week_end_local_date=week_end.isoformat(),
        generated_at_utc=dt.datetime.now(dt.timezone.utc).isoformat(),
        open_ratio_min=open_ratio_min,
        open_ratio_max=open_ratio_max,
        daily_template=deterministic_template,
        expanded_blocks=expanded,
        rationale=rationale,
        plan_hash=plan_hash,
    )


