"""Unit tests for host orchestrator runtime entrypoint validation."""

from __future__ import annotations

import argparse

import pytest

from neuralcast.pipelines.host_orchestrator import main as host_main
from neuralcast.pipelines.host_orchestrator.models import Archetype, TrackFocus


def test_validate_runtime_args_accepts_empty_focus() -> None:
    assert host_main.validate_runtime_args(
        argparse.Namespace(force_archetype=None, force_track_focus=None)
    ) is None


def test_validate_runtime_args_rejects_invalid_focus_pairing() -> None:
    with pytest.raises(host_main.ArgumentValidationError):
        host_main.validate_runtime_args(
            argparse.Namespace(
                force_archetype=Archetype.BACK_SELL.value,
                force_track_focus=TrackFocus.NEXT.value,
            )
        )
