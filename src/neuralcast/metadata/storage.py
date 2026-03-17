"""Helpers for station-scoped metadata files stored next to playlists."""

from __future__ import annotations

import json
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


METADATA_DIRNAME = "metadata"


@dataclass(frozen=True)
class ResolvedStationFile:
    """Resolved read/write locations for a station-scoped file."""

    read_path: Path
    write_path: Path
    used_legacy_path: bool = False


def station_metadata_dir(playlists_dir: Path) -> Path:
    return playlists_dir.parent / METADATA_DIRNAME


def resolve_station_file(
    playlists_dir: Path,
    filename: str,
    *,
    legacy_fallback: bool = True,
) -> ResolvedStationFile:
    preferred_path = station_metadata_dir(playlists_dir) / filename
    if preferred_path.exists() or not legacy_fallback:
        return ResolvedStationFile(
            read_path=preferred_path,
            write_path=preferred_path,
            used_legacy_path=False,
        )

    legacy_path = playlists_dir / filename
    if legacy_path.exists():
        return ResolvedStationFile(
            read_path=legacy_path,
            write_path=preferred_path,
            used_legacy_path=True,
        )

    return ResolvedStationFile(
        read_path=preferred_path,
        write_path=preferred_path,
        used_legacy_path=False,
    )


def log_legacy_fallback(
    resolved: ResolvedStationFile,
    filename: str,
    *,
    log_info: Callable[[str], None],
) -> None:
    if not resolved.used_legacy_path:
        return
    log_info(
        f"Using legacy metadata path {resolved.read_path} for {filename}. "
        f"It will be migrated to {METADATA_DIRNAME}/ on next write."
    )


def load_station_json_dict(
    playlists_dir: Path,
    filename: str,
    *,
    log_warning: Callable[[str], None],
    log_info: Callable[[str], None] | None = None,
    legacy_fallback: bool = True,
    warning_label: str = "metadata file",
) -> tuple[dict[str, Any], ResolvedStationFile]:
    resolved = resolve_station_file(
        playlists_dir,
        filename,
        legacy_fallback=legacy_fallback,
    )
    if resolved.used_legacy_path and log_info is not None:
        log_legacy_fallback(resolved, filename, log_info=log_info)

    path = resolved.read_path
    if not path.exists():
        return {}, resolved

    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except Exception as exc:  # noqa: BLE001
        log_warning(f"Failed reading {warning_label} {path}: {exc}")
        return {}, resolved

    if not isinstance(payload, dict):
        log_warning(f"Unexpected {warning_label} structure in {path}")
        return {}, resolved

    return payload, resolved


def load_station_entry_mapping(
    playlists_dir: Path,
    filename: str,
    *,
    log_warning: Callable[[str], None],
    log_info: Callable[[str], None] | None = None,
    legacy_fallback: bool = True,
    warning_label: str = "metadata file",
) -> tuple[dict[str, Any], ResolvedStationFile]:
    payload, resolved = load_station_json_dict(
        playlists_dir,
        filename,
        log_warning=log_warning,
        log_info=log_info,
        legacy_fallback=legacy_fallback,
        warning_label=warning_label,
    )
    entries = payload.get("entries", payload)
    if isinstance(entries, dict):
        return entries, resolved
    log_warning(f"Unexpected {warning_label} structure in {resolved.read_path}")
    return {}, resolved


def save_station_json_dict(
    playlists_dir: Path,
    filename: str,
    payload: dict[str, Any],
) -> Path:
    resolved = resolve_station_file(playlists_dir, filename, legacy_fallback=False)
    path = resolved.write_path
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
    return path


def save_station_entry_mapping(
    playlists_dir: Path,
    filename: str,
    entries: dict[str, Any],
) -> Path:
    return save_station_json_dict(playlists_dir, filename, {"entries": entries})


def normalize_metadata_component(value: str | None) -> str:
    normalized = unicodedata.normalize("NFKC", value or "")
    return normalized.strip().casefold()


def metadata_key(artist: str, title: str, album: str, year: int | str) -> str:
    return "|".join(
        (
            normalize_metadata_component(artist),
            normalize_metadata_component(title),
            normalize_metadata_component(album),
            str(year),
        )
    )
