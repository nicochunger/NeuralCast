"""Persistence and output helpers for station playlist synchronization."""

from __future__ import annotations

import contextlib
import io
import pathlib
from typing import Callable, List, Tuple

from neuralcast.metadata.constants import NEW_RELEASES_METADATA_FILENAME
from neuralcast.models import Song
from neuralcast.playlists.catalog import StationPlaylistCatalog


def _capture_output(func: Callable[[], object]) -> Tuple[object, List[str]]:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        result = func()
    return result, [
        line.rstrip() for line in buffer.getvalue().splitlines() if line.strip()
    ]


def _emit_captured_lines(
    lines: List[str],
    *,
    logger: Callable[[str], None],
    include_plain: bool = False,
) -> None:
    for line in lines:
        if (
            include_plain
            or "⚠️" in line
            or "❌" in line
            or "warning" in line.casefold()
            or "error" in line.casefold()
            or "failed" in line.casefold()
            or "deleted" in line.casefold()
            or "removed" in line.casefold()
        ):
            logger(line)


def remove_new_releases_metadata_entries(
    playlists_dir: pathlib.Path, songs_to_remove: List[Song]
) -> int:
    """Compatibility entrypoint for catalog-owned companion metadata cleanup."""

    if not songs_to_remove:
        return 0
    return StationPlaylistCatalog(playlists_dir).remove_companion_entries(
        songs_to_remove,
        filename=NEW_RELEASES_METADATA_FILENAME,
    )


def _log_album_validation_result(
    song: Song,
    result,
    *,
    log: Callable[[str], None] = print,
) -> None:
    if not result.album:
        return
    if result.album_cleared:
        log(
            f"🧹 album removed after validation failure: {song.artist} - {song.title} ({result.album})"
        )
        return
    log(f"✅ album validated: {song.artist} - {song.title} ({result.album})")


__all__ = [
    "_capture_output",
    "_emit_captured_lines",
    "_log_album_validation_result",
    "remove_new_releases_metadata_entries",
]
