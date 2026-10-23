"""Private downloads and no-overwrite promotion of the exact auditioned bytes."""

from pathlib import Path
import os
import shutil
import tempfile

from mutagen.mp3 import MP3
from mutagen import MutagenError
from mutagen.id3 import ID3

from neuralcast.audio.download import tag_mp3, youtube_to_mp3
from neuralcast.playlists.library import sanitize_filename_component
from .models import Candidate
from .storage import ConflictError, file_hash


def artwork_type(data: bytes) -> str | None:
    """Allow raster covers only; never serve active content from ID3 metadata."""
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def save_artwork(audio: Path, destination: Path) -> bool:
    """Retain embedded art separately without changing the auditioned MP3 bytes."""
    try:
        frames = ID3(audio).getall("APIC")
    except MutagenError:
        return False
    # Prefer the front cover, falling back to another supported embedded image.
    for frame in sorted(frames, key=lambda frame: frame.type != 3):
        data = frame.data
        if not data or len(data) > 10 * 1024 * 1024 or not artwork_type(data):
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            dir=destination.parent, prefix=".cover-", delete=False
        ) as temporary:
            staged = Path(temporary.name)
            try:
                temporary.write(data)
                temporary.flush()
                os.fsync(temporary.fileno())
                os.replace(staged, destination)
            finally:
                staged.unlink(missing_ok=True)
        return True
    return False


def download(candidate: Candidate, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        dir=destination.parent, prefix=".download-"
    ) as temp:
        path = Path(temp) / "audition.mp3"
        youtube_to_mp3(candidate.source_url, str(path), use_search=False)
        if not MP3(path).info.length > 0:
            raise ValueError("Downloaded recording is not playable MP3 audio")
        tag_mp3(
            str(path),
            candidate.artist,
            candidate.title,
            candidate.year,
            candidate.playlist,
            candidate.album,
        )
        os.replace(path, destination)


def target_path(songs_root: Path, candidate: Candidate, playlist: str) -> Path:
    name = (
        f"{sanitize_filename_component(candidate.artist)} - "
        f"{sanitize_filename_component(candidate.title)}.mp3"
    )
    return songs_root / playlist / name


def check_target(target: Path, expected_hash: str) -> None:
    if target.parent.is_symlink():
        raise ConflictError("A playlist media directory must not be a symlink")
    if target.is_symlink() or (
        target.exists() and (not target.is_file() or file_hash(target) != expected_hash)
    ):
        raise ConflictError(f"A different recording already exists at {target.name}")


def promote(source: Path, target: Path, expected_hash: str) -> None:
    """Allow retries after installation, but never replace another recording."""
    check_target(target, expected_hash)
    if target.exists():
        return
    if file_hash(source) != expected_hash:
        raise ConflictError("Audition changed before promotion")
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        dir=target.parent, prefix=".audition-", delete=False
    ) as temporary:
        staged = Path(temporary.name)
        try:
            with source.open("rb") as handle:
                shutil.copyfileobj(handle, temporary)
            temporary.flush()
            os.fsync(temporary.fileno())
            if file_hash(staged) != expected_hash:
                raise ConflictError("Audition changed while copying")
            # NamedTemporaryFile is 0600; Liquidsoap runs under a different UID.
            staged.chmod(0o644)
            # link is exclusive, unlike replace: another writer cannot be overwritten.
            try:
                os.link(staged, target)
            except FileExistsError:
                check_target(target, expected_hash)
        finally:
            staged.unlink(missing_ok=True)
