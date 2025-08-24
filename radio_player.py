#!/usr/bin/env python3
"""random_playlist_player.py

Continuously plays random songs by:
  1. Randomly choosing a *playlist* subfolder inside a root music directory (e.g. "Songs/Classic Rock").
  2. (Optionally) waiting a per‑playlist preset delay ("warmup" / intro time).
  3. Randomly choosing a song file within that playlist.
  4. Playing it to completion (blocking), then repeating forever with a *different* random playlist when possible.

FEATURES
--------
* Supports common audio formats (configurable extensions list).
* Per‑playlist or default delay before each song (e.g. give you time to see what was chosen).
* Graceful handling of empty / missing playlists.
* Avoids immediately repeating the same playlist back‑to‑back (unless only one playlist available).
* Clean shutdown with Ctrl+C.
* Pluggable playback backends:
    - **python-vlc** (if installed) – preferred, broad format support.
    - **playsound** (if installed) – simple blocking playback.
    - **ffplay** (if available on PATH) – via subprocess.
    - **afplay** (macOS), **mpg123**, **mpv**, or **vlc** CLI as fallbacks.
  The first working backend found will be used. You can force one via CLI flag.
* Optional log output with timestamps.

USAGE
-----
    python random_playlist_player.py --root "/path/to/Songs" \
        --default-delay 2 \
        --extensions mp3 flac wav m4a \
        --backend auto

Add per‑playlist delays in a small JSON file (see "PER-PLAYLIST DELAYS" below):
    python random_playlist_player.py --delay-map delays.json

Press Ctrl+C to stop after the current track.

PER-PLAYLIST DELAYS JSON EXAMPLE
--------------------------------
{
  "Classic Rock": 5,
  "Reggae": 2,
  "Jazz": 0
}

Each key is the *folder name* (not the full path) under the root directory; value = seconds to wait before picking a song within that playlist.

REQUIREMENTS (OPTIONAL)
-----------------------
Install whichever backend you prefer (only one needed):
    pip install python-vlc
    pip install playsound==1.2.2
Or rely on command line players present on your system (ffplay, afplay, etc.).

LIMITATIONS
-----------
Determining the song duration is delegated to the backend; blocking playback is used so we simply wait until completion.

"""

from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Sequence

# ---------------------------------------------------------------------------
# Playback Backend Abstractions
# ---------------------------------------------------------------------------


class PlaybackError(Exception):
    pass


@dataclass
class Backend:
    name: str
    play_func: Callable[[Path], None]  # Must block until playback finishes

    def play(self, filepath: Path) -> None:
        try:
            self.play_func(filepath)
        except KeyboardInterrupt:
            raise
        except Exception as e:  # noqa: BLE001
            raise PlaybackError(f"Backend '{self.name}' failed: {e}") from e


# Backend factory functions -------------------------------------------------


def backend_python_vlc() -> Optional[Backend]:
    try:
        import vlc  # type: ignore
    except Exception:  # noqa: BLE001
        return None

    def _play(path: Path) -> None:
        instance = vlc.Instance()
        player = instance.media_player_new()
        media = instance.media_new(str(path))
        player.set_media(media)
        if player.play() == -1:
            raise PlaybackError("VLC failed to start playback")
        # Poll until playback ends
        while True:
            state = player.get_state()
            if state in (vlc.State.Ended, vlc.State.Stopped, vlc.State.Error):
                break
            time.sleep(0.25)

    return Backend("python-vlc", _play)


def backend_playsound() -> Optional[Backend]:
    try:
        from playsound import playsound  # type: ignore
    except Exception:  # noqa: BLE001
        return None

    def _play(path: Path) -> None:
        playsound(str(path))  # blocking

    return Backend("playsound", _play)


def backend_subprocess(command: Sequence[str], name: str) -> Backend:
    def _play(path: Path) -> None:
        # Replace placeholder {file} if present, else append file path
        if any("{file}" in part for part in command):
            cmd = [part.replace("{file}", str(path)) for part in command]
        else:
            cmd = list(command) + [str(path)]
        proc = subprocess.Popen(cmd)  # noqa: S603, S607
        try:
            proc.wait()
        except KeyboardInterrupt:
            proc.terminate()
            raise
        if proc.returncode not in (0, None):
            raise PlaybackError(f"Command {cmd} exited with {proc.returncode}")

    return Backend(name, _play)


def discover_backends(preferred: Optional[str] = None) -> List[Backend]:
    """Return a list of available backends (ordered by desirability)."""
    candidates: List[Backend] = []

    # Ordered attempts
    constructed: List[Optional[Backend]] = [
        backend_python_vlc(),
        backend_playsound(),
    ]
    for b in constructed:
        if b is not None:
            candidates.append(b)

    # CLI players (only if on PATH)
    cli_players = [
        ("ffplay", ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", "{file}"]),
        ("afplay", ["afplay", "{file}"]),  # macOS
        ("mpg123", ["mpg123", "{file}"]),
        ("mpv", ["mpv", "--no-terminal", "--quiet", "{file}"]),
        ("vlc", ["vlc", "--intf", "dummy", "--play-and-exit", "{file}"]),
    ]
    for name, cmd in cli_players:
        if shutil.which(cmd[0]):
            candidates.append(backend_subprocess(cmd, name))

    if preferred and preferred != "auto":
        filtered = [b for b in candidates if b.name == preferred]
        if not filtered:
            raise SystemExit(
                f"Requested backend '{preferred}' not available. Available: {[b.name for b in candidates]}"
            )
        return filtered

    if not candidates:
        raise SystemExit(
            "No playback backend found. Install 'python-vlc' or 'playsound', or have ffplay/mpv/vlc etc. on PATH."
        )

    return candidates


# ---------------------------------------------------------------------------
# Core Logic
# ---------------------------------------------------------------------------


def list_playlists(root: Path) -> List[Path]:
    return [p for p in root.iterdir() if p.is_dir()]


def list_songs(playlist_dir: Path, exts: Iterable[str]) -> List[Path]:
    exts_lc = {e.lower() for e in exts}
    return [
        p
        for p in playlist_dir.iterdir()
        if p.is_file() and p.suffix.lower().lstrip(".") in exts_lc
    ]


def load_delay_map(path: Optional[Path]) -> Dict[str, float]:
    if not path:
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            raw = json.load(f)
        return {str(k): float(v) for k, v in raw.items()}
    except FileNotFoundError:
        print(f"[WARN] Delay map file not found: {path}")
    except Exception as e:  # noqa: BLE001
        print(f"[WARN] Failed to read delay map {path}: {e}")
    return {}


def choose_different(prev: Optional[Path], options: List[Path]) -> Optional[Path]:
    if not options:
        return None
    if prev and len(options) > 1:
        filtered = [o for o in options if o != prev]
        if filtered:
            return random.choice(filtered)
    return random.choice(options)


def play_loop(
    root: Path,
    extensions: List[str],
    delay_map: Dict[str, float],
    default_delay: float,
    backend: Backend,
    verbose: bool,
) -> None:
    print(f"Using backend: {backend.name}")
    prev_playlist: Optional[Path] = None
    while True:
        playlists = list_playlists(root)
        if not playlists:
            print("[ERROR] No playlist subfolders found. Exiting.")
            return
        playlist = choose_different(prev_playlist, playlists)
        if playlist is None:
            print("[ERROR] Could not select a playlist.")
            return

        print(f"[RADIO] Selected playlist: {playlist.name}")

        songs = list_songs(playlist, extensions)
        if not songs:
            if verbose:
                print(
                    f"[WARN] Playlist '{playlist.name}' has no songs matching {extensions}. Skipping."
                )
            # Avoid getting stuck: mark as prev and continue
            prev_playlist = playlist
            time.sleep(0.5)
            continue
        delay = delay_map.get(playlist.name, default_delay)
        if delay > 0:
            if verbose:
                print(
                    f"[INFO] Waiting {delay:.1f}s before selecting song in '{playlist.name}'..."
                )
            time.sleep(delay)
        song = random.choice(songs)

        print(f"[RADIO] Now playing: {song.stem} from {playlist.name}")

        if verbose:
            print(f"[PLAY] Playlist: {playlist.name} | Song: {song.name}")
        try:
            backend.play(song)
            print(f"[RADIO] Finished playing: {song.stem}")
        except KeyboardInterrupt:
            print("[INFO] Stopping after user interrupt.")
            return
        except PlaybackError as e:
            print(f"[ERROR] {e}")
            # brief pause to avoid tight failure loop
            time.sleep(1)
        prev_playlist = playlist


# ---------------------------------------------------------------------------
# CLI Interface
# ---------------------------------------------------------------------------


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Continuously play random songs from random playlist subfolders."
    )
    parser.add_argument(
        "--root",
        type=Path,
        required=True,
        help="Root music directory containing playlist subfolders.",
    )
    parser.add_argument(
        "--extensions",
        nargs="+",
        default=["mp3", "flac", "wav", "m4a", "ogg"],
        help="File extensions to consider (no dots).",
    )
    parser.add_argument(
        "--delay-map",
        type=Path,
        help="JSON file mapping playlist folder names to delay seconds.",
    )
    parser.add_argument(
        "--default-delay",
        type=float,
        default=0.0,
        help="Default delay (seconds) before choosing song in a playlist.",
    )
    parser.add_argument(
        "--backend",
        choices=[
            "auto",
            "python-vlc",
            "playsound",
            "ffplay",
            "afplay",
            "mpg123",
            "mpv",
            "vlc",
        ],
        default="auto",
        help="Force a specific backend or auto-detect.",
    )
    parser.add_argument(
        "--verbose", action="store_true", help="Enable informational logging."
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    root: Path = args.root
    if not root.is_dir():
        print(f"Root directory does not exist or is not a directory: {root}")
        return 1

    delay_map = load_delay_map(args.delay_map)
    try:
        backends = discover_backends(args.backend)
    except SystemExit as e:  # propagate message
        print(e)
        return 1
    backend = backends[0]

    try:
        play_loop(
            root, args.extensions, delay_map, args.default_delay, backend, args.verbose
        )
    except KeyboardInterrupt:
        print("[INFO] Exiting.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
