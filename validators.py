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

    album_str = str(album).strip()
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
