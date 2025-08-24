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
