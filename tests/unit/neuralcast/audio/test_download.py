#!/usr/bin/env python3
"""Unit tests for yt-dlp download helpers."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from neuralcast.audio import download


class YoutubeToMp3Test(unittest.TestCase):
    def test_raises_no_results_when_search_finds_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            outfile = Path(tmpdir) / "missing.mp3"

            with patch.object(
                download.subprocess,
                "run",
                return_value=subprocess.CompletedProcess(args=[], returncode=0),
            ), patch.object(
                download, "_yt_dlp_search_has_results", return_value=False
            ):
                with self.assertRaises(download.DownloadNoResultsError):
                    download.youtube_to_mp3("Antti Martikainen Minnōn", str(outfile))

    def test_raises_output_missing_when_search_probe_still_has_results(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            outfile = Path(tmpdir) / "missing.mp3"

            with patch.object(
                download.subprocess,
                "run",
                return_value=subprocess.CompletedProcess(args=[], returncode=0),
            ), patch.object(
                download, "_yt_dlp_search_has_results", return_value=True
            ):
                with self.assertRaises(download.DownloadOutputMissingError):
                    download.youtube_to_mp3("Some Search Query", str(outfile))

    def test_reports_success_when_expected_mp3_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            outfile = Path(tmpdir) / "downloaded.mp3"

            def _fake_run(cmd: list[str], check: bool) -> subprocess.CompletedProcess[str]:
                output_index = cmd.index("-o") + 1
                Path(cmd[output_index]).write_bytes(b"fake mp3")
                return subprocess.CompletedProcess(args=cmd, returncode=0)

            with patch.object(download.subprocess, "run", side_effect=_fake_run):
                download.youtube_to_mp3("Existing Search Query", str(outfile))

            self.assertTrue(outfile.exists())


if __name__ == "__main__":
    unittest.main()
