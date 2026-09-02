"""Unit tests for host story asset helpers."""

from __future__ import annotations

import csv
import datetime as dt
import json
import os
from types import SimpleNamespace

from neuralcast.pipelines.host_orchestrator import assets


def test_load_station_track_metadata_merges_csv_and_metadata_cache(tmp_path) -> None:
    station_dir = tmp_path / "Station"
    playlist_dir = station_dir / "playlists"
    metadata_dir = station_dir / "metadata"
    playlist_dir.mkdir(parents=True)
    metadata_dir.mkdir()
    with (playlist_dir / "New Releases.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["Artist", "Title", "Album", "Year"])
        writer.writeheader()
        writer.writerow({"Artist": "Ghost", "Title": "Rats", "Album": "Prequelle", "Year": "2018"})
    (metadata_dir / "New Releases.metadata.json").write_text(
        json.dumps(
            {
                "entries": {
                    "ghost|rats|prequelle|2018": {"Popularity": 100}
                }
            }
        ),
        encoding="utf-8",
    )

    metadata = assets.load_station_track_metadata(station_dir)

    item = metadata["ghost|rats"]
    assert item.album == "Prequelle"
    assert item.year == "2018"
    assert item.genre == "New Releases"
    assert item.notes == "popularity=100"


def test_load_station_track_metadata_tolerates_bad_sources_and_uses_direct_entries(tmp_path) -> None:
    station_dir = tmp_path / "Station"
    playlists = station_dir / "playlists"
    metadata_dir = station_dir / "metadata"
    playlists.mkdir(parents=True)
    metadata_dir.mkdir()
    (playlists / "Broken.csv").write_bytes(b"\xff\xfe")
    (metadata_dir / "New Releases.metadata.json").write_text(
        json.dumps({"Artist|Title|Album|2026": {"AlbumType": "single", "ReleaseDate": "2026-01-01"}}),
        encoding="utf-8",
    )

    metadata = assets.load_station_track_metadata(station_dir)

    assert metadata["artist|title"].album == "Album"
    assert metadata["artist|title"].notes == "album_type=single, release_date=2026-01-01"


def test_replaygain_and_local_cleanup_handle_missing_tool_and_expired_files(tmp_path, monkeypatch) -> None:
    audio = tmp_path / "story.mp3"
    audio.write_bytes(b"audio")
    monkeypatch.setattr(assets.subprocess, "run", lambda *_args, **_kwargs: (_ for _ in ()).throw(FileNotFoundError("missing")))
    assets.apply_replaygain(audio)

    output_dir = tmp_path / "snippets"
    old_dir = output_dir / "neuralforge" / "old"
    old_dir.mkdir(parents=True)
    old_file = old_dir / "story.mp3"
    old_file.write_bytes(b"old")
    fresh_file = output_dir / "neuralforge" / "fresh" / "story.txt"
    fresh_file.parent.mkdir(parents=True)
    fresh_file.write_text("fresh", encoding="utf-8")
    old_ts = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=10)).timestamp()
    os.utime(old_file, (old_ts, old_ts))
    monkeypatch.setattr(assets, "STORY_OUTPUT_DIR", output_dir)

    assets.cleanup_local_stories("neuralforge", keep_days=3)

    assert not old_file.exists()
    assert fresh_file.exists()
    assert not old_dir.exists()


def test_remote_cleanup_deletes_only_expired_matching_files() -> None:
    now = dt.datetime.now(dt.timezone.utc).timestamp()
    deleted: list[tuple[str, int]] = []
    client = SimpleNamespace(
        list_media_files=lambda _station: [
            {"path": "AI Stories/old.mp3", "mtime": now - 10_000_000, "id": 1},
            {"path": "AI Stories/fresh.mp3", "mtime": now, "id": 2},
            {"path": "Music/old.mp3", "mtime": now - 10_000_000, "id": 3},
            {"path": "AI Stories/no-id.mp3", "mtime": now - 10_000_000},
        ],
        delete_media_file=lambda station, media_id: deleted.append((station, media_id)),
    )

    assets.cleanup_remote_stories(client, "neuralforge", keep_days=3)

    assert deleted == [("neuralforge", 1)]
