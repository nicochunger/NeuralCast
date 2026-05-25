#!/usr/bin/env python3
"""Unit tests for remote media sync configuration."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from neuralcast.pipelines.media_sync import (
    RemoteSyncConfig,
    build_remote_sync_config,
    build_rsync_command,
    run_remote_sync,
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

    def test_run_remote_sync_counts_changed_and_deleted_items(self) -> None:
        tmpdir, config = self._build_config("neuralcast")
        self.addCleanup(tmpdir.cleanup)

        def fake_run(cmd, **kwargs):
            if cmd[:1] == ["ssh"]:
                return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")
            return subprocess.CompletedProcess(
                cmd,
                returncode=0,
                stdout=">f+++++++++ Playlist/song.mp3\n*deleting Old/song.mp3\nsent 1 bytes\n",
                stderr="",
            )

        with patch("neuralcast.pipelines.media_sync.subprocess.run", side_effect=fake_run):
            result = run_remote_sync(config)

        self.assertEqual(result.changed_count, 2)
        self.assertEqual(result.deleted_count, 1)
        self.assertTrue(result.dry_run)

    def test_run_remote_sync_raises_when_preflight_fails(self) -> None:
        tmpdir, config = self._build_config("neuralforge")
        self.addCleanup(tmpdir.cleanup)

        with patch(
            "neuralcast.pipelines.media_sync.subprocess.run",
            return_value=subprocess.CompletedProcess(
                ["ssh"],
                returncode=1,
                stdout="",
                stderr="missing",
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "Remote media root"):
                run_remote_sync(config)


if __name__ == "__main__":
    unittest.main()
