"""Focused unit tests for station sync helpers."""

from __future__ import annotations

import json

from neuralcast.metadata.storage import metadata_key
from neuralcast.models import Song
from neuralcast.pipelines import station_sync


def test_remove_new_releases_metadata_entries_removes_matching_keys(tmp_path) -> None:
    playlists_dir = tmp_path / "playlists"
    metadata_dir = tmp_path / "metadata"
    playlists_dir.mkdir()
    metadata_dir.mkdir()
    keep_key = metadata_key("Artist B", "Keep", "Album", "2026")
    remove_key = metadata_key("Artist A", "Remove", "Album", "2026")
    metadata_path = metadata_dir / "New Releases.metadata.json"
    metadata_path.write_text(
        json.dumps({"entries": {keep_key: {}, remove_key: {}}}),
        encoding="utf-8",
    )

    removed = station_sync.remove_new_releases_metadata_entries(
        playlists_dir,
        [Song(artist="Artist A", title="Remove", album="Album", year="2026")],
    )

    payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert removed == 1
    assert sorted(payload["entries"]) == [keep_key]
