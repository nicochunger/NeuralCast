"""Centralized paths and shared configuration for NeuralCast modules."""

from __future__ import annotations

from pathlib import Path


# Paths anchored to the repository root (three levels up from this file:
# config.py -> neuralcast -> src -> repo_root)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
PACKAGE_ROOT = SRC_ROOT / "neuralcast"
ASSETS_ROOT = PACKAGE_ROOT / "assets"
LOGS_ROOT = PROJECT_ROOT / "logs"


def ensure_logs_dir() -> Path:
    LOGS_ROOT.mkdir(parents=True, exist_ok=True)
    return LOGS_ROOT


__all__ = [
    "PROJECT_ROOT",
    "SRC_ROOT",
    "PACKAGE_ROOT",
    "ASSETS_ROOT",
    "LOGS_ROOT",
    "ensure_logs_dir",
]
