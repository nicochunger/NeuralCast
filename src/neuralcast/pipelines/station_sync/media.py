"""Live media operations for station playlist synchronization."""

from __future__ import annotations

import pathlib
from subprocess import CalledProcessError
from typing import List

from mutagen.easyid3 import EasyID3

from neuralcast.audio.download import tag_mp3, youtube_to_mp3
from neuralcast.models import Song
from neuralcast.playlists.utils import normalize_year_value

from .models import PlaylistLog
from .persistence import _capture_output, _emit_captured_lines


class DefaultMediaLibrary:
    def apply_override(
        self,
        song: Song,
        song_path: pathlib.Path | None,
        playlist_name: str,
        *,
        dry_run: bool,
        log: PlaylistLog,
    ) -> bool:
        url = song.override_url
        if not song.artist or not song.title:
            log.warning(f"override skipped; missing artist/title for URL {url}")
            return False

        if not url or not any(host in url.lower() for host in ("youtube.com", "youtu.be")):
            log.warning(f"override skipped; unsupported URL {url}")
            return False

        if song_path is None:
            log.warning(
                f"override skipped; could not determine target path for {song.artist} - {song.title}"
            )
            return False

        log.change(f"🔁 forced YouTube override: {song.artist} - {song.title}")
        if dry_run:
            log.info(f"🧪 dry-run: would replace {song.artist} - {song.title} via override")
            return False

        file_existed = song_path.exists()
        backup_path: pathlib.Path | None = None
        backup_created = False

        try:
            if file_existed:
                backup_path = song_path.with_suffix(song_path.suffix + ".bak")
                if backup_path.exists():
                    backup_path.unlink()
                song_path.rename(backup_path)
                backup_created = True

            _, download_lines = _capture_output(
                lambda: youtube_to_mp3(url, str(song_path), use_search=False)
            )
            _emit_captured_lines(
                download_lines,
                logger=log.change,
                include_plain=True,
            )
            _, tag_lines = _capture_output(
                lambda: tag_mp3(
                    str(song_path),
                    song.artist,
                    song.title,
                    song.year,
                    playlist_name,
                    song.album,
                    log_prefix="      ",
                )
            )
            _emit_captured_lines(tag_lines, logger=log.warning)

            if backup_path and backup_path.exists():
                backup_path.unlink()

            song.override_url = None
            replacement_note = (
                "🔁 override replaced existing file"
                if file_existed
                else "⬇️ override downloaded new file"
            )
            log.change(f"{replacement_note}: {song.artist} - {song.title}")
            return True
        except (CalledProcessError, Exception) as exc:
            log.error(
                f"override failed; original retained for {song.artist} - {song.title}: {exc}"
            )
            if song_path.exists() and backup_created:
                try:
                    song_path.unlink()
                except Exception:
                    pass
            if backup_created and backup_path and backup_path.exists():
                try:
                    backup_path.rename(song_path)
                except Exception as restore_exc:
                    log.warning(
                        f"failed to restore original file from backup: {restore_exc}"
                    )
            return False

    def audit_existing_tags(
        self,
        existing_songs: list[tuple[Song, pathlib.Path]],
        playlist_name: str,
        *,
        repair: bool,
        log: PlaylistLog,
    ) -> int:
        mismatches = 0
        for song, song_path in existing_songs:
            track_label = f"{song.artist or 'Unknown Artist'} - {song.title or song_path.stem}"
            status_lines: List[str] = []
            try:
                audio = EasyID3(str(song_path))
                cur_artist = audio.get("artist", [""])[0] if audio.get("artist") else ""
                cur_title = audio.get("title", [""])[0] if audio.get("title") else ""
                cur_year = audio.get("date", [""])[0] if audio.get("date") else ""
                cur_genre = audio.get("genre", [""])[0] if audio.get("genre") else ""
                cur_album = audio.get("album", [""])[0] if audio.get("album") else ""
            except Exception as exc:
                status_lines.append(
                    f"⚠️ Cannot read tags ({exc}); metadata + album art need repair"
                )
                if repair:
                    _, tag_lines = _capture_output(
                        lambda: tag_mp3(
                            str(song_path),
                            song.artist,
                            song.title,
                            song.year,
                            playlist_name,
                            song.album,
                            log_prefix="      ",
                        )
                    )
                    _emit_captured_lines(tag_lines, logger=log.warning)
                mismatches += 1
                for line in status_lines:
                    log.change(f"{track_label}: {line}")
                continue

            needs = []
            if cur_artist.strip() != song.artist.strip():
                needs.append("artist")
            if cur_title.strip() != song.title.strip():
                needs.append("title")
            year_value = normalize_year_value(song.year) or ""
            current_year_value = normalize_year_value(cur_year) or cur_year.strip()
            if year_value and year_value.casefold() != "unknown":
                if current_year_value != year_value:
                    needs.append("year")
            if cur_genre.strip() != playlist_name:
                needs.append("genre")
            if song.album and str(song.album).strip():
                if cur_album.strip() != str(song.album).strip():
                    needs.append("album")

            if not needs:
                continue

            action = "Updating" if repair else "Would update"
            status_lines.append(f"{action} fields: {', '.join(needs)}")
            refresh_art = "album" in needs
            if refresh_art:
                status_lines.append(
                    "Reapplying album art" if repair else "Would reapply album art"
                )
            if repair:
                _, tag_lines = _capture_output(
                    lambda: tag_mp3(
                        str(song_path),
                        song.artist,
                        song.title,
                        song.year,
                        playlist_name,
                        song.album,
                        log_prefix="      ",
                        refresh_art=refresh_art,
                        apply_replaygain=False,
                    )
                )
                _emit_captured_lines(tag_lines, logger=log.warning)
            mismatches += 1
            for line in status_lines:
                log.change(f"{track_label}: {line}")

        return mismatches

    def download_song(
        self,
        song: Song,
        song_path: pathlib.Path,
        playlist_name: str,
        *,
        log: PlaylistLog,
    ) -> None:
        _, download_lines = _capture_output(
            lambda: youtube_to_mp3(f"{song.artist} {song.title}", str(song_path))
        )
        _emit_captured_lines(
            download_lines,
            logger=log.change,
            include_plain=True,
        )
        _, tag_lines = _capture_output(
            lambda: tag_mp3(
                str(song_path),
                song.artist,
                song.title,
                song.year,
                playlist_name,
                song.album,
                log_prefix="      ",
            )
        )
        _emit_captured_lines(tag_lines, logger=log.warning)

    def delete_file(
        self,
        song_path: pathlib.Path,
        *,
        log: PlaylistLog,
    ) -> None:
        try:
            song_path.unlink()
            log.change(f"🗑️ deleted invalid file: {song_path.name}")
        except Exception as exc:
            log.error(f"failed to delete {song_path.name}: {exc}")


__all__ = ["DefaultMediaLibrary"]
