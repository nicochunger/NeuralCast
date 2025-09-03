import os, urllib.parse, requests, difflib, spotipy, musicbrainzngs
from spotipy.oauth2 import SpotifyClientCredentials
import dotenv

# Load environment variables from .env file
dotenv.load_dotenv()

musicbrainzngs.set_useragent("NeuralCast", "0.1", "you@example.com")
sp = spotipy.Spotify(
    auth_manager=SpotifyClientCredentials(
        client_id=os.getenv("SPOTIFY_CLIENT_ID"),
        client_secret=os.getenv("SPOTIFY_CLIENT_SECRET"),
    )
)


def _close_enough(a, b):
    return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio() > 0.7


def spotify_ok(artist, title):
    try:
        res = sp.search(q=f'artist:"{artist}" track:"{title}"', type="track", limit=1)
        return res["tracks"]["total"] > 0
    except Exception:
        return False


def mb_ok(artist, title):
    try:
        q = f'recording:"{title}" AND artist:"{artist}"'
        res = musicbrainzngs.search_recordings(query=q, limit=1)
        return res["recording-count"] > 0
    except Exception:
        return False


def itunes_ok(artist, title):
    try:
        term = urllib.parse.quote_plus(f"{artist} {title}")
        url = f"https://itunes.apple.com/search?term={term}&entity=song&limit=1"
        data = requests.get(url, timeout=10).json()
        if data["resultCount"] == 0:
            return False
        hit = data["results"][0]
        return _close_enough(hit["artistName"], artist) and _close_enough(
            hit["trackName"], title
        )
    except Exception:
        return False


def verified(artist, title):
    return spotify_ok(artist, title) or mb_ok(artist, title) or itunes_ok(artist, title)


def spotify_album_ok(artist, title, album):
    try:
        # Restrict search by album; also double-check album name from the hit
        res = sp.search(
            q=f'artist:"{artist}" track:"{title}" album:"{album}"',
            type="track",
            limit=1,
        )
        if res["tracks"]["total"] == 0:
            return False
        item = res["tracks"]["items"][0]
        return _close_enough(item["album"]["name"], album)
    except Exception:
        return False


def mb_album_ok(artist, title, album):
    try:
        # Search includes 'release' (album) constraint
        q = f'recording:"{title}" AND artist:"{artist}" AND release:"{album}"'
        res = musicbrainzngs.search_recordings(query=q, limit=1)
        return res["recording-count"] > 0
    except Exception:
        return False


def itunes_album_ok(artist, title, album):
    try:
        # Include album in the search term; verify collectionName (album)
        term = urllib.parse.quote_plus(f"{artist} {title} {album}")
        url = f"https://itunes.apple.com/search?term={term}&entity=song&limit=1"
        data = requests.get(url, timeout=10).json()
        if data.get("resultCount", 0) == 0:
            return False
        hit = data["results"][0]
        return (
            _close_enough(hit.get("artistName", ""), artist)
            and _close_enough(hit.get("trackName", ""), title)
            and _close_enough(hit.get("collectionName", ""), album)
        )
    except Exception:
        return False


def verified_album(artist, title, album):
    # True if any provider confirms the specific album for the track
    return (
        spotify_album_ok(artist, title, album)
        or mb_album_ok(artist, title, album)
        or itunes_album_ok(artist, title, album)
    )


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

    album_str = str(album).strip()
    if not album_str:
        return {
            "provided": True,
            "validated": None,
            "message": "Album not provided (empty)",
        }

    is_valid = verified_album(artist, title, album_str)
    if is_valid:
        return {
            "provided": True,
            "validated": True,
            "message": "Album validated",
        }

    # Do not delete it — just report it isn't validated
    return {
        "provided": True,
        "validated": False,
        "message": "Album not validated",
    }
