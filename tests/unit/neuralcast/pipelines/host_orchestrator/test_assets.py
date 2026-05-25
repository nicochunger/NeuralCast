"""Unit tests for host story asset helpers."""

from __future__ import annotations

import csv
import json

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
