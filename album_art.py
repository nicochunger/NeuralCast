import musicbrainzngs
import requests
from mutagen.id3 import APIC, ID3, ID3NoHeaderError
from mutagen.easyid3 import EasyID3
import datetime
import tempfile
from IPython.display import Image as IPyImage, display
import os
from validators import _close_enough
import json

# Set up musicbrainzngs library
musicbrainzngs.set_useragent(
    "NeuralCastArtEmbedder", "1.0", "https://github.com/your-repo"
)

LOG_FILE = os.path.join(os.path.dirname(__file__), "logs/album_art_skipped.log")


def _log_skip(entry: dict):
    try:
        # Ensure directory exists (in case LOG_FILE points to a subdir)
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
    except Exception as e:
        print(f"-> Failed to write skip log: {e}")


def _parse_release_date(date_str: str) -> datetime.datetime:
    try:
        return datetime.datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        try:
            return datetime.datetime.strptime(date_str, "%Y-%m")
        except ValueError:
            try:
                return datetime.datetime.strptime(date_str, "%Y")
            except ValueError:
                return datetime.datetime.max


def find_best_release_from_releases(releases):
    """
    Finds the best release from a list of releases (result of search_releases).
    Prioritizes the earliest, official album release.
    """
    candidate_releases = []
    for release in releases:
        if (
            release.get("status") == "Official"
            and release.get("release-group", {}).get("primary-type") == "Album"
            and "date" in release
        ):
            candidate_releases.append(release)

    if not candidate_releases:
        return None

    candidate_releases.sort(key=lambda r: _parse_release_date(r.get("date", "")))
    return candidate_releases[0]


def _download_cover_art(release_id: str):
    art_url = f"https://coverartarchive.org/release/{release_id}/front"
    response = requests.get(art_url, allow_redirects=True, timeout=10)
    response.raise_for_status()
    image_data = response.content
    mime_type = response.headers.get("Content-Type", "image/jpeg")
    return image_data, mime_type, art_url


def _embed_image(mp3_path: str, image_data: bytes, mime_type: str):
    try:
        audio = ID3(mp3_path)
    except ID3NoHeaderError:
        audio = ID3()
    audio.delall("APIC")
    audio.add(APIC(encoding=3, mime=mime_type, type=3, desc="Cover", data=image_data))
    audio.save(mp3_path)


def embed_from_release_id(
    mp3_path: str, release_id: str, release_title: str | None = None
):
    try:
        image_data, mime_type, art_url = _download_cover_art(release_id)
        print(f"-> Successfully downloaded cover art from {art_url}")
        _embed_image(mp3_path, image_data, mime_type)
        if release_title:
            print(
                f"-> Successfully embedded artwork into '{mp3_path}' (Release: '{release_title}')"
            )
        else:
            print(f"-> Successfully embedded artwork into '{mp3_path}'")
        return True
    except requests.exceptions.RequestException as e:
        # Do not log here; let caller try other releases and log only if all fail.
        print(f"-> Failed to download cover art: {e}")
        return False
    except Exception as e:
        # Do not log here; let caller handle final logging.
        print(f"-> An unexpected error occurred while embedding from release id: {e}")
        return False


def embed_from_artist_album(mp3_path: str, artist: str, album: str):
    """
    Uses artist + album to find the best release and embed its cover.
    """
    print(f"Searching for album '{album}' by '{artist}' on MusicBrainz...")
    try:
        result = musicbrainzngs.search_releases(artist=artist, release=album, limit=10)
        releases = result.get("release-list", [])
        if not releases:
            print("-> No releases found for given artist and album.")
            _log_skip(
                {
                    "ts": datetime.datetime.utcnow().isoformat() + "Z",
                    "input": {"artist": artist, "album": album, "mp3_path": mp3_path},
                    "reason": "no_releases",
                }
            )
            return

        release = find_best_release_from_releases(releases) or releases[0]
        release_id = release["id"]
        found_title = release.get("title", album)
        print(f"-> Found release: '{found_title}' (ID: {release_id})")

        # Build close-enough candidates:
        # - Try the "best" release first if it's close
        # - Then other close-enough releases, preferring Official Albums and earlier dates
        def _alt_sort_key(r):
            is_official_album = (
                r.get("status") == "Official"
                and r.get("release-group", {}).get("primary-type") == "Album"
            )
            date = _parse_release_date(r.get("date", ""))
            return (0 if is_official_album else 1, date)

        candidates = []
        if _close_enough(found_title, album):
            candidates.append(release)

        alternates = [
            r
            for r in releases
            if r.get("id") != release_id and _close_enough(r.get("title", ""), album)
        ]
        alternates.sort(key=_alt_sort_key)
        candidates.extend(alternates)

        if not candidates:
            _log_skip(
                {
                    "ts": datetime.datetime.utcnow().isoformat() + "Z",
                    "input": {"artist": artist, "album": album, "mp3_path": mp3_path},
                    "found": {"title": found_title, "id": release_id},
                    "reason": "not_close_enough_any_release",
                }
            )
            print(f"-> No close-enough releases found. Logged to {LOG_FILE}")
            return

        # Try candidates until one succeeds (cover art available and embedded)
        for r in candidates:
            if embed_from_release_id(mp3_path, r["id"], r.get("title", album)):
                return

        # If none succeeded, log a summary entry
        _log_skip(
            {
                "ts": datetime.datetime.utcnow().isoformat() + "Z",
                "input": {"artist": artist, "album": album, "mp3_path": mp3_path},
                "reason": "no_cover_art_found_for_any_matching_release",
                "attempted_release_ids": [r["id"] for r in candidates],
            }
        )
    except musicbrainzngs.WebServiceError as exc:
        print(f"-> MusicBrainz API error: {exc}")
        _log_skip(
            {
                "ts": datetime.datetime.utcnow().isoformat() + "Z",
                "input": {"artist": artist, "album": album, "mp3_path": mp3_path},
                "reason": "musicbrainz_error",
                "error": str(exc),
            }
        )
    except Exception as e:
        print(f"-> An unexpected error occurred: {e}")
        _log_skip(
            {
                "ts": datetime.datetime.utcnow().isoformat() + "Z",
                "input": {"artist": artist, "album": album, "mp3_path": mp3_path},
                "reason": "unexpected_error",
                "error": str(e),
            }
        )


def show_embedded_art(mp3_path: str):
    """Display the embedded cover art (preferring front cover) for the given MP3."""
    print(f"[show] Loading ID3 from: {mp3_path}")
    id3 = ID3(mp3_path)

    apics = id3.getall("APIC")
    print(f"[show] Found {len(apics)} APIC frame(s).")
    if not apics:
        print("[show] No embedded artwork found.")
        return None

    apic = next((a for a in apics if getattr(a, "type", None) == 3), apics[0])
    mime = apic.mime
    print(f"[show] Selected APIC type={getattr(apic, 'type', None)}, MIME={mime}")

    fmt = "png" if "png" in (mime or "").lower() else "jpeg"
    print(f"[show] Displaying image (format={fmt}, fixed width=400)...")
    display(IPyImage(data=apic.data, format=fmt, width=400))

    # Save to a temporary file to view
    fd, path = tempfile.mkstemp(suffix=f".{fmt}")
    with os.fdopen(fd, "wb") as f:
        f.write(apic.data)
    print(f"Saved embedded art to: {path}")

    return mime
