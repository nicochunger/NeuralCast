"""Tests for the NeuralCast admin API models, station helper, and job manager."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from neuralcast.admin_api.app import (
    ForceArchetypeRequest,
    ScheduleGeneratorRequest,
    create_app,
    require_admin_token,
)
from neuralcast.admin_api.favorites import FavoriteStore
from neuralcast.admin_api.jobs import (
    DEFAULT_ADMIN_SCHEDULE_SEED_MODE,
    JOB_OPERATION_FORCE_ARCHETYPE,
    JOB_OPERATION_SCHEDULE_GENERATOR,
    JobConflictError,
    JobManager,
    JobRecord,
    SUPPORTED_ARCHETYPES,
    SUPPORTED_SCHEDULE_SEED_MODES,
    SUPPORTED_STATIONS,
    SUPPORTED_SCHEDULE_TUNING_FIELDS,
    SUPPORTED_TRACK_FOCUS_ARCHETYPES,
    build_force_archetype_command,
    build_schedule_generator_command,
    save_job_record,
)
from neuralcast.admin_api.stations import AdminStationService


class FakeAzuraCastClient:
    def __init__(self) -> None:
        self.now_playing_payload = {
            "listeners": {"current": 7},
            "now_playing": {
                "remaining": 142,
                "song": {
                    "id": 11,
                    "artist": "Amorphis",
                    "title": "Black Winter Day",
                    "length": 244,
                },
            },
        }
        self.queue_payload = [
            {
                "id": "queue-1",
                "duration": 244,
                "song": {
                    "id": 11,
                    "artist": "Amorphis",
                    "title": "Black Winter Day",
                },
            },
            {
                "id": "queue-2",
                "duration": 215,
                "song": {
                    "id": 12,
                    "artist": "Sentenced",
                    "title": "Noose",
                },
            },
            {
                "id": "queue-3",
                "duration": 300,
                "song": {
                    "id": 13,
                    "artist": "Opeth",
                    "title": "The Moor",
                },
            },
        ]

    def get_now_playing(self, _station: str) -> dict[str, object]:
        return self.now_playing_payload

    def get_upcoming_queue(self, _station: str) -> list[dict[str, object]]:
        return self.queue_payload


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
        self.station_service = AdminStationService(
            client_factory=lambda: FakeAzuraCastClient()
        )
        self.favorite_store = FavoriteStore(
            path=self.base_dir / "favorites.json",
            lock_path=self.base_dir / "favorites.lock",
        )
        os.environ["NEURALCAST_ADMIN_HTTP_TOKEN"] = "test-token"
        self.addCleanup(os.environ.pop, "NEURALCAST_ADMIN_HTTP_TOKEN", None)

    def test_create_app_registers_expected_routes(self) -> None:
        app = create_app(
            job_manager=self.manager,
            station_service=self.station_service,
        )
        paths = {route.path for route in app.routes}
        self.assertIn("/healthz", paths)
        self.assertIn("/admin/options", paths)
        self.assertIn("/admin/capabilities", paths)
        self.assertIn("/admin/stations/{station}/now-playing", paths)
        self.assertIn("/admin/stations/{station}/queue", paths)
        self.assertIn("/admin/stations/{station}/schedule-presentation", paths)
        self.assertIn("/admin/force-archetype", paths)
        self.assertIn("/admin/run-schedule-generator", paths)
        self.assertIn("/admin/jobs/{job_id}", paths)
        self.assertIn("/admin/favorites", paths)

    def test_http_favorites_endpoint_persists_authenticated_admin_favorites(self) -> None:
        client = TestClient(
            create_app(
                job_manager=self.manager,
                station_service=self.station_service,
                favorite_store=self.favorite_store,
            )
        )
        headers = {"Authorization": "Bearer test-token"}

        unauthenticated = client.get("/admin/favorites")
        self.assertEqual(unauthenticated.status_code, 401)

        empty = client.get("/admin/favorites", headers=headers)
        self.assertEqual(empty.status_code, 200)
        self.assertEqual(empty.json(), {"favorites": [], "exists": False})

        saved = client.put(
            "/admin/favorites",
            headers=headers,
            json={
                "favorites": [
                    {
                        "id": "neuralcast:artist|song",
                        "stationId": "neuralcast",
                        "likedAt": 1760000000000,
                        "artist": "Artist",
                        "title": "Song",
                    }
                ]
            },
        )
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.json()["favorites"][0]["stationId"], "neuralcast")
        self.assertTrue(self.favorite_store.path.exists())

        loaded = client.get("/admin/favorites", headers=headers)
        self.assertEqual(loaded.status_code, 200)
        self.assertTrue(loaded.json()["exists"])
        self.assertEqual(loaded.json()["favorites"][0]["title"], "Song")

        invalid = client.put(
            "/admin/favorites",
            headers=headers,
            json={"favorites": [{"id": "bad", "stationId": "unknown", "likedAt": 1, "title": "Song"}]},
        )
        self.assertEqual(invalid.status_code, 422)

    def test_http_endpoints_enforce_auth_and_return_station_payloads(self) -> None:
        client = TestClient(
            create_app(
                job_manager=self.manager,
                station_service=self.station_service,
            )
        )

        unauthenticated = client.get("/admin/options")
        self.assertEqual(unauthenticated.status_code, 401)

        headers = {"Authorization": "Bearer test-token"}
        options = client.get("/admin/options", headers=headers)
        self.assertEqual(options.status_code, 200)
        self.assertEqual(options.json()["stations"], ["neuralcast", "neuralforge"])

        now_playing = client.get(
            "/admin/stations/neuralforge/now-playing",
            headers=headers,
        )
        self.assertEqual(now_playing.status_code, 200)
        self.assertEqual(
            now_playing.json()["current_track"]["title"],
            "Black Winter Day",
        )

        queue = client.get(
            "/admin/stations/neuralforge/queue?limit=1",
            headers=headers,
        )
        self.assertEqual(queue.status_code, 200)
        self.assertEqual(queue.json()["next_track"]["title"], "Noose")
        self.assertEqual(len(queue.json()["items"]), 1)

    def test_schedule_presentation_endpoint_returns_persisted_copy(self) -> None:
        presentation = {
            "version": 1,
            "plan_hash": "plan-1",
            "generated_at_utc": "2026-07-30T12:00:00+00:00",
            "blocks": [
                {
                    "key": "ids:37,28",
                    "playlist_ids": ["28", "37"],
                    "playlist_names": ["Folk Metal", "Folk Rock"],
                    "kind": "combo",
                    "translations": {"en": {"title": "Folk & Fire", "description": "Rooted melodies meet thunderous metal intensity."}},
                }
            ],
        }
        client = TestClient(
            create_app(
                job_manager=self.manager,
                station_service=self.station_service,
                schedule_presentation_loader=lambda station: presentation if station == "neuralforge" else None,
            )
        )
        headers = {"Authorization": "Bearer test-token"}

        response = client.get(
            "/admin/stations/neuralforge/schedule-presentation", headers=headers
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["blocks"][0]["translations"]["en"]["title"], "Folk & Fire")

    def test_http_force_archetype_job_lifecycle_and_conflict(self) -> None:
        client = TestClient(
            create_app(
                job_manager=self.manager,
                station_service=self.station_service,
            )
        )
        headers = {"Authorization": "Bearer test-token"}

        accepted = client.post(
            "/admin/force-archetype",
            headers=headers,
            json={
                "station": "neuralforge",
                "archetype": "deep_dive",
                "track_focus": "next",
                "dry_run": True,
            },
        )
        self.assertEqual(accepted.status_code, 202)
        job_id = accepted.json()["job_id"]

        conflict = client.post(
            "/admin/run-schedule-generator",
            headers=headers,
            json={"station": "neuralforge", "dry_run": True},
        )
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.json()["detail"]["job_id"], job_id)

        status_payload = client.get(f"/admin/jobs/{job_id}", headers=headers)
        self.assertEqual(status_payload.status_code, 200)
        self.assertEqual(
            status_payload.json()["operation"],
            JOB_OPERATION_FORCE_ARCHETYPE,
        )

        missing = client.get("/admin/jobs/not-real", headers=headers)
        self.assertEqual(missing.status_code, 404)

    def test_http_schedule_generator_accepts_tuning_payload(self) -> None:
        client = TestClient(
            create_app(
                job_manager=self.manager,
                station_service=self.station_service,
            )
        )
        headers = {"Authorization": "Bearer test-token"}

        accepted = client.post(
            "/admin/run-schedule-generator",
            headers=headers,
            json={
                "station": "neuralcast",
                "dry_run": True,
                "force_apply": True,
                "seed_mode": "custom",
                "seed_salt": "reroll-a",
                "week_start_date": "2026-03-16",
                "open_ratio_min": 0.3,
                "open_ratio_max": 0.45,
            },
        )

        self.assertEqual(accepted.status_code, 202)
        status_payload = client.get(
            f"/admin/jobs/{accepted.json()['job_id']}",
            headers=headers,
        )
        self.assertEqual(status_payload.status_code, 200)
        payload = status_payload.json()
        self.assertEqual(payload["operation"], JOB_OPERATION_SCHEDULE_GENERATOR)
        self.assertEqual(payload["schedule_options"]["seed_mode"], "custom")

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
            track_focus="next",
            dry_run=True,
        )
        self.assertEqual(request.station, "neuralforge")
        self.assertEqual(request.archetype, "deep_dive")
        self.assertEqual(request.track_focus, "next")
        self.assertTrue(request.dry_run)

        with self.assertRaises(ValidationError):
            ForceArchetypeRequest(station="bad-station", archetype="deep_dive")
        with self.assertRaises(ValidationError):
            ForceArchetypeRequest(station="neuralcast", archetype="not-real")
        album_spotlight_request = ForceArchetypeRequest(
            station="neuralcast",
            archetype="album_spotlight",
            track_focus="current",
        )
        self.assertEqual(album_spotlight_request.track_focus, "current")
        with self.assertRaises(ValidationError):
            ForceArchetypeRequest(
                station="neuralcast",
                archetype="back_sell",
                track_focus="current",
            )

    def test_schedule_generator_request_validation_uses_supported_values(self) -> None:
        request = ScheduleGeneratorRequest(
            station="neuralforge",
            dry_run=True,
            force_apply=True,
            seed_mode="custom",
            seed_salt="reroll-a",
            week_start_date="2026-03-16",
            open_ratio_min=0.2,
            open_ratio_max=0.5,
            min_open_slots=1,
            max_open_slots=3,
            min_block_minutes=60,
            max_block_minutes=180,
        )
        self.assertEqual(request.station, "neuralforge")
        self.assertTrue(request.dry_run)
        self.assertTrue(request.force_apply)
        self.assertEqual(request.seed_mode, "custom")
        self.assertEqual(request.seed_salt, "reroll-a")

        with self.assertRaises(ValidationError):
            ScheduleGeneratorRequest(station="bad-station")
        with self.assertRaises(ValidationError):
            ScheduleGeneratorRequest(station="neuralforge", seed_mode="custom")
        with self.assertRaises(ValidationError):
            ScheduleGeneratorRequest(
                station="neuralforge",
                seed_mode="stable_week",
                seed_salt="not-allowed",
            )

    def test_supported_allowlists_match_phase_two_contract(self) -> None:
        self.assertEqual(list(SUPPORTED_STATIONS), ["neuralcast", "neuralforge"])
        self.assertIn("deep_dive", SUPPORTED_ARCHETYPES)
        self.assertIn("album_spotlight", SUPPORTED_TRACK_FOCUS_ARCHETYPES)
        self.assertIn("short_story", SUPPORTED_TRACK_FOCUS_ARCHETYPES)

    def test_capabilities_payload_includes_schedule_generator(self) -> None:
        capabilities = self.manager.capabilities()
        self.assertEqual(capabilities["stations"], ["neuralcast", "neuralforge"])
        self.assertIn("deep_dive", capabilities["archetypes"])
        self.assertIn("album_spotlight", capabilities["track_focus_archetypes"])
        self.assertIn(JOB_OPERATION_SCHEDULE_GENERATOR, capabilities["operations"])
        self.assertTrue(
            capabilities["operations"][JOB_OPERATION_SCHEDULE_GENERATOR][
                "dry_run_supported"
            ]
        )
        self.assertTrue(
            capabilities["operations"][JOB_OPERATION_SCHEDULE_GENERATOR][
                "force_apply_supported"
            ]
        )
        self.assertEqual(
            capabilities["operations"][JOB_OPERATION_SCHEDULE_GENERATOR][
                "default_seed_mode"
            ],
            DEFAULT_ADMIN_SCHEDULE_SEED_MODE,
        )
        self.assertEqual(
            capabilities["operations"][JOB_OPERATION_SCHEDULE_GENERATOR][
                "supported_seed_modes"
            ],
            list(SUPPORTED_SCHEDULE_SEED_MODES),
        )
        self.assertEqual(
            capabilities["operations"][JOB_OPERATION_SCHEDULE_GENERATOR][
                "supported_tuning_fields"
            ],
            list(SUPPORTED_SCHEDULE_TUNING_FIELDS),
        )

    def test_station_service_now_playing_uses_existing_transport_parsing(self) -> None:
        payload = self.station_service.now_playing("neuralforge")
        self.assertEqual(payload["station"], "neuralforge")
        self.assertEqual(payload["current_track"]["artist"], "Amorphis")
        self.assertEqual(payload["current_track"]["title"], "Black Winter Day")
        self.assertEqual(payload["remaining_seconds"], 142)
        self.assertEqual(payload["listener_count"], 7)

    def test_station_service_queue_filters_out_current_track(self) -> None:
        payload = self.station_service.queue("neuralforge", limit=2)
        self.assertEqual(payload["station"], "neuralforge")
        self.assertEqual(payload["next_track"]["artist"], "Sentenced")
        self.assertEqual(len(payload["items"]), 2)
        self.assertEqual(payload["items"][0]["title"], "Noose")
        self.assertEqual(payload["items"][1]["title"], "The Moor")

    def test_enqueue_force_archetype_persists_job(self) -> None:
        job = self.manager.enqueue_force_archetype(
            station="neuralforge",
            archetype="deep_dive",
            track_focus="next",
            dry_run=True,
        )

        self.assertEqual(job.operation, JOB_OPERATION_FORCE_ARCHETYPE)
        self.assertEqual(job.station, "neuralforge")
        self.assertEqual(job.archetype, "deep_dive")
        self.assertEqual(job.track_focus, "next")
        self.assertTrue(job.dry_run)
        self.assertEqual(job.status, "running")
        self.assertEqual(job.runner_pid, 4242)
        self.assertTrue(self.manager.job_path(job.job_id).exists())

    def test_enqueue_schedule_generator_persists_job(self) -> None:
        job = self.manager.enqueue_schedule_generator(
            station="neuralforge",
            dry_run=True,
            force_apply=True,
            seed_mode="fresh",
            week_start_date="2026-03-16",
            open_ratio_min=0.2,
            open_ratio_max=0.5,
        )

        self.assertEqual(job.operation, JOB_OPERATION_SCHEDULE_GENERATOR)
        self.assertEqual(job.station, "neuralforge")
        self.assertIsNone(job.archetype)
        self.assertIsNone(job.track_focus)
        self.assertTrue(job.dry_run)
        self.assertEqual(job.status, "running")
        self.assertEqual(job.runner_pid, 4242)
        self.assertEqual(job.schedule_options["seed_mode"], "fresh")
        self.assertEqual(job.schedule_options["week_start_date"], "2026-03-16")
        self.assertTrue(job.schedule_options["force_apply"])
        self.assertIn("seed_salt", job.schedule_options)

    def test_enqueue_job_rejects_second_running_job_for_station(self) -> None:
        first = self.manager.enqueue_force_archetype(
            station="neuralcast",
            archetype="back_sell",
            track_focus=None,
            dry_run=False,
        )

        with self.assertRaises(JobConflictError) as exc:
            self.manager.enqueue_schedule_generator(
                station="neuralcast",
                dry_run=True,
            )

        self.assertEqual(exc.exception.job_id, first.job_id)

    def test_job_status_payload_returns_log_tail_and_operation(self) -> None:
        job = JobRecord(
            job_id="20260314T153012Z-neuralforge-deep_dive",
            operation=JOB_OPERATION_FORCE_ARCHETYPE,
            station="neuralforge",
            archetype="deep_dive",
            track_focus="current",
            dry_run=False,
            status="succeeded",
            accepted_at="2026-03-14T15:30:12Z",
            started_at="2026-03-14T15:30:13Z",
            finished_at="2026-03-14T15:30:25Z",
            exit_code=0,
            schedule_options=None,
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
        self.assertEqual(payload["operation"], JOB_OPERATION_FORCE_ARCHETYPE)
        self.assertEqual(payload["exit_code"], 0)
        self.assertEqual(payload["status"], "succeeded")
        self.assertEqual(payload["track_focus"], "current")
        self.assertIn("last useful line", payload["log_tail"])
        self.assertEqual(payload["log_path"], str(log_path))
        self.assertIsNone(payload["schedule_options"])

    def test_backward_compatible_job_load_defaults_operation(self) -> None:
        legacy_path = self.manager.job_path("20260314T153012Z-neuralforge-deep_dive")
        legacy_path.write_text(
            json.dumps(
                {
                    "job_id": "20260314T153012Z-neuralforge-deep_dive",
                    "station": "neuralforge",
                    "archetype": "deep_dive",
                    "track_focus": "next",
                    "dry_run": True,
                    "status": "running",
                    "accepted_at": "2026-03-14T15:30:12Z",
                    "started_at": None,
                    "finished_at": None,
                    "exit_code": None,
                    "schedule_options": None,
                    "log_path": str(
                        self.manager.log_path("20260314T153012Z-neuralforge-deep_dive")
                    ),
                    "runner_pid": 5151,
                }
            ),
            encoding="utf-8",
        )

        legacy_manager = JobManager(
            base_dir=self.base_dir,
            runner_launcher=lambda _job_path: 4242,
            process_checker=lambda _pid: True,
        )
        job = legacy_manager.get_job("20260314T153012Z-neuralforge-deep_dive")
        self.assertEqual(job.operation, JOB_OPERATION_FORCE_ARCHETYPE)
        self.assertEqual(job.archetype, "deep_dive")

    def test_stale_running_job_is_marked_failed(self) -> None:
        stale_manager = JobManager(
            base_dir=self.base_dir / "stale",
            runner_launcher=lambda _job_path: 5151,
            process_checker=lambda _pid: False,
        )
        stale_job = JobRecord(
            job_id="20260314T153012Z-neuralcast-schedule_generator",
            operation=JOB_OPERATION_SCHEDULE_GENERATOR,
            station="neuralcast",
            archetype=None,
            track_focus=None,
            dry_run=True,
            status="running",
            accepted_at="2026-03-14T15:30:12Z",
            started_at="2026-03-14T15:30:13Z",
            finished_at=None,
            exit_code=None,
            schedule_options={"seed_mode": "fresh", "seed_salt": "reroll-a"},
            log_path=str(stale_manager.log_path("20260314T153012Z-neuralcast-schedule_generator")),
            runner_pid=5151,
        )
        save_job_record(stale_manager.job_path(stale_job.job_id), stale_job)

        refreshed = stale_manager.get_job(stale_job.job_id)
        self.assertEqual(refreshed.status, "failed")
        self.assertIsNotNone(refreshed.finished_at)

    def test_build_force_archetype_command_uses_existing_cli_shape(self) -> None:
        argv, env, cwd = build_force_archetype_command(
            station="neuralforge",
            archetype="deep_dive",
            track_focus="next",
            dry_run=True,
            project_root=self.base_dir,
        )

        self.assertEqual(
            argv[1:9],
            [
                "-m",
                "neuralcast.cli.host_orchestrator",
                "-s",
                "neuralforge",
                "--force-archetype",
                "deep_dive",
                "--force-track-focus",
                "next",
            ],
        )
        self.assertEqual(argv[-1], "--dry-run")
        self.assertEqual(cwd, self.base_dir)
        self.assertTrue(
            env["PYTHONPATH"].startswith(str((self.base_dir / "src").resolve()))
        )

    def test_build_schedule_generator_command_uses_existing_cli_shape(self) -> None:
        argv, env, cwd = build_schedule_generator_command(
            station="neuralforge",
            dry_run=True,
            force_apply=True,
            week_start_date="2026-03-16",
            seed_mode="fresh",
            seed_salt="reroll-a",
            open_ratio_min=0.2,
            open_ratio_max=0.5,
            min_open_slots=1,
            max_open_slots=3,
            min_block_minutes=60,
            max_block_minutes=180,
            project_root=self.base_dir,
        )

        self.assertEqual(
            argv[1:5],
            [
                "-m",
                "neuralcast.cli.schedule_generator",
                "-s",
                "neuralforge",
            ],
        )
        self.assertIn("--dry-run", argv)
        self.assertIn("--force-apply", argv)
        self.assertIn("--week-start-date", argv)
        self.assertIn("2026-03-16", argv)
        self.assertIn("--seed-mode", argv)
        self.assertIn("fresh", argv)
        self.assertIn("--seed-salt", argv)
        self.assertIn("reroll-a", argv)
        self.assertIn("--open-ratio-min", argv)
        self.assertIn("0.2", argv)
        self.assertIn("--max-block-minutes", argv)
        self.assertIn("180", argv)
        self.assertEqual(cwd, self.base_dir)
        self.assertTrue(
            env["PYTHONPATH"].startswith(str((self.base_dir / "src").resolve()))
        )


if __name__ == "__main__":
    unittest.main()
