"""Audio download and tagging helpers."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Optional

from mutagen.easyid3 import EasyID3
from mutagen.id3 import APIC, ID3, ID3NoHeaderError, error

from neuralcast.audio.album_art import embed_from_artist_album
from neuralcast.config import ASSETS_ROOT


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

    def _log(message: str) -> None:
        prefix = log_prefix or ""
        print(f"{prefix}{message}")

    _log(
        f"↻ Tagging '{file_name}' (artist: {artist}, title: {title}, year: {year}, genre: {genre})"
    )
    audio = ensure_easyid3(path)
    audio["artist"] = artist
    audio["title"] = title
    audio["date"] = year
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
    cmd = [
        "yt-dlp",
        source,
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
    print(f"Downloaded: {outfile}")


__all__ = ["ensure_easyid3", "tag_mp3", "youtube_to_mp3"]
