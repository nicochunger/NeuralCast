import difflib
import os
import urllib.parse
from functools import lru_cache
from typing import Optional

import dotenv
import musicbrainzngs
import requests
import spotipy
from requests import Session
from spotipy.oauth2 import SpotifyClientCredentials

# Load environment variables from .env file
dotenv.load_dotenv()

musicbrainzngs.set_useragent("NeuralCast", "0.1", "you@example.com")

SESSION: Session = requests.Session()
SESSION.headers.update({"User-Agent": "NeuralCast/1.0"})
_REQUEST_TIMEOUT = 10

_SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
_SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")

try:
    sp = (
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
    sp = None


def _close_enough(a, b):
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
    return sp


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
def spotify_ok(artist, title):
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
def mb_ok(artist, title):
    artist = _norm(artist)
    title = _norm(title)
    if not artist or not title:
        return False

    query = f'recording:"{title}" AND artist:"{artist}"'
    result = _musicbrainz_search(query)
    return _mb_recording_found(result)


@lru_cache(maxsize=2048)
def itunes_ok(artist, title):
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
def verified(artist, title):
    artist = _norm(artist)
    title = _norm(title)
    if not artist or not title:
        return False

    return spotify_ok(artist, title) or mb_ok(artist, title) or itunes_ok(artist, title)


@lru_cache(maxsize=2048)
def spotify_album_ok(artist, title, album):
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
def mb_album_ok(artist, title, album):
    artist = _norm(artist)
    title = _norm(title)
    album = _norm(album)
    if not artist or not title or not album:
        return False

    query = f'recording:"{title}" AND artist:"{artist}" AND release:"{album}"'
    result = _musicbrainz_search(query)
    return _mb_recording_found(result)


@lru_cache(maxsize=2048)
def itunes_album_ok(artist, title, album):
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
def verified_album(artist, title, album, verbose=False):
    """
    Checks if a track's album is verified by Spotify, MusicBrainz, or iTunes.

    Args:
        artist (str): The artist's name.
        title (str): The track's title.
        album (str): The album's name.
        verbose (bool): If True, returns a dict with each provider's status.

    Returns:
        bool or dict: If verbose is False, returns True if any provider verifies
                      the album, False otherwise. If verbose is True, returns a
                      dictionary with the validation status for each provider.
    """
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


def validate_album_field(artist, title, album):
    """
    Validates an optional album value for a track.

    Returns a dict:
      {
        "provided": bool,        # album column/value provided
        "validated": bool|None,  # None if not provided/empty, True/False otherwise
        "message": str           # short status message
      }
    """
    # No album column or value not provided
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
            k.capitalize() for k, v in validation_details.items() if k != "any" and v
        ]
        return {
            "provided": True,
            "validated": True,
            "message": f"Album validated by {', '.join(validated_by)}",
        }

    # Do not delete it — just report it isn't validated
    return {
        "provided": True,
        "validated": False,
        "message": "Album not validated by any provider",
    }
