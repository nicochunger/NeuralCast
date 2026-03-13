"""Rsync-based station media mirroring helpers."""

from __future__ import annotations

import argparse
import os
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

DEFAULT_REMOTE_HOST = "neuralvps"
DEFAULT_REMOTE_MEDIA_ROOT_TEMPLATE = (
    "/var/lib/docker/volumes/azuracast_station_data/_data/{station}/media"
)
DEFAULT_REMOTE_TIMEOUT_SECONDS = 300

ENV_REMOTE_HOST = "NC_REMOTE_SYNC_HOST"
ENV_REMOTE_USER = "NC_REMOTE_SYNC_USER"
ENV_REMOTE_PORT = "NC_REMOTE_SYNC_PORT"
ENV_REMOTE_MEDIA_ROOT = "NC_REMOTE_SYNC_MEDIA_ROOT"
ENV_REMOTE_MEDIA_ROOT_PREFIX = "NC_REMOTE_SYNC_MEDIA_ROOT_"
ENV_REMOTE_SSH_KEY = "NC_REMOTE_SYNC_SSH_KEY"
ENV_REMOTE_RSYNC_BIN = "NC_REMOTE_SYNC_RSYNC_BIN"
ENV_REMOTE_TIMEOUT_SECONDS = "NC_REMOTE_SYNC_TIMEOUT_SECONDS"


@dataclass(frozen=True)
class RemoteSyncConfig:
    station_slug: str
    local_songs_root: Path
    remote_host: str
    remote_user: str | None
    remote_port: int | None
    remote_media_root: str
    remote_ssh_key: Path | None
    remote_rsync_bin: str
    remote_extra_rsync_args: tuple[str, ...]
    delete_remote: bool
    dry_run: bool
    timeout_seconds: int


@dataclass(frozen=True)
class RemoteSyncResult:
    command: tuple[str, ...]
    changed_count: int
    deleted_count: int
    stdout: str
    stderr: str
    dry_run: bool


@dataclass(frozen=True)
class RemoteSyncRequest:
    enabled: bool = True
    remote_host: str | None = None
    remote_user: str | None = None
    remote_port: int | None = None
    remote_media_root: str | None = None
    remote_ssh_key: str | None = None
    remote_rsync_bin: str | None = None
    remote_extra_rsync_args: tuple[str, ...] = ()
    delete_remote: bool = True
    timeout_seconds: int | None = None


def add_remote_sync_args(parser: argparse.ArgumentParser) -> None:
    group = parser.add_argument_group("Remote media mirror (rsync)")
    group.add_argument(
        "--sync-remote",
        action="store_true",
        default=True,
        help=(
            "Mirror station songs to AzuraCast media via rsync after local sync "
            "(default: enabled; includes deletions by default)."
        ),
    )
    group.add_argument(
        "--no-sync-remote",
        action="store_false",
        dest="sync_remote",
        help="Disable the default remote rsync mirror step.",
    )
    group.add_argument(
        "--remote-host",
        help=f"SSH host/alias for rsync target (default: ${ENV_REMOTE_HOST} or {DEFAULT_REMOTE_HOST}).",
    )
    group.add_argument(
        "--remote-user",
        help=f"Optional SSH username (default: ${ENV_REMOTE_USER} if set).",
    )
    group.add_argument(
        "--remote-port",
        type=int,
        help=f"Optional SSH port (default: ${ENV_REMOTE_PORT} if set).",
    )
    group.add_argument(
        "--remote-media-root",
        help=(
            "Remote AzuraCast media root. Supports '{station}' placeholder "
            "(default: station-specific "
            f"${ENV_REMOTE_MEDIA_ROOT_PREFIX}<STATION_SLUG_UPPER>, "
            f"then ${ENV_REMOTE_MEDIA_ROOT}, "
            f"then {DEFAULT_REMOTE_MEDIA_ROOT_TEMPLATE})."
        ),
    )
    group.add_argument(
        "--remote-ssh-key",
        help=f"Optional SSH identity file path (default: ${ENV_REMOTE_SSH_KEY} if set).",
    )
    group.add_argument(
        "--remote-rsync-bin",
        help=f"Rsync executable to use (default: ${ENV_REMOTE_RSYNC_BIN} or rsync).",
    )
    group.add_argument(
        "--remote-extra-rsync-args",
        action="append",
        default=[],
        metavar="ARG",
        help="Additional rsync arg (repeatable).",
    )
    group.add_argument(
        "--no-remote-delete",
        action="store_true",
        help="Disable --delete for remote rsync mirror.",
    )
    group.add_argument(
        "--remote-timeout-seconds",
        type=int,
        help=(
            "Rsync I/O timeout in seconds "
            f"(default: ${ENV_REMOTE_TIMEOUT_SECONDS} or {DEFAULT_REMOTE_TIMEOUT_SECONDS})."
        ),
    )


def remote_sync_request_from_args(args: argparse.Namespace) -> RemoteSyncRequest:
    return RemoteSyncRequest(
        enabled=bool(getattr(args, "sync_remote", True)),
        remote_host=getattr(args, "remote_host", None),
        remote_user=getattr(args, "remote_user", None),
        remote_port=getattr(args, "remote_port", None),
        remote_media_root=getattr(args, "remote_media_root", None),
        remote_ssh_key=getattr(args, "remote_ssh_key", None),
        remote_rsync_bin=getattr(args, "remote_rsync_bin", None),
        remote_extra_rsync_args=tuple(getattr(args, "remote_extra_rsync_args", ()) or ()),
        delete_remote=not bool(getattr(args, "no_remote_delete", False)),
        timeout_seconds=getattr(args, "remote_timeout_seconds", None),
    )


def _parse_optional_env_int(name: str) -> int | None:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"Environment variable {name} must be an integer, got {raw!r}.") from exc


def _station_specific_env_name(prefix: str, station_slug: str) -> str:
    sanitized_slug = "".join(
        char if char.isalnum() else "_" for char in station_slug.strip().upper()
    )
    return f"{prefix}{sanitized_slug}"


def _station_specific_env_value(prefix: str, station_slug: str) -> str | None:
    env_name = _station_specific_env_name(prefix, station_slug)
    raw_value = (os.getenv(env_name) or "").strip()
    return raw_value or None


def _resolved_remote_media_root(template_or_path: str, station_slug: str) -> str:
    try:
        resolved = template_or_path.format(station=station_slug).strip()
    except (KeyError, ValueError) as exc:
        raise ValueError(
            "Invalid remote media root template; only '{station}' placeholder is supported."
        ) from exc
    if not resolved:
        raise ValueError("Resolved remote media root is empty.")
    if not resolved.startswith("/"):
        raise ValueError(
            f"Remote media root must be absolute, got {resolved!r}."
        )
    return resolved.rstrip("/")


def build_remote_sync_config(
    *,
    station_slug: str,
    local_songs_root: Path,
    dry_run: bool,
    remote_host: str | None,
    remote_user: str | None,
    remote_port: int | None,
    remote_media_root: str | None,
    remote_ssh_key: str | None,
    remote_rsync_bin: str | None,
    remote_extra_rsync_args: Sequence[str] | None,
    delete_remote: bool,
    timeout_seconds: int | None,
) -> RemoteSyncConfig:
    resolved_remote_host = (remote_host or os.getenv(ENV_REMOTE_HOST) or DEFAULT_REMOTE_HOST).strip()
    if not resolved_remote_host:
        raise ValueError("Remote host is required for remote sync.")

    resolved_remote_user = (remote_user or os.getenv(ENV_REMOTE_USER) or "").strip() or None

    resolved_remote_port = remote_port
    if resolved_remote_port is None:
        resolved_remote_port = _parse_optional_env_int(ENV_REMOTE_PORT)

    resolved_remote_root_template = (
        remote_media_root
        or _station_specific_env_value(ENV_REMOTE_MEDIA_ROOT_PREFIX, station_slug)
        or os.getenv(ENV_REMOTE_MEDIA_ROOT)
        or DEFAULT_REMOTE_MEDIA_ROOT_TEMPLATE
    )
    resolved_remote_root = _resolved_remote_media_root(
        resolved_remote_root_template, station_slug
    )

    resolved_remote_ssh_key: Path | None = None
    remote_ssh_key_value = (remote_ssh_key or os.getenv(ENV_REMOTE_SSH_KEY) or "").strip()
    if remote_ssh_key_value:
        resolved_remote_ssh_key = Path(remote_ssh_key_value).expanduser()

    resolved_rsync_bin = (remote_rsync_bin or os.getenv(ENV_REMOTE_RSYNC_BIN) or "rsync").strip()
    if not resolved_rsync_bin:
        raise ValueError("Rsync binary cannot be empty.")

    resolved_timeout = timeout_seconds
    if resolved_timeout is None:
        resolved_timeout = _parse_optional_env_int(ENV_REMOTE_TIMEOUT_SECONDS)
    if resolved_timeout is None:
        resolved_timeout = DEFAULT_REMOTE_TIMEOUT_SECONDS
    if resolved_timeout <= 0:
        raise ValueError("Remote sync timeout must be greater than zero.")

    source_root = local_songs_root.resolve()
    if not source_root.exists():
        raise RuntimeError(
            f"Local songs directory does not exist: {source_root}"
        )
    if not source_root.is_dir():
        raise RuntimeError(
            f"Local songs path is not a directory: {source_root}"
        )

    extra_args = tuple(remote_extra_rsync_args or ())

    return RemoteSyncConfig(
        station_slug=station_slug,
        local_songs_root=source_root,
        remote_host=resolved_remote_host,
        remote_user=resolved_remote_user,
        remote_port=resolved_remote_port,
        remote_media_root=resolved_remote_root,
        remote_ssh_key=resolved_remote_ssh_key,
        remote_rsync_bin=resolved_rsync_bin,
        remote_extra_rsync_args=extra_args,
        delete_remote=delete_remote,
        dry_run=dry_run,
        timeout_seconds=resolved_timeout,
    )


def _build_ssh_transport_arg(config: RemoteSyncConfig) -> str:
    parts = ["ssh", "-o", "BatchMode=yes"]
    if config.remote_port is not None:
        parts.extend(["-p", str(config.remote_port)])
    if config.remote_ssh_key is not None:
        parts.extend(["-i", str(config.remote_ssh_key)])
    return " ".join(shlex.quote(part) for part in parts)


def _build_ssh_base_command(config: RemoteSyncConfig) -> list[str]:
    target = (
        f"{config.remote_user}@{config.remote_host}"
        if config.remote_user
        else config.remote_host
    )
    command = ["ssh", "-o", "BatchMode=yes"]
    if config.remote_port is not None:
        command.extend(["-p", str(config.remote_port)])
    if config.remote_ssh_key is not None:
        command.extend(["-i", str(config.remote_ssh_key)])
    command.append(target)
    return command


def build_rsync_command(config: RemoteSyncConfig) -> list[str]:
    remote_target = (
        f"{config.remote_user}@{config.remote_host}"
        if config.remote_user
        else config.remote_host
    )
    source = f"{config.local_songs_root}/"
    destination = f"{remote_target}:{config.remote_media_root}/"

    command = [
        config.remote_rsync_bin,
        "-az",
        "--itemize-changes",
        "--exclude=AI Stories/***",
        "--exclude=.albumart/***",
        "--timeout",
        str(config.timeout_seconds),
        "-e",
        _build_ssh_transport_arg(config),
    ]
    if config.delete_remote:
        command.append("--delete")
    if config.dry_run:
        command.append("--dry-run")
    command.extend(config.remote_extra_rsync_args)
    command.extend([source, destination])
    return command


def _is_itemized_change_line(line: str) -> bool:
    if line.startswith("*deleting "):
        return True
    return len(line) > 12 and line[11] == " "


def run_remote_sync(config: RemoteSyncConfig) -> RemoteSyncResult:
    preflight = subprocess.run(
        [
            *_build_ssh_base_command(config),
            "test",
            "-d",
            config.remote_media_root,
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=max(15, config.timeout_seconds),
    )
    if preflight.returncode != 0:
        preflight_detail = (preflight.stderr or preflight.stdout or "").strip()
        raise RuntimeError(
            "Remote media root does not exist or is not accessible: "
            f"{config.remote_media_root}. "
            f"Host={config.remote_host}. {preflight_detail}"
        )

    command = build_rsync_command(config)
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
    )
    stdout = completed.stdout or ""
    stderr = completed.stderr or ""
    if completed.returncode != 0:
        detail = stderr.strip() or stdout.strip() or "Unknown rsync error."
        raise RuntimeError(f"Remote sync failed (exit {completed.returncode}): {detail}")

    changed_count = 0
    deleted_count = 0
    for raw_line in stdout.splitlines():
        line = raw_line.strip("\n")
        if not _is_itemized_change_line(line):
            continue
        changed_count += 1
        if line.startswith("*deleting "):
            deleted_count += 1

    return RemoteSyncResult(
        command=tuple(command),
        changed_count=changed_count,
        deleted_count=deleted_count,
        stdout=stdout,
        stderr=stderr,
        dry_run=config.dry_run,
    )
