"""Audio download and tagging helpers."""
from __future__ import annotations

import os
import subprocess
from typing import Optional

from mutagen.easyid3 import EasyID3
from mutagen.id3 import APIC, ID3, ID3NoHeaderError, error

from album_art import embed_from_artist_album


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
):
    print(
        f"Tagging {path} with artist: {artist}, title: {title}, year: {year}, genre: {genre}"
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
            embed_from_artist_album(path, artist, str(album).strip())
        except Exception as exc:
            print(f"Warning: Failed to embed cover art from MusicBrainz: {exc}")
    else:
        try:
            id3 = ID3(path)
        except ID3NoHeaderError:
            id3 = ID3()
            id3.save(path)
            id3 = ID3(path)
        except error:
            id3 = ID3()
        thumbnail_path = os.path.join(os.path.dirname(__file__), "Thumbnail_logo.png")
        if os.path.exists(thumbnail_path):
            with open(thumbnail_path, "rb") as img:
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

    print(f"Applying ReplayGain to {path}")
    try:
        subprocess.run(["mp3gain", "-r", "-k", str(path)], check=True)
    except subprocess.CalledProcessError as exc:
        print(f"Error applying ReplayGain to {path}: {exc}")


def youtube_to_mp3(query: str, outfile: str):
    filtered_query = f"{query}"
    cmd = [
        "yt-dlp",
        f"ytsearch1:{filtered_query}",
        "-x",
        "--audio-format",
        "mp3",
        "--audio-quality",
        "0",
        "-o",
        outfile,
        "--quiet",
    ]
    subprocess.run(cmd, check=True)
    print(f"Downloaded: {outfile}")


__all__ = ["ensure_easyid3", "tag_mp3", "youtube_to_mp3"]
