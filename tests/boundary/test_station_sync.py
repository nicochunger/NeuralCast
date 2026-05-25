#!/usr/bin/env python3
"""Boundary tests for the station sync service."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from neuralcast.metadata.storage import metadata_key
from neuralcast.models import Song, ValidationResult
from neuralcast.pipelines.media_sync import RemoteSyncRequest
from neuralcast.pipelines.station_sync import (
    MediaLibrary,
    PlaylistLog,
    StationSync,
    SyncRequest,
    TrackResolver,
)


class FakeResolver(TrackResolver):
    def __init__(
        self,
        *,
        available: dict[tuple[str, str], bool] | None = None,
        validations: dict[tuple[str, str], ValidationResult] | None = None,
    ) -> None:
        self.available = available or {}
        self.validations = validations or {}

    def is_available(self, song: Song) -> bool:
        return self.available.get((song.artist, song.title), True)

    def validate_song(self, song: Song) -> ValidationResult:
        return self.validations.get(
            (song.artist, song.title),
            ValidationResult(song=song.model_copy(update={"validated": True})),
        )

    def backfill_album(
        self,
        song: Song,
        *,
        log=print,
    ) -> tuple[Song, bool]:
        return song, False


class FakeMediaLibrary(MediaLibrary):
    def __init__(self) -> None:
        self.downloads: list[Path] = []

    def apply_override(
        self,
        song: Song,
        song_path: Path | None,
        playlist_name: str,
        *,
        dry_run: bool,
        log: PlaylistLog,
    ) -> bool:
        return False

    def audit_existing_tags(
        self,
        existing_songs: list[tuple[Song, Path]],
        playlist_name: str,
        *,
        log: PlaylistLog,
    ) -> int:
        return 0

    def download_song(
        self,
        song: Song,
        song_path: Path,
        playlist_name: str,
        *,
        log: PlaylistLog,
    ) -> None:
        song_path.parent.mkdir(parents=True, exist_ok=True)
        song_path.write_bytes(b"fake mp3")
        self.downloads.append(song_path)

    def delete_file(
        self,
        song_path: Path,
        *,
        log: PlaylistLog,
    ) -> None:
        if song_path.exists():
            song_path.unlink()


class StationSyncBoundaryTest(unittest.TestCase):
    def test_run_removes_invalid_new_release_and_cleans_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            station_dir = Path(tmpdir) / "Station"
            playlists_dir = station_dir / "playlists"
            metadata_dir = station_dir / "metadata"
            songs_dir = station_dir / "songs"
            playlists_dir.mkdir(parents=True)
            metadata_dir.mkdir()
            songs_dir.mkdir()

            playlist_file = playlists_dir / "New Releases.csv"
            pd.DataFrame(
                [
                    {
                        "Artist": "Artist A",
                        "Title": "Remove Me",
                        "Album": "Album A",
                        "Year": "2026",
                        "Validated": False,
                    },
                    {
                        "Artist": "Artist B",
                        "Title": "Keep Me",
                        "Album": "Album B",
                        "Year": "2026",
                        "Validated": True,
                    },
                ]
            ).to_csv(playlist_file, index=False)

            metadata_entries = {
                metadata_key("Artist A", "Remove Me", "Album A", "2026"): {"provider": "test"},
                metadata_key("Artist B", "Keep Me", "Album B", "2026"): {"provider": "test"},
            }
            metadata_file = metadata_dir / "New Releases.metadata.json"
            metadata_file.write_text(
                json.dumps({"entries": metadata_entries}, indent=2) + "\n",
                encoding="utf-8",
            )

            resolver = FakeResolver(available={("Artist A", "Remove Me"): False})
            service = StationSync(
                resolver=resolver,
                media_library=FakeMediaLibrary(),
                station_dir_resolver=lambda _slug: station_dir,
            )

            report = service.run(
                SyncRequest(
                    station_slug="test-station",
                    dry_run=True,
                    remote_sync=RemoteSyncRequest(enabled=False),
                )
            )

            self.assertEqual(len(report.playlist_reports), 1)
            playlist_report = report.playlist_reports[0]
            self.assertEqual(playlist_report.name, "New Releases")
            self.assertEqual(playlist_report.removed_count, 1)
            self.assertEqual(playlist_report.final_song_count, 1)

            persisted_df = pd.read_csv(playlist_file, dtype=str).fillna("")
            self.assertEqual(list(persisted_df["Title"]), ["Keep Me"])

            persisted_metadata = json.loads(metadata_file.read_text(encoding="utf-8"))
            self.assertEqual(
                sorted(persisted_metadata["entries"].keys()),
                [metadata_key("Artist B", "Keep Me", "Album B", "2026")],
            )
            self.assertTrue(report.duplicate_analysis_log.exists())

    def test_run_downloads_validated_missing_song_via_media_adapter(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            station_dir = Path(tmpdir) / "Station"
            playlists_dir = station_dir / "playlists"
            songs_dir = station_dir / "songs"
            playlists_dir.mkdir(parents=True)
            songs_dir.mkdir()

            playlist_file = playlists_dir / "Synthwave.csv"
            pd.DataFrame(
                [
                    {
                        "Artist": "Timecop1983",
                        "Title": "On the Run",
                        "Album": "",
                        "Year": "2016",
                        "Validated": False,
                    }
                ]
            ).to_csv(playlist_file, index=False)

            validated_song = Song(
                artist="Timecop1983",
                title="On the Run",
                album=None,
                year="2016",
                validated=True,
            )
            resolver = FakeResolver(
                validations={
                    ("Timecop1983", "On the Run"): ValidationResult(song=validated_song)
                }
            )
            media_library = FakeMediaLibrary()
            service = StationSync(
                resolver=resolver,
                media_library=media_library,
                station_dir_resolver=lambda _slug: station_dir,
            )

            report = service.run(
                SyncRequest(
                    station_slug="test-station",
                    dry_run=False,
                    remote_sync=RemoteSyncRequest(enabled=False),
                )
            )

            playlist_report = report.playlist_reports[0]
            self.assertEqual(playlist_report.downloaded_count, 1)
            self.assertEqual(playlist_report.failed_count, 0)
            self.assertEqual(len(media_library.downloads), 1)
            self.assertTrue(media_library.downloads[0].exists())


if __name__ == "__main__":
    unittest.main()
