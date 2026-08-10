"""Centralized paths and shared configuration for NeuralCast modules."""

from __future__ import annotations

from pathlib import Path
from typing import Final


# Paths anchored to the repository root (three levels up from this file:
# config.py -> neuralcast -> src -> repo_root)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
PACKAGE_ROOT = SRC_ROOT / "neuralcast"
ASSETS_ROOT = PACKAGE_ROOT / "assets"
RUNTIME_ROOT = PROJECT_ROOT / "runtime"
LOGS_ROOT = RUNTIME_ROOT / "logs"

STATION_DIRECTORY_BY_SLUG: Final[dict[str, str]] = {
    "neuralcast": "NeuralCast",
    "neuralforge": "NeuralForge",
}
ALLOWED_STATION_SLUGS: Final[tuple[str, ...]] = tuple(
    STATION_DIRECTORY_BY_SLUG.keys()
)
DEFAULT_STATION_SLUG: Final[str] = "neuralforge"


def ensure_logs_dir() -> Path:
    LOGS_ROOT.mkdir(parents=True, exist_ok=True)
    return LOGS_ROOT


def station_dir_from_slug(station_slug: str) -> Path:
    directory_name = STATION_DIRECTORY_BY_SLUG.get(station_slug)
    if directory_name is None:
        allowed = ", ".join(ALLOWED_STATION_SLUGS)
        raise ValueError(
            f"Unsupported station '{station_slug}'. Allowed values: {allowed}."
        )
    return PROJECT_ROOT / directory_name


__all__ = [
    "PROJECT_ROOT",
    "SRC_ROOT",
    "PACKAGE_ROOT",
    "RUNTIME_ROOT",
    "ASSETS_ROOT",
    "LOGS_ROOT",
    "STATION_DIRECTORY_BY_SLUG",
    "ALLOWED_STATION_SLUGS",
    "DEFAULT_STATION_SLUG",
    "ensure_logs_dir",
    "station_dir_from_slug",
]
