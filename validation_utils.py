"""Helpers for validating songs and albums."""
from __future__ import annotations

import difflib
import os
import urllib.parse
from functools import lru_cache
from typing import List, Optional

import dotenv
import musicbrainzngs
import requests
import spotipy
from requests import Session
from spotipy.oauth2 import SpotifyClientCredentials

from models import Song, ValidationResult

# Load environment variables from .env file
# Keeping this here avoids requiring callers to import dotenv themselves.
dotenv.load_dotenv()

musicbrainzngs.set_useragent("NeuralCast", "0.1", "you@example.com")

SESSION: Session = requests.Session()
SESSION.headers.update({"User-Agent": "NeuralCast/1.0"})
_REQUEST_TIMEOUT = 10

_SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
_SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")

try:
    _SPOTIFY_CLIENT = (
        spotipy.Spotify(
            auth_manager=SpotifyClientCredentials(
                client_id=_SPOTIFY_CLIENT_ID,
                client_secret=_SPOTIFY_CLIENT_SECRET,
            )
        )
        if _SPOTIFY_CLIENT_ID and _SPOTIFY_CLIENT_SECRET
        else None
    )
except Exception:
    _SPOTIFY_CLIENT = None


def _close_enough(a: str, b: str) -> bool:
    return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio() > 0.7


def _norm(value: Optional[str]) -> str:
    return (value or "").strip()


def _itunes_lookup(term: str) -> Optional[dict]:
    url = f"https://itunes.apple.com/search?term={term}&entity=song&limit=1"
    try:
        response = SESSION.get(url, timeout=_REQUEST_TIMEOUT)
        response.raise_for_status()
        return response.json()
    except Exception:
        return None


def _ensure_spotify_client() -> Optional[spotipy.Spotify]:
    return _SPOTIFY_CLIENT


def _musicbrainz_search(query: str, limit: int = 1) -> Optional[dict]:
    try:
        return musicbrainzngs.search_recordings(query=query, limit=limit)
    except Exception:
        return None


def _mb_recording_found(result: Optional[dict]) -> bool:
    if not result:
        return False
    return result.get("recording-count", 0) > 0


@lru_cache(maxsize=2048)
def spotify_ok(artist: str, title: str) -> bool:
    artist = _norm(artist)
    title = _norm(title)
    if not artist or not title:
        return False

    client = _ensure_spotify_client()
    if client is None:
        return False

    try:
        res = client.search(q=f'artist:"{artist}" track:"{title}"', type="track", limit=1)
        return res.get("tracks", {}).get("total", 0) > 0
    except Exception:
        return False


@lru_cache(maxsize=2048)
def mb_ok(artist: str, title: str) -> bool:
    artist = _norm(artist)
    title = _norm(title)
    if not artist or not title:
        return False

    query = f'recording:"{title}" AND artist:"{artist}"'
    result = _musicbrainz_search(query)
    return _mb_recording_found(result)


@lru_cache(maxsize=2048)
def itunes_ok(artist: str, title: str) -> bool:
    artist = _norm(artist)
    title = _norm(title)
    if not artist or not title:
        return False

    term = urllib.parse.quote_plus(f"{artist} {title}")
    data = _itunes_lookup(term)
    if not data or data.get("resultCount", 0) == 0:
        return False

    hit = data["results"][0]
    return _close_enough(hit.get("artistName", ""), artist) and _close_enough(
        hit.get("trackName", ""), title
    )


@lru_cache(maxsize=4096)
def verified(artist: str, title: str) -> bool:
    artist = _norm(artist)
    title = _norm(title)
    if not artist or not title:
        return False

    return spotify_ok(artist, title) or mb_ok(artist, title) or itunes_ok(artist, title)


@lru_cache(maxsize=2048)
def spotify_album_ok(artist: str, title: str, album: str) -> bool:
    artist = _norm(artist)
    title = _norm(title)
    album = _norm(album)
    if not artist or not title or not album:
        return False

    client = _ensure_spotify_client()
    if client is None:
        return False

    try:
        res = client.search(
            q=f'artist:"{artist}" track:"{title}" album:"{album}"',
            type="track",
            limit=1,
        )
        if res.get("tracks", {}).get("total", 0) == 0:
            return False
        item = res["tracks"]["items"][0]
        return _close_enough(item.get("album", {}).get("name", ""), album)
    except Exception:
        return False


@lru_cache(maxsize=2048)
def mb_album_ok(artist: str, title: str, album: str) -> bool:
    artist = _norm(artist)
    title = _norm(title)
    album = _norm(album)
    if not artist or not title or not album:
        return False

    query = f'recording:"{title}" AND artist:"{artist}" AND release:"{album}"'
    result = _musicbrainz_search(query)
    return _mb_recording_found(result)


@lru_cache(maxsize=2048)
def itunes_album_ok(artist: str, title: str, album: str) -> bool:
    artist = _norm(artist)
    title = _norm(title)
    album = _norm(album)
    if not artist or not title or not album:
        return False

    term = urllib.parse.quote_plus(f"{artist} {title} {album}")
    data = _itunes_lookup(term)
    if not data or data.get("resultCount", 0) == 0:
        return False
    hit = data["results"][0]
    return (
        _close_enough(hit.get("artistName", ""), artist)
        and _close_enough(hit.get("trackName", ""), title)
        and _close_enough(hit.get("collectionName", ""), album)
    )


@lru_cache(maxsize=4096)
def verified_album(artist: str, title: str, album: str, verbose: bool = False):
    """Validate a track's album against Spotify, MusicBrainz, and iTunes."""
    artist = _norm(artist)
    title = _norm(title)
    album = _norm(album)
    if not artist or not title or not album:
        if verbose:
            return {
                "spotify": False,
                "musicbrainz": False,
                "itunes": False,
                "any": False,
            }
        return False

    spotify = spotify_album_ok(artist, title, album)
    mb = mb_album_ok(artist, title, album)
    itunes = itunes_album_ok(artist, title, album)

    if verbose:
        return {
            "spotify": spotify,
            "musicbrainz": mb,
            "itunes": itunes,
            "any": spotify or mb or itunes,
        }

    return spotify or mb or itunes


def validate_album_field(artist: str, title: str, album: Optional[str]) -> dict:
    """Validate an optional album column for a track."""
    if album is None:
        return {
            "provided": False,
            "validated": None,
            "message": "No album column present",
        }

    album_str = _norm(album)
    if not album_str:
        return {
            "provided": True,
            "validated": None,
            "message": "Album not provided (empty)",
        }

    validation_details = verified_album(artist, title, album_str, verbose=True)
    is_valid = validation_details["any"]

    if is_valid:
        validated_by = [
            provider.capitalize()
            for provider, passed in validation_details.items()
            if provider != "any" and passed
        ]
        return {
            "provided": True,
            "validated": True,
            "message": f"Album validated by {', '.join(validated_by)}",
        }

    return {
        "provided": True,
        "validated": False,
        "message": "Album not validated by any provider",
    }


def perform_song_validation(
    song: Song, playlist_name: str, invalid_albums: List[dict]
) -> ValidationResult:
    if not verified(song.artist, song.title):
        return ValidationResult(status="song_invalid", song=None)

    album_value = (song.album or "").strip() if song.album else ""
    if album_value:
        try:
            if verified_album(song.artist, song.title, album_value):
                return ValidationResult(
                    status="valid",
                    song=song.copy(update={"validated": True}),
                    album=album_value,
                    album_validated=True,
                )

            invalid_albums.append(
                {
                    "Artist": song.artist,
                    "Title": song.title,
                    "Album": album_value,
                    "Playlist": playlist_name,
                    "Reason": "not_validated",
                }
            )
            return ValidationResult(
                status="album_failed",
                song=None,
                album=album_value,
                album_validated=False,
                album_reason="not_validated",
            )
        except Exception:
            invalid_albums.append(
                {
                    "Artist": song.artist,
                    "Title": song.title,
                    "Album": album_value,
                    "Playlist": playlist_name,
                    "Reason": "validation_error",
                }
            )
            return ValidationResult(
                status="album_failed",
                song=None,
                album=album_value,
                album_validated=False,
                album_reason="validation_error",
            )

    return ValidationResult(
        status="valid",
        song=song.copy(update={"validated": True}),
        album=None,
        album_validated=None,
    )


__all__ = [
    "spotify_ok",
    "mb_ok",
    "itunes_ok",
    "verified",
    "spotify_album_ok",
    "mb_album_ok",
    "itunes_album_ok",
    "verified_album",
    "validate_album_field",
    "perform_song_validation",
]
