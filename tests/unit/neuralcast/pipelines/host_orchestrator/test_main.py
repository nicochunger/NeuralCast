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


def test_validate_runtime_args_rejects_schedule_only_with_forced_archetype() -> None:
    with pytest.raises(
        host_main.ArgumentValidationError,
        match="cannot be combined with --force-archetype",
    ):
        host_main.validate_runtime_args(
            argparse.Namespace(
                force_archetype=Archetype.BLOCK_INTRO.value,
                force_track_focus=None,
                scheduled_block_intros_only=True,
            )
        )


def test_schedule_only_parser_flag_defaults_false_and_can_be_enabled() -> None:
    parser = host_main.build_arg_parser()

    assert parser.parse_args([]).scheduled_block_intros_only is False
    assert (
        parser.parse_args(["--scheduled-block-intros-only"]).scheduled_block_intros_only
        is True
    )
