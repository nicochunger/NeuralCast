"""Tests for the NeuralCast admin API models, auth, and job manager."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from fastapi import HTTPException
from pydantic import ValidationError

from neuralcast.admin_api.app import (
    ForceArchetypeRequest,
    create_app,
    require_admin_token,
)
from neuralcast.admin_api.jobs import (
    JobConflictError,
    JobManager,
    JobRecord,
    SUPPORTED_ARCHETYPES,
    SUPPORTED_STATIONS,
    build_orchestrator_command,
    save_job_record,
)


class AdminApiUnitTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.base_dir = Path(self.temp_dir.name)
        self.manager = JobManager(
            base_dir=self.base_dir,
            runner_launcher=lambda _job_path: 4242,
            process_checker=lambda _pid: True,
        )
        os.environ["NEURALCAST_ADMIN_HTTP_TOKEN"] = "test-token"
        self.addCleanup(os.environ.pop, "NEURALCAST_ADMIN_HTTP_TOKEN", None)

    def test_create_app_registers_expected_routes(self) -> None:
        app = create_app(job_manager=self.manager)
        paths = {route.path for route in app.routes}
        self.assertIn("/healthz", paths)
        self.assertIn("/admin/options", paths)
        self.assertIn("/admin/force-archetype", paths)
        self.assertIn("/admin/jobs/{job_id}", paths)

    def test_require_admin_token_accepts_and_rejects_expected_values(self) -> None:
        require_admin_token("Bearer test-token")

        with self.assertRaises(HTTPException) as missing_header:
            require_admin_token(None)
        self.assertEqual(missing_header.exception.status_code, 401)

        with self.assertRaises(HTTPException) as bad_token:
            require_admin_token("Bearer wrong-token")
        self.assertEqual(bad_token.exception.status_code, 401)

    def test_force_archetype_request_validation_uses_supported_values(self) -> None:
        request = ForceArchetypeRequest(
            station="neuralforge",
            archetype="deep_dive",
            dry_run=True,
        )
        self.assertEqual(request.station, "neuralforge")
        self.assertEqual(request.archetype, "deep_dive")
        self.assertTrue(request.dry_run)

        with self.assertRaises(ValidationError):
            ForceArchetypeRequest(station="bad-station", archetype="deep_dive")
        with self.assertRaises(ValidationError):
            ForceArchetypeRequest(station="neuralcast", archetype="not-real")

    def test_supported_allowlists_match_phase_one_contract(self) -> None:
        self.assertEqual(list(SUPPORTED_STATIONS), ["neuralcast", "neuralforge"])
        self.assertIn("deep_dive", SUPPORTED_ARCHETYPES)
        self.assertIn("back_sell", SUPPORTED_ARCHETYPES)

    def test_enqueue_force_archetype_persists_job(self) -> None:
        job = self.manager.enqueue_force_archetype(
            station="neuralforge",
            archetype="deep_dive",
            dry_run=True,
        )

        self.assertEqual(job.station, "neuralforge")
        self.assertEqual(job.archetype, "deep_dive")
        self.assertTrue(job.dry_run)
        self.assertEqual(job.status, "running")
        self.assertEqual(job.runner_pid, 4242)
        self.assertTrue(self.manager.job_path(job.job_id).exists())

    def test_enqueue_force_archetype_rejects_second_running_job_for_station(self) -> None:
        first = self.manager.enqueue_force_archetype(
            station="neuralcast",
            archetype="back_sell",
            dry_run=False,
        )

        with self.assertRaises(JobConflictError) as exc:
            self.manager.enqueue_force_archetype(
                station="neuralcast",
                archetype="deep_dive",
                dry_run=True,
            )

        self.assertEqual(exc.exception.job_id, first.job_id)

    def test_job_status_payload_returns_log_tail(self) -> None:
        job = JobRecord(
            job_id="20260314T153012Z-neuralforge-deep_dive",
            station="neuralforge",
            archetype="deep_dive",
            dry_run=False,
            status="succeeded",
            accepted_at="2026-03-14T15:30:12Z",
            started_at="2026-03-14T15:30:13Z",
            finished_at="2026-03-14T15:30:25Z",
            exit_code=0,
            log_path=str(self.manager.log_path("20260314T153012Z-neuralforge-deep_dive")),
            runner_pid=4242,
            orchestrator_pid=5252,
        )
        save_job_record(self.manager.job_path(job.job_id), job)
        log_path = Path(job.log_path)
        log_path.write_text(
            "line 1\nline 2\nlast useful line\n",
            encoding="utf-8",
        )

        payload = self.manager.job_status_payload(job.job_id)
        self.assertEqual(payload["exit_code"], 0)
        self.assertEqual(payload["status"], "succeeded")
        self.assertIn("last useful line", payload["log_tail"])
        self.assertEqual(payload["log_path"], str(log_path))

    def test_stale_running_job_is_marked_failed(self) -> None:
        stale_manager = JobManager(
            base_dir=self.base_dir / "stale",
            runner_launcher=lambda _job_path: 5151,
            process_checker=lambda _pid: False,
        )
        stale_job = JobRecord(
            job_id="20260314T153012Z-neuralcast-back_sell",
            station="neuralcast",
            archetype="back_sell",
            dry_run=True,
            status="running",
            accepted_at="2026-03-14T15:30:12Z",
            started_at="2026-03-14T15:30:13Z",
            finished_at=None,
            exit_code=None,
            log_path=str(stale_manager.log_path("20260314T153012Z-neuralcast-back_sell")),
            runner_pid=5151,
        )
        save_job_record(stale_manager.job_path(stale_job.job_id), stale_job)

        refreshed = stale_manager.get_job(stale_job.job_id)
        self.assertEqual(refreshed.status, "failed")
        self.assertIsNotNone(refreshed.finished_at)

    def test_build_orchestrator_command_uses_existing_cli_shape(self) -> None:
        argv, env, cwd = build_orchestrator_command(
            station="neuralforge",
            archetype="deep_dive",
            dry_run=True,
            project_root=self.base_dir,
        )

        self.assertEqual(
            argv[1:7],
            [
                "-m",
                "neuralcast.cli.host_orchestrator",
                "-s",
                "neuralforge",
                "--force-archetype",
                "deep_dive",
            ],
        )
        self.assertEqual(argv[-1], "--dry-run")
        self.assertEqual(cwd, self.base_dir)
        self.assertTrue(
            env["PYTHONPATH"].startswith(str((self.base_dir / "src").resolve()))
        )


if __name__ == "__main__":
    unittest.main()
