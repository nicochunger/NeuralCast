"""Unit tests for the detached admin job runner."""

from __future__ import annotations

from neuralcast.admin_api import jobs, runner


class FakeProcess:
    pid = 1234

    def wait(self) -> int:
        return 0


def test_run_job_marks_success_and_records_command(tmp_path, monkeypatch) -> None:
    job = jobs.JobRecord(
        job_id="job-1",
        operation=jobs.JOB_OPERATION_FORCE_ARCHETYPE,
        station="neuralforge",
        archetype="back_sell",
        track_focus=None,
        dry_run=True,
        status="accepted",
        accepted_at="2026-03-14T15:30:12Z",
        started_at=None,
        finished_at=None,
        exit_code=None,
        schedule_options=None,
        log_path=str(tmp_path / "job.log"),
        runner_pid=None,
    )
    job_path = tmp_path / "job.json"
    jobs.save_job_record(job_path, job)
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *_args, **_kwargs: FakeProcess())

    assert runner.run_job(job_path) == 0

    saved = jobs.load_job_record(job_path)
    assert saved.status == "succeeded"
    assert saved.exit_code == 0
    assert saved.orchestrator_pid == 1234
    assert saved.command is not None
