#!/usr/bin/env python3
"""Unit tests for yt-dlp download helpers."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pytest

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


def test_yt_dlp_cookie_args_prefers_cookie_file(monkeypatch) -> None:
    monkeypatch.setenv("NC_YTDLP_COOKIES_FILE", "~/cookies.txt")
    monkeypatch.setenv("NC_YTDLP_COOKIES_FROM_BROWSER", "firefox")

    args = download._yt_dlp_cookie_args()

    assert args[0] == "--cookies"
    assert args[1].endswith("cookies.txt")


def test_yt_dlp_cookie_args_uses_browser_when_file_missing(monkeypatch) -> None:
    monkeypatch.delenv("NC_YTDLP_COOKIES_FILE", raising=False)
    monkeypatch.delenv("YTDLP_COOKIES_FILE", raising=False)
    monkeypatch.setenv("YTDLP_COOKIES_FROM_BROWSER", "firefox:default")

    assert download._yt_dlp_cookie_args() == ["--cookies-from-browser", "firefox:default"]


def test_normalize_year_for_id3_handles_unknown_and_zeroed_dates() -> None:
    assert download._normalize_year_for_id3("2018.0") == "2018"
    assert download._normalize_year_for_id3("2026-00-00") == "2026"
    assert download._normalize_year_for_id3(None) == ""


def test_search_probe_returns_true_false_or_none(monkeypatch) -> None:
    monkeypatch.setattr(
        download.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="video-id\n",
        ),
    )
    assert download._yt_dlp_search_has_results("Ghost Rats") is True

    monkeypatch.setattr(
        download.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="",
        ),
    )
    assert download._yt_dlp_search_has_results("Ghost Rats") is False

    def raise_called_process_error(*_args, **_kwargs):
        raise subprocess.CalledProcessError(returncode=1, cmd=[])

    monkeypatch.setattr(download.subprocess, "run", raise_called_process_error)
    assert download._yt_dlp_search_has_results("Ghost Rats") is None


def test_youtube_to_mp3_uses_direct_url_when_search_disabled(tmp_path, monkeypatch) -> None:
    outfile = tmp_path / "downloaded.mp3"
    commands: list[list[str]] = []

    def fake_run(cmd: list[str], check: bool) -> subprocess.CompletedProcess[str]:
        commands.append(cmd)
        outfile.write_bytes(b"mp3")
        return subprocess.CompletedProcess(args=cmd, returncode=0)

    monkeypatch.setattr(download.subprocess, "run", fake_run)

    download.youtube_to_mp3("https://example.test/video", str(outfile), use_search=False)

    assert commands[0][3] == "https://example.test/video"


def test_tag_mp3_sets_standard_tags_and_album_art_provider(tmp_path, monkeypatch) -> None:
    mp3_path = tmp_path / "song.mp3"
    mp3_path.write_bytes(b"mp3")
    saved_tags: dict[str, str] = {}
    embedded: list[tuple[str, str, str]] = []

    class FakeEasyID3(dict):
        def save(self, *_args, **_kwargs) -> None:
            saved_tags.update(self)

    monkeypatch.setattr(download, "ensure_easyid3", lambda _path: FakeEasyID3())
    monkeypatch.setattr(
        download,
        "embed_from_artist_album",
        lambda path, artist, album, log_prefix="": embedded.append((path, artist, album)),
    )
    monkeypatch.setattr(
        download.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(args=[], returncode=0),
    )

    download.tag_mp3(
        str(mp3_path),
        "Ghost",
        "Rats",
        "2018.0",
        "Metal",
        album="Prequelle",
    )

    assert saved_tags["artist"] == "Ghost"
    assert saved_tags["title"] == "Rats"
    assert saved_tags["date"] == "2018"
    assert saved_tags["genre"] == "Metal"
    assert saved_tags["album"] == "Prequelle"
    assert embedded == [(str(mp3_path), "Ghost", "Prequelle")]


def test_tag_mp3_logs_replaygain_missing_without_failing(tmp_path, monkeypatch, capsys) -> None:
    mp3_path = tmp_path / "song.mp3"
    mp3_path.write_bytes(b"mp3")

    class FakeEasyID3(dict):
        def save(self, *_args, **_kwargs) -> None:
            return None

    class FakeID3:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def add(self, *_args, **_kwargs) -> None:
            pass

        def save(self, *_args, **_kwargs) -> None:
            pass

    monkeypatch.setattr(download, "ensure_easyid3", lambda _path: FakeEasyID3())
    monkeypatch.setattr(download, "ID3", FakeID3)
    monkeypatch.setattr(download.subprocess, "run", lambda *_args, **_kwargs: (_ for _ in ()).throw(FileNotFoundError("mp3gain")))

    download.tag_mp3(str(mp3_path), "Ghost", "Rats", "Unknown", "Metal")

    assert "mp3gain not available" in capsys.readouterr().out
