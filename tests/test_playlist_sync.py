#!/usr/bin/env python3
"""Unit tests for playlist sync persistence helpers."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from neuralcast.models import Song
from neuralcast.pipelines import playlist_sync


class PlaylistSyncPersistenceTest(unittest.TestCase):
    def test_save_playlist_state_removes_new_release_and_cleans_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            playlist_file = Path(tmpdir) / "New Releases.csv"
            playlist_df = pd.DataFrame(
                [
                    {
                        "Artist": "Artist A",
                        "Title": "Remove Me",
                        "Album": "Album A",
                        "Year": "2026",
                        "Validated": True,
                    },
                    {
                        "Artist": "Artist B",
                        "Title": "Keep Me",
                        "Album": "Album B",
                        "Year": "2026",
                        "Validated": True,
                    },
                ]
            )
            playlist_df.to_csv(playlist_file, index=False)

            songs = [
                Song(
                    artist="Artist A",
                    title="Remove Me",
                    album="Album A",
                    year="2026",
                    validated=True,
                ),
                Song(
                    artist="Artist B",
                    title="Keep Me",
                    album="Album B",
                    year="2026",
                    validated=True,
                ),
            ]

            with patch.object(
                playlist_sync,
                "remove_new_releases_metadata_entries",
                return_value=1,
            ) as remove_metadata:
                updated_songs = playlist_sync._save_playlist_state(
                    playlist_file,
                    "New Releases",
                    songs,
                    playlist_df,
                    songs_to_remove=[songs[0]],
                )

            self.assertEqual([song.title for song in updated_songs], ["Keep Me"])
            persisted_df = pd.read_csv(playlist_file, dtype=str).fillna("")
            self.assertEqual(list(persisted_df["Title"]), ["Keep Me"])
            remove_metadata.assert_called_once_with(playlist_file.parent, [songs[0]])


if __name__ == "__main__":
    unittest.main()
