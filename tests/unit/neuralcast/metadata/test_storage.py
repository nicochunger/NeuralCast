#!/usr/bin/env python3
"""Unit tests for shared station metadata storage helpers."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from neuralcast.metadata import storage


class StationMetadataStorageTest(unittest.TestCase):
    def test_load_station_entry_mapping_uses_legacy_file_and_unwraps_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            playlists_dir = Path(tmpdir) / "playlists"
            playlists_dir.mkdir()
            legacy_file = playlists_dir / "New Releases.metadata.json"
            legacy_file.write_text(
                '{"entries":{"artist|title|album|2026":{"TrackID":"123"}}}\n',
                encoding="utf-8",
            )
            info_messages: list[str] = []
            warning_messages: list[str] = []

            entries, resolved = storage.load_station_entry_mapping(
                playlists_dir,
                "New Releases.metadata.json",
                log_info=info_messages.append,
                log_warning=warning_messages.append,
            )

            self.assertEqual(
                entries,
                {"artist|title|album|2026": {"TrackID": "123"}},
            )
            self.assertTrue(resolved.used_legacy_path)
            self.assertEqual(resolved.read_path, legacy_file)
            self.assertEqual(
                resolved.write_path,
                playlists_dir.parent / "metadata" / "New Releases.metadata.json",
            )
            self.assertEqual(warning_messages, [])
            self.assertEqual(len(info_messages), 1)

    def test_save_station_entry_mapping_writes_to_metadata_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            playlists_dir = Path(tmpdir) / "playlists"
            playlists_dir.mkdir()

            output_path = storage.save_station_entry_mapping(
                playlists_dir,
                "New Releases.metadata.json",
                {"artist|title|album|2026": {"TrackID": "123"}},
            )

            self.assertEqual(
                output_path,
                playlists_dir.parent / "metadata" / "New Releases.metadata.json",
            )
            self.assertTrue(output_path.exists())
            self.assertFalse((playlists_dir / "New Releases.metadata.json").exists())
            self.assertIn('"entries"', output_path.read_text(encoding="utf-8"))

    def test_metadata_key_normalizes_unicode_and_spacing(self) -> None:
        key = storage.metadata_key("  MotOrHeAd ", "Ace  Of  Spades", "Överkill", "1980")

        self.assertEqual(key, "motorhead|ace  of  spades|överkill|1980")


if __name__ == "__main__":
    unittest.main()
