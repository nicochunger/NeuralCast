"""Boundary tests for scheduled catalog maintenance orchestration."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = PROJECT_ROOT / "deployment" / "run_catalog_maintenance.sh"
pytestmark = pytest.mark.allow_external_calls


def _run_script(tmp_path: Path, mode: str, *, fail_new_releases: bool = False):
    call_log = tmp_path / "calls.log"
    fake_python = tmp_path / "python"
    fake_python.write_text(
        "#!/bin/sh\n"
        'printf "%s\\n" "$*" >> "$NC_MAINTENANCE_CALL_LOG"\n'
        'if [ "${NC_MAINTENANCE_FAIL_NEW_RELEASES:-0}" = "1" ] && '
        'printf "%s" "$*" | grep -q "update_new_releases"; then\n'
        "    exit 9\n"
        "fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    env = {
        **os.environ,
        "NC_MAINTENANCE_PROJECT_ROOT": str(PROJECT_ROOT),
        "NC_MAINTENANCE_PYTHON": str(fake_python),
        "NC_MAINTENANCE_LOCK_FILE": str(tmp_path / "maintenance.lock"),
        "NC_MAINTENANCE_CALL_LOG": str(call_log),
        "NC_MAINTENANCE_FAIL_NEW_RELEASES": "1" if fail_new_releases else "0",
    }
    result = subprocess.run(
        [str(SCRIPT), mode],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    calls = call_log.read_text(encoding="utf-8").splitlines()
    return result, calls


def test_daily_mode_syncs_each_station_once(tmp_path: Path) -> None:
    result, calls = _run_script(tmp_path, "daily")

    assert result.returncode == 0
    assert calls == [
        "-m neuralcast.cli.sync_playlists -s neuralforge",
        "-m neuralcast.cli.sync_playlists -s neuralcast",
    ]


def test_saturday_mode_refreshes_neuralforge_before_sync(tmp_path: Path) -> None:
    result, calls = _run_script(tmp_path, "saturday")

    assert result.returncode == 0
    assert calls == [
        "-m neuralcast.cli.update_new_releases -s neuralforge",
        "-m neuralcast.cli.sync_playlists -s neuralforge",
        "-m neuralcast.cli.sync_playlists -s neuralcast",
    ]


def test_saturday_mode_skips_neuralforge_sync_after_discovery_failure(
    tmp_path: Path,
) -> None:
    result, calls = _run_script(tmp_path, "saturday", fail_new_releases=True)

    assert result.returncode == 1
    assert calls == [
        "-m neuralcast.cli.update_new_releases -s neuralforge",
        "-m neuralcast.cli.sync_playlists -s neuralcast",
    ]
    assert "Skipping NeuralForge playlist sync" in result.stdout
