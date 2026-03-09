"""Configuration, constants, logging, and dependency guards for schedule generation."""

from __future__ import annotations

import logging
from typing import Any

try:
    import requests
except ModuleNotFoundError:  # pragma: no cover - dependency guard
    requests = None  # type: ignore[assignment]

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:  # pragma: no cover - dependency guard

    def load_dotenv(*_args: Any, **_kwargs: Any) -> bool:
        return False

if requests is not None:
    from requests import Response

    RequestsHTTPError = requests.HTTPError
else:  # pragma: no cover - dependency guard
    Response = Any  # type: ignore[misc,assignment]

    class RequestsHTTPError(Exception):
        pass

try:
    from urllib3.exceptions import InsecureRequestWarning
except ModuleNotFoundError:  # pragma: no cover - dependency guard

    class InsecureRequestWarning(Warning):
        pass

LOGGER = logging.getLogger("schedule_generator")

STATE_FILENAME = "ai_schedule_state.json"
STATE_VERSION = 1

DEFAULT_OPEN_RATIO_MIN = 0.20
DEFAULT_OPEN_RATIO_MAX = 0.40
DEFAULT_MIN_OPEN_SLOTS = 3
DEFAULT_MAX_OPEN_SLOTS = 6
DEFAULT_MIN_BLOCK_MINUTES = 30
DEFAULT_MAX_BLOCK_MINUTES = 90
DEFAULT_TEMPLATE_TARGET_BLOCK_MINUTES = 120
UNSCHEDULED_WINDOW_START_MINUTE = 22 * 60
UNSCHEDULED_WINDOW_END_MINUTE = 6 * 60
UNSCHEDULED_WINDOW_TOTAL_MINUTES = (
    (24 * 60 - UNSCHEDULED_WINDOW_START_MINUTE) + UNSCHEDULED_WINDOW_END_MINUTE
)

FALLBACK_TIMEZONE = "Europe/Zurich"


def configure_logging(level: int = logging.INFO) -> None:
    if LOGGER.handlers:
        return

    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s | %(name)s | %(levelname)-7s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    LOGGER.addHandler(handler)
    LOGGER.setLevel(level)
    LOGGER.propagate = False
