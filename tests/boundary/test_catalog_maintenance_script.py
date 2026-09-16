"""Boundary tests for scheduled catalog maintenance orchestration."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = PROJECT_ROOT / "deployment" / "run_catalog_maintenance.sh"
pytestmark = pytest.mark.allow_external_calls


def _git(root: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(root), *args], text=True, stderr=subprocess.STDOUT
    ).strip()


def _repository(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    if root.exists():
        return root
    root.mkdir()
    remote = tmp_path / "origin.git"
    subprocess.run(
        ["git", "init", "--bare", str(remote)], check=True, capture_output=True
    )
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.name", "Maintenance Test")
    _git(root, "config", "user.email", "maintenance@example.invalid")
    for name in [
        "NeuralForge/playlists/Prog Metal.csv",
        "NeuralCast/playlists/Rock.csv",
        "NeuralForge/metadata/ArtistIDs.json",
        "NeuralForge/metadata/New Releases.metadata.json",
        "NeuralForge/metadata/ai_schedule_state.json",
        "code.py",
    ]:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("original\n")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "Initial")
    _git(root, "remote", "add", "origin", str(remote))
    _git(root, "push", "-u", "origin", "main")
    return root


def _run_script(
    tmp_path: Path,
    mode: str,
    *,
    fail_new_releases: bool = False,
    fail_sync: bool = False,
):
    root = _repository(tmp_path)
    call_log = tmp_path / "calls.log"
    call_log.write_text("")
    fake_python = tmp_path / "python"
    fake_python.write_text(
        "#!/bin/sh\n"
        'printf "%s\\n" "$*" >> "$NC_MAINTENANCE_CALL_LOG"\n'
        'if [ "${NC_MAINTENANCE_FAIL_NEW_RELEASES:-0}" = "1" ] && '
        'printf "%s" "$*" | grep -q "update_new_releases"; then\n'
        "    exit 9\n"
        "fi\n"
        'if [ "${NC_MAINTENANCE_FAIL_SYNC:-0}" = "1" ] && '
        'printf "%s" "$*" | grep -q "sync_playlists"; then\n'
        "    exit 8\n"
        "fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    env = {
        **os.environ,
        "NC_MAINTENANCE_PROJECT_ROOT": str(root),
        "NC_MAINTENANCE_PYTHON": str(fake_python),
        "NC_MAINTENANCE_LOCK_FILE": str(tmp_path / "maintenance.lock"),
        "NC_MAINTENANCE_CALL_LOG": str(call_log),
        "NC_MAINTENANCE_FAIL_NEW_RELEASES": "1" if fail_new_releases else "0",
        "NC_MAINTENANCE_FAIL_SYNC": "1" if fail_sync else "0",
    }
    result = subprocess.run(
        [str(SCRIPT), mode],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    return result, call_log.read_text(encoding="utf-8").splitlines()


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


def test_commits_catalog_and_metadata_without_unrelated_staged_work(tmp_path: Path):
    root = _repository(tmp_path)
    changed = [
        "NeuralForge/playlists/Prog Metal.csv",
        "NeuralForge/metadata/ArtistIDs.json",
        "NeuralForge/metadata/New Releases.metadata.json",
        "NeuralCast/playlists/New Playlist.csv",
    ]
    for name in changed:
        (root / name).write_text("updated\n")
    deleted = "NeuralCast/playlists/Rock.csv"
    (root / deleted).unlink()
    (root / "code.py").write_text("unrelated staged work\n")
    _git(root, "add", "code.py")
    (root / "NeuralForge/metadata/ai_schedule_state.json").write_text("runtime\n")

    result, _ = _run_script(tmp_path, "daily")

    assert result.returncode == 0, result.stdout + result.stderr
    committed = _git(
        root, "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD"
    ).splitlines()
    assert set(committed) == {*changed, deleted}
    assert _git(root, "diff", "--cached", "--name-only") == "code.py"
    assert "ai_schedule_state.json" in _git(root, "diff", "--name-only")
    assert _git(root, "rev-parse", "HEAD") == _git(root, "rev-parse", "origin/main")


@pytest.mark.parametrize("failure", ["new_releases", "sync"])
def test_pipeline_failure_does_not_commit_or_push(tmp_path: Path, failure: str):
    root = _repository(tmp_path)
    before = _git(root, "rev-parse", "HEAD")
    (root / "NeuralForge/playlists/Prog Metal.csv").write_text("partial changes\n")
    result, _ = _run_script(
        tmp_path,
        "saturday",
        fail_new_releases=failure == "new_releases",
        fail_sync=failure == "sync",
    )
    assert result.returncode == 1
    assert "Skipping catalog commit/push" in result.stdout
    assert _git(root, "rev-parse", "HEAD") == before
    assert _git(root, "rev-parse", "origin/main") == before
    assert _git(root, "diff", "--cached", "--name-only") == ""


def test_unchanged_run_retries_failed_push_without_empty_commit(tmp_path: Path):
    root = _repository(tmp_path)
    _git(root, "remote", "set-url", "origin", str(tmp_path / "missing.git"))
    (root / "NeuralForge/playlists/Prog Metal.csv").write_text("updated\n")
    result, _ = _run_script(tmp_path, "daily")
    assert result.returncode == 1
    assert "FAILED catalog commit/push" in result.stdout
    commit = _git(root, "rev-parse", "HEAD")
    assert commit != _git(root, "rev-parse", "origin/main")

    _git(root, "remote", "set-url", "origin", str(tmp_path / "origin.git"))
    result, _ = _run_script(tmp_path, "daily")
    assert result.returncode == 0
    assert "No catalog changes to commit" in result.stdout
    assert _git(root, "rev-parse", "HEAD") == commit
    assert _git(root, "rev-parse", "origin/main") == commit


def test_other_branch_is_not_published(tmp_path: Path):
    root = _repository(tmp_path)
    _git(root, "checkout", "-b", "work")
    (root / "NeuralForge/playlists/Prog Metal.csv").write_text("updated\n")
    result, _ = _run_script(tmp_path, "daily")
    assert result.returncode == 1
    assert "Refusing automatic catalog publication" in result.stdout
