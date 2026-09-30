"""Bounded TypeSafe decision requests used by the host memory selector."""

from __future__ import annotations

import os
import time
from typing import Any, Mapping

import requests


class DecisionUnavailable(RuntimeError):
    """A decision could not be obtained; callers can continue without it."""


def is_configured() -> bool:
    return bool(os.getenv("TYPESAFE_API_KEY", "").strip())


def evaluate_questions(
    state: Mapping[str, Any], questions: Mapping[str, Any]
) -> dict[str, Any]:
    """Evaluate all questions in one request, with one transient-error retry.

    Never include response bodies or credentials in errors: upstream errors can
    contain the submitted state. Keep this optional step within a short budget.
    """
    key = os.getenv("TYPESAFE_API_KEY", "").strip()
    if not key:
        raise DecisionUnavailable("TypeSafe key is not configured")
    payload = {
        "model": os.getenv("TYPESAFE_MODEL", "jev-1.13.0"),
        "state": state,
        "questions": questions,
    }
    for attempt in range(2):
        try:
            response = requests.post(
                "https://api.typesafe.ai/v1/systemone",
                headers={"Authorization": f"Bearer {key}"},
                json=payload,
                timeout=(2, 5),
            )
        except requests.RequestException:
            if attempt == 0:
                time.sleep(0.25)
                continue
            raise DecisionUnavailable("TypeSafe connection failed") from None
        if response.status_code in {429, 500, 502, 503, 504, 529} and attempt == 0:
            time.sleep(0.25)
            continue
        if response.status_code != 200:
            raise DecisionUnavailable(f"TypeSafe returned HTTP {response.status_code}")
        try:
            result = response.json()
        except ValueError:
            raise DecisionUnavailable("TypeSafe returned invalid JSON") from None
        if not isinstance(result, dict) or not isinstance(result.get("answers"), dict):
            raise DecisionUnavailable("TypeSafe returned an invalid decision response")
        return result
    raise DecisionUnavailable("TypeSafe request failed")
