"""Console logging controls for the New Releases pipeline."""

from __future__ import annotations

import os


def _env_flag(value: str | None) -> bool:
    normalized = (value or "").strip().lower()
    return normalized in {"1", "true", "yes", "on"}


def set_debug_mode(enabled: bool) -> None:
    global _DEBUG_ENABLED
    _DEBUG_ENABLED = enabled or _env_flag(os.getenv("NC_DEBUG"))


def _emit(icon: str, message: str) -> None:
    print(f"{icon} {message}")


def log_info(message: str) -> None:
    _emit("💡", message)


def log_success(message: str) -> None:
    _emit("✅", message)


def log_warning(message: str) -> None:
    _emit("⚠️", message)


def log_error(message: str) -> None:
    _emit("❌", message)


def log_debug(message: str) -> None:
    if _DEBUG_ENABLED:
        _emit("⋯", message)


set_debug_mode(False)


__all__ = [
    "log_debug",
    "log_error",
    "log_info",
    "log_success",
    "log_warning",
    "set_debug_mode",
]

