"""Disk-backed job management for the NeuralCast admin HTTP API."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from neuralcast.config import ALLOWED_STATION_SLUGS, PROJECT_ROOT
from neuralcast.pipelines.host_orchestrator.models import (
    Archetype,
    TrackFocus,
    supports_track_focus,
)

EXPECTED_SUPPORTED_STATIONS = ("neuralcast", "neuralforge")
SUPPORTED_STATIONS = tuple(ALLOWED_STATION_SLUGS)
if SUPPORTED_STATIONS != EXPECTED_SUPPORTED_STATIONS:
    raise RuntimeError(
        "Admin API station allowlist drifted from the expected "
        f"{EXPECTED_SUPPORTED_STATIONS!r}: {SUPPORTED_STATIONS!r}"
    )

SUPPORTED_ARCHETYPES = tuple(archetype.value for archetype in Archetype)
SUPPORTED_TRACK_FOCUSES = tuple(focus.value for focus in TrackFocus)
SUPPORTED_TRACK_FOCUS_ARCHETYPES = tuple(
    archetype.value for archetype in Archetype if supports_track_focus(archetype)
)
JOB_OPERATION_FORCE_ARCHETYPE = "force_archetype"
JOB_OPERATION_SCHEDULE_GENERATOR = "schedule_generator"
SUPPORTED_JOB_OPERATIONS = (
    JOB_OPERATION_FORCE_ARCHETYPE,
    JOB_OPERATION_SCHEDULE_GENERATOR,
)
DEFAULT_ADMIN_HTTP_HOST = "127.0.0.1"
DEFAULT_ADMIN_HTTP_PORT = 8787
ADMIN_HTTP_ROOT = PROJECT_ROOT / "admin_http"
ADMIN_HTTP_JOBS_DIR = ADMIN_HTTP_ROOT / "jobs"
ADMIN_HTTP_LOGS_DIR = ADMIN_HTTP_ROOT / "logs"
LOG_TAIL_LINES = 20
LOG_TAIL_CHARS = 4000


class JobConflictError(RuntimeError):
    """Raised when a station already has a running admin job."""

    def __init__(self, station: str, job_id: str) -> None:
        super().__init__(
            f"Station '{station}' already has a running admin job: {job_id}."
        )
        self.station = station
        self.job_id = job_id


class JobNotFoundError(FileNotFoundError):
    """Raised when a requested admin job record does not exist."""

    def __init__(self, job_id: str) -> None:
        super().__init__(f"Admin job '{job_id}' was not found.")
        self.job_id = job_id


@dataclass
class JobRecord:
    """Persisted state for one admin-triggered orchestrator run."""

    job_id: str
    operation: str
    station: str
    archetype: str | None
    track_focus: str | None
    dry_run: bool
    status: str
    accepted_at: str
    started_at: str | None
    finished_at: str | None
    exit_code: int | None
    log_path: str
    runner_pid: int | None = None
    orchestrator_pid: int | None = None
    command: list[str] | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "JobRecord":
        return cls(
            job_id=str(payload["job_id"]),
            operation=str(payload.get("operation") or JOB_OPERATION_FORCE_ARCHETYPE),
            station=str(payload["station"]),
            archetype=_optional_str(payload.get("archetype")),
            track_focus=_optional_str(payload.get("track_focus")),
            dry_run=bool(payload["dry_run"]),
            status=str(payload["status"]),
            accepted_at=str(payload["accepted_at"]),
            started_at=_optional_str(payload.get("started_at")),
            finished_at=_optional_str(payload.get("finished_at")),
            exit_code=_optional_int(payload.get("exit_code")),
            log_path=str(payload["log_path"]),
            runner_pid=_optional_int(payload.get("runner_pid")),
            orchestrator_pid=_optional_int(payload.get("orchestrator_pid")),
            command=_optional_str_list(payload.get("command")),
        )


RunnerLauncher = Callable[[Path], int]
ProcessChecker = Callable[[int], bool]


def utc_now_iso() -> str:
    """Return the current UTC timestamp formatted for job records."""

    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def build_job_id(station: str, label: str, *, now: datetime | None = None) -> str:
    """Build a stable, human-readable job identifier."""

    timestamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    return f"{timestamp}-{station}-{label}"


def build_force_archetype_command(
    station: str,
    archetype: str,
    track_focus: str | None,
    dry_run: bool,
    *,
    project_root: Path = PROJECT_ROOT,
) -> tuple[list[str], dict[str, str], Path]:
    """Return the argv, environment, and cwd for the real host-orchestrator CLI."""

    python_executable = project_root / "venv" / "bin" / "python"
    if not python_executable.exists():
        python_executable = Path(sys.executable)

    argv = [
        str(python_executable),
        "-m",
        "neuralcast.cli.host_orchestrator",
        "-s",
        station,
        "--force-archetype",
        archetype,
    ]
    if track_focus:
        argv.extend(["--force-track-focus", track_focus])
    if dry_run:
        argv.append("--dry-run")

    env = os.environ.copy()
    src_path = str((project_root / "src").resolve())
    current_pythonpath = env.get("PYTHONPATH", "").strip()
    env["PYTHONPATH"] = (
        f"{src_path}:{current_pythonpath}" if current_pythonpath else src_path
    )
    return argv, env, project_root


def build_schedule_generator_command(
    station: str,
    dry_run: bool,
    *,
    project_root: Path = PROJECT_ROOT,
) -> tuple[list[str], dict[str, str], Path]:
    """Return the argv, environment, and cwd for the real schedule-generator CLI."""

    python_executable = project_root / "venv" / "bin" / "python"
    if not python_executable.exists():
        python_executable = Path(sys.executable)

    argv = [
        str(python_executable),
        "-m",
        "neuralcast.cli.schedule_generator",
        "-s",
        station,
    ]
    if dry_run:
        argv.append("--dry-run")

    env = os.environ.copy()
    src_path = str((project_root / "src").resolve())
    current_pythonpath = env.get("PYTHONPATH", "").strip()
    env["PYTHONPATH"] = (
        f"{src_path}:{current_pythonpath}" if current_pythonpath else src_path
    )
    return argv, env, project_root


def build_job_command(
    job: JobRecord,
    *,
    project_root: Path = PROJECT_ROOT,
) -> tuple[list[str], dict[str, str], Path]:
    """Return the argv, environment, and cwd for one persisted admin job."""

    if job.operation == JOB_OPERATION_FORCE_ARCHETYPE:
        if not job.archetype:
            raise RuntimeError(
                f"Admin job '{job.job_id}' is missing an archetype for force_archetype."
            )
        return build_force_archetype_command(
            station=job.station,
            archetype=job.archetype,
            track_focus=job.track_focus,
            dry_run=job.dry_run,
            project_root=project_root,
        )

    if job.operation == JOB_OPERATION_SCHEDULE_GENERATOR:
        return build_schedule_generator_command(
            station=job.station,
            dry_run=job.dry_run,
            project_root=project_root,
        )

    raise RuntimeError(
        f"Admin job '{job.job_id}' has unsupported operation '{job.operation}'."
    )


def read_log_tail(
    log_path: Path,
    *,
    max_lines: int = LOG_TAIL_LINES,
    max_chars: int = LOG_TAIL_CHARS,
) -> str:
    """Read a short tail of a job log file for status responses."""

    if not log_path.exists():
        return ""

    with log_path.open("r", encoding="utf-8", errors="replace") as handle:
        lines = handle.readlines()

    tail = "".join(lines[-max_lines:])
    if len(tail) <= max_chars:
        return tail
    return tail[-max_chars:]


def is_process_alive(pid: int) -> bool:
    """Best-effort process liveness check for persisted runner PIDs."""

    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def load_job_record(job_path: Path) -> JobRecord:
    """Load one persisted job record from disk."""

    with job_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    return JobRecord.from_dict(payload)


def save_job_record(job_path: Path, job: JobRecord) -> None:
    """Persist a job record atomically."""

    job_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = job_path.with_suffix(f"{job_path.suffix}.tmp")
    with temp_path.open("w", encoding="utf-8") as handle:
        json.dump(job.to_dict(), handle, indent=2, sort_keys=True)
        handle.write("\n")
    temp_path.replace(job_path)


def append_job_log(log_path: Path, message: str) -> None:
    """Append one line to a job log file."""

    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(message.rstrip("\n"))
        handle.write("\n")


def runner_module_argv(job_path: Path) -> list[str]:
    """Build the detached runner argv used by the HTTP service."""

    return [
        sys.executable,
        "-m",
        "neuralcast.admin_api.runner",
        "--job-file",
        str(job_path),
    ]


def launch_runner_process(job_path: Path) -> int:
    """Launch the detached runner that executes and tracks one orchestrator job."""

    process = subprocess.Popen(  # noqa: S603
        runner_module_argv(job_path),
        cwd=str(PROJECT_ROOT),
        env=build_runner_environment(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return int(process.pid)


def build_runner_environment() -> dict[str, str]:
    """Prepare the environment used for the detached runner module."""

    env = os.environ.copy()
    src_path = str((PROJECT_ROOT / "src").resolve())
    current_pythonpath = env.get("PYTHONPATH", "").strip()
    env["PYTHONPATH"] = (
        f"{src_path}:{current_pythonpath}" if current_pythonpath else src_path
    )
    return env


class JobManager:
    """Manage persisted admin API jobs and spawn background runner processes."""

    def __init__(
        self,
        *,
        base_dir: Path = ADMIN_HTTP_ROOT,
        runner_launcher: RunnerLauncher | None = None,
        process_checker: ProcessChecker | None = None,
    ) -> None:
        self.base_dir = base_dir
        self.jobs_dir = base_dir / "jobs"
        self.logs_dir = base_dir / "logs"
        self._runner_launcher = runner_launcher or launch_runner_process
        self._process_checker = process_checker or is_process_alive
        self._lock = threading.Lock()
        self.ensure_directories()

    def ensure_directories(self) -> None:
        """Create the admin API state directories if they do not exist."""

        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)

    def job_path(self, job_id: str) -> Path:
        """Return the persisted JSON path for one job."""

        return self.jobs_dir / f"{job_id}.json"

    def log_path(self, job_id: str) -> Path:
        """Return the log path for one job."""

        return self.logs_dir / f"{job_id}.log"

    def options(self) -> dict[str, list[str]]:
        """Return the admin API allowlist exposed by `/admin/options`."""

        return {
            "stations": list(SUPPORTED_STATIONS),
            "archetypes": list(SUPPORTED_ARCHETYPES),
        }

    def capabilities(self) -> dict[str, object]:
        """Return the richer admin API capability payload."""

        return {
            "stations": list(SUPPORTED_STATIONS),
            "archetypes": list(SUPPORTED_ARCHETYPES),
            "track_focus_values": list(SUPPORTED_TRACK_FOCUSES),
            "track_focus_archetypes": list(SUPPORTED_TRACK_FOCUS_ARCHETYPES),
            "operations": {
                JOB_OPERATION_FORCE_ARCHETYPE: {
                    "dry_run_supported": True,
                    "track_focus_supported": True,
                },
                JOB_OPERATION_SCHEDULE_GENERATOR: {
                    "dry_run_supported": True,
                    "track_focus_supported": False,
                },
            },
        }

    def enqueue_force_archetype(
        self,
        *,
        station: str,
        archetype: str,
        track_focus: str | None,
        dry_run: bool,
    ) -> JobRecord:
        """Create and launch a background orchestrator job."""

        return self._enqueue_job(
            operation=JOB_OPERATION_FORCE_ARCHETYPE,
            station=station,
            label=archetype,
            archetype=archetype,
            track_focus=track_focus,
            dry_run=dry_run,
        )

    def enqueue_schedule_generator(
        self,
        *,
        station: str,
        dry_run: bool,
    ) -> JobRecord:
        """Create and launch a background schedule-generator job."""

        return self._enqueue_job(
            operation=JOB_OPERATION_SCHEDULE_GENERATOR,
            station=station,
            label=JOB_OPERATION_SCHEDULE_GENERATOR,
            archetype=None,
            track_focus=None,
            dry_run=dry_run,
        )

    def _enqueue_job(
        self,
        *,
        operation: str,
        station: str,
        label: str,
        archetype: str | None,
        track_focus: str | None,
        dry_run: bool,
    ) -> JobRecord:
        """Create and launch one background admin job."""

        with self._lock:
            self._refresh_running_jobs()
            active_job = self._running_job_for_station(station)
            if active_job is not None:
                raise JobConflictError(station, active_job.job_id)

            accepted_at = utc_now_iso()
            job_id = self._next_job_id(station, label)
            job = JobRecord(
                job_id=job_id,
                operation=operation,
                station=station,
                archetype=archetype,
                track_focus=track_focus,
                dry_run=dry_run,
                status="running",
                accepted_at=accepted_at,
                started_at=None,
                finished_at=None,
                exit_code=None,
                log_path=str(self.log_path(job_id)),
            )
            job_path = self.job_path(job_id)
            save_job_record(job_path, job)

            runner_pid = self._runner_launcher(job_path)
            latest = load_job_record(job_path)
            latest.runner_pid = latest.runner_pid or runner_pid
            save_job_record(job_path, latest)
            return latest

    def get_job(self, job_id: str) -> JobRecord:
        """Return one job record after refreshing any stale running state."""

        job_path = self.job_path(job_id)
        if not job_path.exists():
            raise JobNotFoundError(job_id)

        job = load_job_record(job_path)
        refreshed = self._refresh_job(job)
        if refreshed is not None:
            job = refreshed
        return job

    def job_status_payload(self, job_id: str) -> dict[str, object]:
        """Return the external job status payload including a short log tail."""

        job = self.get_job(job_id)
        return {
            "job_id": job.job_id,
            "operation": job.operation,
            "station": job.station,
            "archetype": job.archetype,
            "track_focus": job.track_focus,
            "dry_run": job.dry_run,
            "status": job.status,
            "accepted_at": job.accepted_at,
            "started_at": job.started_at,
            "finished_at": job.finished_at,
            "exit_code": job.exit_code,
            "log_path": job.log_path,
            "log_tail": read_log_tail(Path(job.log_path)),
        }

    def _refresh_running_jobs(self) -> None:
        for job_path in sorted(self.jobs_dir.glob("*.json")):
            job = load_job_record(job_path)
            self._refresh_job(job)

    def _running_job_for_station(self, station: str) -> JobRecord | None:
        for job_path in sorted(self.jobs_dir.glob("*.json")):
            job = load_job_record(job_path)
            if job.station == station and job.status == "running":
                return job
        return None

    def _refresh_job(self, job: JobRecord) -> JobRecord | None:
        if job.status != "running":
            return None
        if job.runner_pid is None:
            return None
        if self._process_checker(job.runner_pid):
            return None

        job.status = "failed"
        job.finished_at = job.finished_at or utc_now_iso()
        save_job_record(self.job_path(job.job_id), job)
        return job

    def _next_job_id(self, station: str, label: str) -> str:
        base_job_id = build_job_id(station, label)
        if not self.job_path(base_job_id).exists():
            return base_job_id

        suffix = 2
        while True:
            candidate = f"{base_job_id}-{suffix}"
            if not self.job_path(candidate).exists():
                return candidate
            suffix += 1


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    return int(value)


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _optional_str_list(value: object) -> list[str] | None:
    if value is None:
        return None
    return [str(item) for item in value]
