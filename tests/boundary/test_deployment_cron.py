"""Boundary tests for repository-managed cron definitions."""

from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CRON_DIR = PROJECT_ROOT / "deployment" / "cron"


def _cron_entries(filename: str) -> list[tuple[tuple[str, ...], str]]:
    entries = []
    for raw_line in (CRON_DIR / filename).read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        first_field = line.split(maxsplit=1)[0] if line else ""
        if not line or line.startswith("#") or "=" in first_field:
            continue
        fields = line.split(maxsplit=6)
        assert len(fields) == 7, f"Invalid /etc/cron.d entry: {line}"
        assert fields[5] == "root"
        entries.append((tuple(fields[:5]), fields[6]))
    return entries


def _assert_entry(
    entry: tuple[tuple[str, ...], str],
    *,
    schedule: tuple[str, ...],
    invocation: str,
    log_path: str,
) -> None:
    actual_schedule, command = entry
    log_dir = str(Path(log_path).parent)

    assert actual_schedule == schedule
    assert command.startswith(
        f"cd {PROJECT_ROOT} && mkdir -p {log_dir} && "
        f"PYTHONPATH={PROJECT_ROOT}/src ./.venv/bin/python "
    )
    assert invocation in command
    assert command.endswith(f">> {log_path} 2>&1")


def test_all_repository_cron_definitions_use_berlin_time() -> None:
    for cron_path in CRON_DIR.iterdir():
        if cron_path.is_file():
            contents = cron_path.read_text(encoding="utf-8")
            assert "CRON_TZ=Europe/Berlin" in contents


def test_host_orchestrator_cron_preserves_production_configuration() -> None:
    entries = _cron_entries("neuralcast-host-orchestrator")

    assert len(entries) == 4
    _assert_entry(
        entries[0],
        schedule=("*/30", "*", "*", "*", "*"),
        invocation="-m neuralcast.cli.host_orchestrator -s neuralcast",
        log_path="runtime/logs/host_orchestrator/neuralcast/cron.log",
    )
    _assert_entry(
        entries[1],
        schedule=("*", "*", "*", "*", "*"),
        invocation=(
            "-m neuralcast.cli.host_orchestrator -s neuralcast "
            "--scheduled-block-intros-only"
        ),
        log_path="runtime/logs/host_orchestrator/neuralcast/cron.log",
    )
    _assert_entry(
        entries[2],
        schedule=("*/2", "*", "*", "*", "*"),
        invocation="-m neuralcast.cli.host_orchestrator -s neuralforge",
        log_path="runtime/logs/host_orchestrator/neuralforge/cron.log",
    )
    _assert_entry(
        entries[3],
        schedule=("1-59/2", "*", "*", "*", "*"),
        invocation="-m neuralcast.cli.host_orchestrator --channel neuralcast-en",
        log_path="runtime/logs/host_orchestrator/neuralcast-en/cron.log",
    )


def test_schedule_generator_cron_preserves_production_configuration() -> None:
    entries = _cron_entries("neuralcast-schedule-generator")

    assert len(entries) == 2
    _assert_entry(
        entries[0],
        schedule=("5", "0", "*", "*", "1"),
        invocation="-m neuralcast.cli.schedule_generator -s neuralforge",
        log_path=(
            "runtime/logs/schedule_generator/neuralforge/schedule_generator.log"
        ),
    )
    _assert_entry(
        entries[1],
        schedule=("15", "0", "*", "*", "1"),
        invocation="-m neuralcast.cli.schedule_generator -s neuralcast",
        log_path=(
            "runtime/logs/schedule_generator/neuralcast/schedule_generator.log"
        ),
    )
