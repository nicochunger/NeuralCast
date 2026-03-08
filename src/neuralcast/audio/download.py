"""Audio download and tagging helpers."""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional

from mutagen.easyid3 import EasyID3
from mutagen.id3 import APIC, ID3, ID3NoHeaderError, error

from neuralcast.audio.album_art import embed_from_artist_album
from neuralcast.config import ASSETS_ROOT

_FLOAT_YEAR_PATTERN = re.compile(r"^(\d{4})\.0+$")
_ZEROED_DATE_YEAR_PATTERN = re.compile(r"^(\d{4})-00(?:-00)?$")


class DownloadNoResultsError(FileNotFoundError):
    """Raised when a yt-dlp search query resolves to no downloadable items."""


class DownloadOutputMissingError(FileNotFoundError):
    """Raised when yt-dlp succeeds but does not produce the requested MP3."""


def _yt_dlp_cookie_args() -> list[str]:
    cookies_file = (
        str(
            os.getenv("NC_YTDLP_COOKIES_FILE")
            or os.getenv("YTDLP_COOKIES_FILE")
            or ""
        )
        .strip()
    )
    if cookies_file:
        cookies_file = os.path.expanduser(os.path.expandvars(cookies_file))
        return ["--cookies", cookies_file]

    cookies_from_browser = (
        str(
            os.getenv("NC_YTDLP_COOKIES_FROM_BROWSER")
            or os.getenv("YTDLP_COOKIES_FROM_BROWSER")
            or ""
        )
        .strip()
    )
    if cookies_from_browser:
        # Accept raw yt-dlp value, e.g. "firefox" or "firefox:default".
        return ["--cookies-from-browser", cookies_from_browser]

    return []


def _normalize_year_for_id3(year: object) -> str:
    text = str(year).strip() if year is not None else ""
    if not text:
        return ""

    float_match = _FLOAT_YEAR_PATTERN.fullmatch(text)
    if float_match:
        return float_match.group(1)

    zeroed_date_match = _ZEROED_DATE_YEAR_PATTERN.fullmatch(text)
    if zeroed_date_match:
        return zeroed_date_match.group(1)

    return text


def ensure_easyid3(path: str) -> EasyID3:
    try:
        return EasyID3(path)
    except ID3NoHeaderError:
        tags = EasyID3()
        tags.save(path)
        return EasyID3(path)


def tag_mp3(
    path: str,
    artist: str,
    title: str,
    year: str,
    genre: str,
    album: Optional[str] = None,
    *,
    log_prefix: str = "",
):
    file_name = os.path.basename(path)
    trimmed_album = str(album).strip() if album else ""
    trimmed_year = _normalize_year_for_id3(year)

    def _log(message: str) -> None:
        prefix = log_prefix or ""
        print(f"{prefix}{message}")

    _log(
        f"↻ Tagging '{file_name}' (artist: {artist}, title: {title}, year: {trimmed_year}, genre: {genre})"
    )
    audio = ensure_easyid3(path)
    audio["artist"] = artist
    audio["title"] = title
    if trimmed_year and trimmed_year.casefold() != "unknown":
        audio["date"] = trimmed_year
    audio["genre"] = genre
    if album and str(album).strip():
        audio["album"] = str(album).strip()
    audio.save()

    if album and str(album).strip():
        try:
            _log("🎨 Embedding album art via MusicBrainz")
            embed_from_artist_album(path, artist, trimmed_album, log_prefix=log_prefix)
            _log("   ✓ Album art embedded")
        except Exception as exc:
            _log(f"⚠️ Failed to embed cover art from MusicBrainz: {exc}")
    else:
        try:
            id3 = ID3(path)
        except ID3NoHeaderError:
            id3 = ID3()
            id3.save(path)
            id3 = ID3(path)
        except error:
            id3 = ID3()
        thumbnail_path = ASSETS_ROOT / "images" / "Thumbnail_logo.png"
        if thumbnail_path.exists():
            with thumbnail_path.open("rb") as img:
                id3.add(
                    APIC(
                        encoding=3,
                        mime="image/png",
                        type=3,
                        desc="Cover",
                        data=img.read(),
                    )
                )
            id3.save(path)
            _log("🎨 Attached fallback thumbnail art")
        else:
            _log("🎨 No fallback thumbnail art available")

    _log("🔊 Applying ReplayGain")
    try:
        subprocess.run(["mp3gain", "-q", "-r", "-k", str(path)], check=True)
    except FileNotFoundError as exc:
        _log(
            f"⚠️ mp3gain not available ({exc}); continuing without ReplayGain normalization"
        )
    except subprocess.CalledProcessError as exc:
        _log(f"⚠️ Error applying ReplayGain: {exc}")
    except OSError as exc:  # pragma: no cover - unexpected OS-level failure
        _log(f"⚠️ ReplayGain skipped due to OS error: {exc}")


def youtube_to_mp3(query: str, outfile: str, *, use_search: bool = True):
    filtered_query = f"{query}"
    source = f"ytsearch1:{filtered_query}" if use_search else filtered_query
    cookie_args = _yt_dlp_cookie_args()
    cmd = [
        sys.executable,
        "-m",
        "yt_dlp",
        source,
        "--remote-components",
        "ejs:github",
        *cookie_args,
        "-x",
        "--audio-format",
        "mp3",
        "--audio-quality",
        "0",
        "-o",
        outfile,
        "--quiet",
        "--no-playlist",
    ]
    subprocess.run(cmd, check=True)
    output_path = Path(outfile)
    if output_path.exists():
        print(f"Downloaded: {outfile}")
        return

    if use_search:
        search_has_results = _yt_dlp_search_has_results(filtered_query)
        if search_has_results is False:
            raise DownloadNoResultsError(
                f"yt-dlp search returned no results for query: {filtered_query}"
            )

    raise DownloadOutputMissingError(
        f"yt-dlp finished without creating expected output file: {outfile}"
    )


def _yt_dlp_search_has_results(query: str) -> Optional[bool]:
    source = f"ytsearch1:{query}"
    cookie_args = _yt_dlp_cookie_args()
    cmd = [
        sys.executable,
        "-m",
        "yt_dlp",
        source,
        "--remote-components",
        "ejs:github",
        *cookie_args,
        "--flat-playlist",
        "--skip-download",
        "--print",
        "id",
        "--quiet",
        "--no-playlist",
    ]
    try:
        result = subprocess.run(
            cmd,
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError:
        return None

    return bool(result.stdout.strip())


__all__ = [
    "DownloadNoResultsError",
    "DownloadOutputMissingError",
    "ensure_easyid3",
    "tag_mp3",
    "youtube_to_mp3",
]
