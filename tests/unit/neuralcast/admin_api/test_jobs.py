"""Unit tests for admin API job helpers."""

from __future__ import annotations

from datetime import UTC, datetime

from neuralcast.admin_api import jobs


def test_build_job_id_uses_station_and_label() -> None:
    assert jobs.build_job_id(
        "neuralforge",
        "deep_dive",
        now=datetime(2026, 3, 14, 15, 30, 12, tzinfo=UTC),
    ) == "20260314T153012Z-neuralforge-deep_dive"


def test_build_job_command_dispatches_by_operation(tmp_path) -> None:
    force_job = jobs.JobRecord(
        job_id="force",
        operation=jobs.JOB_OPERATION_FORCE_ARCHETYPE,
        station="neuralforge",
        archetype="deep_dive",
        track_focus="next",
        dry_run=True,
        status="accepted",
        accepted_at="2026-03-14T15:30:12Z",
        started_at=None,
        finished_at=None,
        exit_code=None,
        schedule_options=None,
        log_path=str(tmp_path / "force.log"),
        runner_pid=None,
    )

    argv, _env, _cwd = jobs.build_job_command(force_job, project_root=tmp_path)

    assert argv[1:3] == ["-m", "neuralcast.cli.host_orchestrator"]
    assert "--force-archetype" in argv
    assert "--dry-run" in argv


def test_force_archetype_command_targets_configured_host_channel(tmp_path) -> None:
    argv, _env, _cwd = jobs.build_force_archetype_command(
        "neuralcast-en",
        "back_sell",
        None,
        True,
        project_root=tmp_path,
    )

    channel_index = argv.index("--channel")
    assert argv[channel_index + 1] == "neuralcast-en"
    assert "-s" not in argv


def test_read_log_tail_returns_last_lines(tmp_path) -> None:
    log_path = tmp_path / "job.log"
    log_path.write_text("one\ntwo\nthree\n", encoding="utf-8")

    assert jobs.read_log_tail(log_path, max_lines=2) == "two\nthree\n"
