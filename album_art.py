import musicbrainzngs
import requests
from mutagen.id3 import APIC, ID3, ID3NoHeaderError
from mutagen.easyid3 import EasyID3
import datetime
import tempfile
from IPython.display import Image as IPyImage, display
import os

# Set up musicbrainzngs library
musicbrainzngs.set_useragent(
    "NeuralCastArtEmbedder", "1.0", "https://github.com/your-repo"
)


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
    except requests.exceptions.RequestException as e:
        print(f"-> Failed to download cover art: {e}")
    except Exception as e:
        print(f"-> An unexpected error occurred while embedding from release id: {e}")


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
            return

        release = find_best_release_from_releases(releases) or releases[0]
        release_id = release["id"]
        print(f"-> Found release: '{release.get('title', album)}' (ID: {release_id})")
        embed_from_release_id(mp3_path, release_id, release.get("title", album))
    except musicbrainzngs.WebServiceError as exc:
        print(f"-> MusicBrainz API error: {exc}")
    except Exception as e:
        print(f"-> An unexpected error occurred: {e}")


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
