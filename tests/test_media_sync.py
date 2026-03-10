#!/usr/bin/env python3
"""Unit tests for remote media sync configuration."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from neuralcast.pipelines.media_sync import (
    RemoteSyncConfig,
    build_remote_sync_config,
    build_rsync_command,
)


class MediaSyncConfigTest(unittest.TestCase):
    def _build_config(
        self, station_slug: str
    ) -> tuple[tempfile.TemporaryDirectory, RemoteSyncConfig]:
        tmpdir = tempfile.TemporaryDirectory()
        songs_root = Path(tmpdir.name) / "songs"
        songs_root.mkdir()
        config = build_remote_sync_config(
            station_slug=station_slug,
            local_songs_root=songs_root,
            dry_run=True,
            remote_host=None,
            remote_user=None,
            remote_port=None,
            remote_media_root=None,
            remote_ssh_key=None,
            remote_rsync_bin=None,
            remote_extra_rsync_args=(),
            delete_remote=True,
            timeout_seconds=None,
        )
        return tmpdir, config

    def test_default_remote_media_root_uses_station_slug(self) -> None:
        expected_paths = {
            "neuralcast": "/var/lib/docker/volumes/azuracast_station_data/_data/neuralcast/media",
            "neuralforge": "/var/lib/docker/volumes/azuracast_station_data/_data/neuralforge/media",
        }

        for station_slug, expected_path in expected_paths.items():
            with self.subTest(station=station_slug):
                tmpdir, config = self._build_config(station_slug)
                self.addCleanup(tmpdir.cleanup)
                self.assertEqual(config.remote_media_root, expected_path)

    def test_station_specific_remote_media_root_override_takes_precedence(self) -> None:
        env_updates = {
            "NC_REMOTE_SYNC_MEDIA_ROOT": "/shared/{station}/media",
            "NC_REMOTE_SYNC_MEDIA_ROOT_NEURALCAST": "/custom/neuralcast/media",
        }
        with patch.dict(os.environ, env_updates, clear=False):
            tmpdir, neuralcast_config = self._build_config("neuralcast")
            self.addCleanup(tmpdir.cleanup)
            self.assertEqual(
                neuralcast_config.remote_media_root,
                "/custom/neuralcast/media",
            )

            tmpdir, neuralforge_config = self._build_config("neuralforge")
            self.addCleanup(tmpdir.cleanup)
            self.assertEqual(
                neuralforge_config.remote_media_root,
                "/shared/neuralforge/media",
            )

    def test_rsync_command_targets_resolved_remote_media_root(self) -> None:
        env_updates = {
            "NC_REMOTE_SYNC_MEDIA_ROOT_NEURALCAST": "/custom/neuralcast/media",
        }
        with patch.dict(os.environ, env_updates, clear=False):
            tmpdir, config = self._build_config("neuralcast")
            self.addCleanup(tmpdir.cleanup)

        command = build_rsync_command(config)
        self.assertEqual(
            command[-1],
            "neuralvps:/custom/neuralcast/media/",
        )
        self.assertTrue(command[-2].endswith("/songs/"))


if __name__ == "__main__":
    unittest.main()
