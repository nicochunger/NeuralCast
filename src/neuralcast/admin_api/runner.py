"""Detached background runner for admin-triggered orchestrator jobs."""

from __future__ import annotations

import argparse
import os
import shlex
import subprocess
from pathlib import Path

from .jobs import (
    append_job_log,
    build_orchestrator_command,
    load_job_record,
    save_job_record,
    utc_now_iso,
)


def run_job(job_file: Path) -> int:
    """Execute the real host-orchestrator CLI for one persisted admin job."""

    job = load_job_record(job_file)
    log_path = Path(job.log_path)

    try:
        job.runner_pid = job.runner_pid or os.getpid()
        job.started_at = job.started_at or utc_now_iso()
        job.status = "running"
        save_job_record(job_file, job)

        argv, env, cwd = build_orchestrator_command(
            station=job.station,
            archetype=job.archetype,
            dry_run=job.dry_run,
        )
        append_job_log(log_path, f"[admin-api] job accepted at {job.accepted_at}")
        append_job_log(
            log_path,
            "[admin-api] launching command: "
            + " ".join(shlex.quote(part) for part in argv),
        )

        with log_path.open("a", encoding="utf-8") as handle:
            process = subprocess.Popen(  # noqa: S603
                argv,
                cwd=str(cwd),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=handle,
                stderr=subprocess.STDOUT,
                text=True,
            )
            job.orchestrator_pid = int(process.pid)
            job.command = argv
            save_job_record(job_file, job)

            exit_code = int(process.wait())

        append_job_log(log_path, f"[admin-api] command finished with exit code {exit_code}")
        job.exit_code = exit_code
        job.finished_at = utc_now_iso()
        job.status = "succeeded" if exit_code == 0 else "failed"
        save_job_record(job_file, job)
        return exit_code
    except Exception as exc:  # noqa: BLE001
        append_job_log(log_path, f"[admin-api] runner failure: {exc}")
        latest = load_job_record(job_file)
        latest.status = "failed"
        latest.finished_at = latest.finished_at or utc_now_iso()
        save_job_record(job_file, latest)
        return 1


def build_arg_parser() -> argparse.ArgumentParser:
    """Build the CLI parser for the detached admin job runner."""

    parser = argparse.ArgumentParser(
        description="Run one persisted NeuralCast admin API job."
    )
    parser.add_argument(
        "--job-file",
        required=True,
        type=Path,
        help="Path to the persisted job JSON file.",
    )
    return parser


def main() -> None:
    args = build_arg_parser().parse_args()
    raise SystemExit(run_job(args.job_file))


if __name__ == "__main__":
    main()
