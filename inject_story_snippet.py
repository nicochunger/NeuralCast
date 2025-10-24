"""Generate and inject a narrated story after an upcoming AzuraCast track."""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import os
import pathlib
import re
import textwrap
import time
import warnings
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence

import requests
from dotenv import load_dotenv
from requests import Response
from urllib3.exceptions import InsecureRequestWarning

from openai_utils import openai_speech, openai_text_completion
from playlist_utils import sanitize_filename_component

STORY_PROMPT_PATH = pathlib.Path("stories/story_prompt.md")
TTS_INSTRUCTIONS_PATH = pathlib.Path("stories/tts_story_instructions.md")
STORY_OUTPUT_DIR = pathlib.Path("stories") / "snippets"


@dataclass
class UpcomingTrack:
    queue_id: str
    song_id: Optional[str]
    artist: str
    title: str
    starts_at: Optional[dt.datetime]
    duration: Optional[int]
    raw: Dict


@dataclass
class StoryAssets:
    text_path: pathlib.Path
    audio_path: pathlib.Path
    story_text: str


class AzuraCastClient:
    """Thin AzuraCast API helper focused on queue manipulation."""

    def __init__(self, base_url: str, api_key: str, verify_tls: bool = False):
        self.base_url = base_url.rstrip("/")
        self.verify_tls = verify_tls
        self.session = requests.Session()
        self.session.headers.update({"X-API-Key": api_key})

        if not verify_tls:
            warnings.filterwarnings("ignore", category=InsecureRequestWarning)

    def _build_url(self, path: str) -> str:
        return f"{self.base_url}/{path.lstrip('/')}"

    def _request(self, method: str, path: str, **kwargs) -> Response:
        kwargs.setdefault("timeout", 15)
        kwargs.setdefault("verify", self.verify_tls)
        response = self.session.request(
            method=method, url=self._build_url(path), **kwargs
        )
        response.raise_for_status()
        return response

    def get_stations(self) -> List[Dict]:
        return self._request("GET", "/api/stations").json()

    def get_now_playing(self, station: str) -> Dict:
        try:
            return self._request("GET", f"/api/nowplaying/{station}").json()
        except requests.HTTPError as exc:  # fallback to aggregate endpoint
            if exc.response is not None and exc.response.status_code == 404:
                payload = self._request("GET", "/api/nowplaying").json()
                for station_payload in payload:
                    shortcode = station_payload.get("station", {}).get("shortcode")
                    if shortcode == station:
                        return station_payload
            raise

    def get_upcoming_queue(self, station: str) -> List[Dict]:
        payload = self._request("GET", f"/api/station/{station}/queue").json()
        if isinstance(payload, dict) and "data" in payload:
            data = payload.get("data") or []
            return data if isinstance(data, list) else []
        if isinstance(payload, list):
            return payload
        return []

    def upload_media(
        self, station: str, file_path: pathlib.Path, remote_path: Optional[str] = None
    ) -> Dict:
        destination = remote_path or file_path.name
        payload = {
            "path": destination,
            "file": base64.b64encode(file_path.read_bytes()).decode("ascii"),
        }
        try:
            response = self._request(
                "POST",
                f"/api/station/{station}/files",
                json=payload,
            )
        except requests.HTTPError as exc:
            detail = ""
            if exc.response is not None:
                try:
                    detail = exc.response.json()
                except Exception:  # noqa: BLE001
                    detail = exc.response.text
            raise RuntimeError(
                f"Failed to upload media {file_path.name} to station {station}: {detail}"
            ) from exc
        return response.json()

    def send_telnet_command(self, station_id: int, command: str) -> Dict:
        payload = {"command": command}
        response = self._request(
            "PUT",
            f"/api/admin/debug/station/{station_id}/telnet",
            json=payload,
        )
        return response.json()

def parse_upcoming_queue(queue_payload: Sequence[Dict]) -> List[UpcomingTrack]:
    parsed: List[UpcomingTrack] = []
    for entry in queue_payload:
        queue_id = entry.get("id") or entry.get("queue_id") or entry.get("unique_id")
        song = entry.get("song") or {}
        artist = song.get("artist") or entry.get("artist") or ""
        title = song.get("title") or entry.get("title") or ""
        song_id = song.get("id") or entry.get("song_id")

        starts_at_raw = (
            entry.get("play_at") or entry.get("played_at") or entry.get("cued_at")
        )
        starts_at = None
        if isinstance(starts_at_raw, (int, float)):
            starts_at = dt.datetime.fromtimestamp(starts_at_raw, tz=dt.timezone.utc)
        elif isinstance(starts_at_raw, str):
            try:
                starts_at = dt.datetime.fromisoformat(
                    starts_at_raw.replace("Z", "+00:00")
                )
            except ValueError:
                starts_at = None

        duration = None
        if "duration" in entry:
            try:
                duration = int(entry["duration"])
            except (TypeError, ValueError):
                duration = None
        elif "length" in entry:
            try:
                duration = int(entry["length"])
            except (TypeError, ValueError):
                duration = None

        if not queue_id:
            fallback_candidates = [
                song_id,
                entry.get("media_id"),
                entry.get("played_at"),
                entry.get("cued_at"),
            ]
            for candidate in fallback_candidates:
                if candidate:
                    queue_id = str(candidate)
                    break
        if not queue_id:
            queue_id = f"entry-{len(parsed)}"

        if not title:
            continue

        parsed.append(
            UpcomingTrack(
                queue_id=queue_id,
                song_id=song_id,
                artist=artist,
                title=title,
                starts_at=starts_at,
                duration=duration,
                raw=entry,
            )
        )
    return parsed


def select_song_with_ai(
    upcoming: Sequence[UpcomingTrack],
    model: str = "gpt-5-mini",
) -> UpcomingTrack:
    if not upcoming:
        raise RuntimeError("No upcoming songs available to choose from.")

    synopsis_lines = []
    for idx, track in enumerate(upcoming, start=1):
        parts = [f"{idx}. {track.artist} - {track.title}"]
        if track.duration:
            parts.append(f"({track.duration}s)")
        if track.starts_at:
            parts.append(f"plays at {track.starts_at.isoformat()}")
        if track.raw.get("source") == "now_playing":
            remaining = track.raw.get("remaining")
            remaining_note = f"{remaining}s remaining" if remaining is not None else "currently playing"
            parts.append(f"[NOW PLAYING: {remaining_note}]")
        parts.append(f"queue_id={track.queue_id}")
        synopsis_lines.append(" | ".join(parts))

    user_prompt = textwrap.dedent(
        f"""
        Elegí cuál de las siguientes canciones merece una historia corta para la radio.
        Respondé con JSON (sin texto adicional) con el formato:
        {{"queue_id": "ID", "reason": "breve frase"}}

        Canciones:
        {chr(10).join(synopsis_lines)}
        """
    ).strip()

    response_text = openai_text_completion(
        prompt=user_prompt,
        system_prompt="Sos un productor de radio argentino. Elegí solo una canción y devolvé JSON válido.",
        model=model,
    )

    try:
        payload = json.loads(response_text)
        chosen_queue_id = payload.get("queue_id")
    except json.JSONDecodeError:
        chosen_queue_id = None

    if not chosen_queue_id:
        # fallback: search for first matching mention
        lower_text = response_text.lower()
        for track in upcoming:
            if track.queue_id.lower() in lower_text:
                chosen_queue_id = track.queue_id
                break
            descriptor = f"{track.artist.lower()} - {track.title.lower()}"
            if descriptor in lower_text:
                chosen_queue_id = track.queue_id
                break

    if not chosen_queue_id:
        raise RuntimeError(
            f"OpenAI response did not include a recognizable queue ID. Raw response: {response_text}"
        )

    for track in upcoming:
        if track.queue_id == chosen_queue_id:
            return track

    raise RuntimeError(
        f"Selected queue ID {chosen_queue_id} not found in upcoming list."
    )


def cleanup_story_text(raw: str) -> str:
    """Strip URLs, Markdown link remnants, and reference markers from generated copy."""
    text = re.sub(r"\[([^\]]+)\]\(\s*https?://[^\)]+\)", r"\1", raw)
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"\[\s*\d+\s*\]", "", text)
    text = re.sub(r"\[[^\]]+\]", "", text)
    text = re.sub(
        r"\(\s*(?:[a-z][a-z0-9-]*\.)+[a-z]{2,}\s*\)", "", text, flags=re.IGNORECASE
    )
    text = re.sub(r"\b(?:[a-z][a-z0-9-]*\.)+[a-z]{2,}\b", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\(\s*\)", "", text)
    text = text.replace("((", "(").replace("))", ")")
    cleaned_lines = [
        re.sub(r"\s{2,}", " ", line).strip() for line in text.splitlines()
    ]
    cleaned = "\n".join(line for line in cleaned_lines if line)
    return cleaned.strip()


def generate_story_text(artist: str, title: str, station: str) -> str:
    template = STORY_PROMPT_PATH.read_text(encoding="utf-8")
    prompt = (
        template.replace("[ARTIST]", artist)
        .replace("[TITLE]", title)
        .replace("[STATION]", station)
    )
    story = openai_text_completion(prompt=prompt, model="gpt-5-search-api")
    return cleanup_story_text(story)


def synthesize_story_audio(story_text: str, outfile: pathlib.Path) -> None:
    tts_instructions = TTS_INSTRUCTIONS_PATH.read_text(encoding="utf-8").strip()
    openai_speech(
        text=story_text,
        outfile=str(outfile),
        model="gpt-4o-mini-tts",
        voice="ash",
        instructions=tts_instructions,
    )


def write_story_text_file(story_text: str, outfile: pathlib.Path) -> None:
    outfile.write_text(story_text + "\n", encoding="utf-8")


def ensure_story_assets(artist: str, title: str, story_text: str) -> StoryAssets:
    safe_artist = sanitize_filename_component(artist).replace("'", "")
    safe_title = sanitize_filename_component(title).replace("'", "")
    base_name = f"Story_{safe_artist}_{safe_title}"
    audio_path = STORY_OUTPUT_DIR / f"{base_name}.mp3"
    text_path = STORY_OUTPUT_DIR / f"{base_name}.txt"

    STORY_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    write_story_text_file(story_text, text_path)
    synthesize_story_audio(story_text, audio_path)

    return StoryAssets(
        text_path=text_path, audio_path=audio_path, story_text=story_text
    )


def derive_media_id(upload_response: Dict, file_name: str) -> Optional[str]:
    if not upload_response:
        return None
    if "data" in upload_response:
        data = upload_response["data"]
        if isinstance(data, dict):
            if "media" in data and isinstance(data["media"], dict):
                media = data["media"]
                return str(
                    media.get("id")
                    or media.get("media_id")
                    or media.get("unique_id")
                    or ""
                )
            for key in ("id", "media_id", "unique_id", "song_id"):
                if key in data and data[key]:
                    return str(data[key])
        if isinstance(data, list):
            for item in data:
                candidate = derive_media_id(item, file_name)
                if candidate:
                    return candidate
    for key in ("id", "media_id", "song_id", "unique_id"):
        if key in upload_response and upload_response[key]:
            return str(upload_response[key])

    meta = upload_response.get("meta") if isinstance(upload_response, dict) else None
    if isinstance(meta, dict):
        for key in ("id", "media_id", "unique_id"):
            if key in meta and meta[key]:
                return str(meta[key])

    message = (
        upload_response.get("message") if isinstance(upload_response, dict) else None
    )
    if message:
        print(f"Upload response message: {message}")
    print(
        f"Warning: Could not determine media ID from upload response; manual queueing may be required. "
        f"Response keys: {list(upload_response.keys())}"
    )
    return None


def escape_annotation_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def build_interrupting_command(
    media_full_path: str,
    story_artist: str,
    story_title: str,
    duration: Optional[int],
) -> str:
    annotations = [
        f'title="{escape_annotation_value(story_title)}"',
        f'artist="{escape_annotation_value(story_artist)}"',
    ]
    if duration is not None and duration > 0:
        annotations.append(f'duration="{duration}"')
    annotation_block = ",".join(annotations)
    return (
        f"interrupting_requests.push annotate:{annotation_block}:"
        f"{media_full_path}"
    )


def is_song_match(song_payload: Dict, track: UpcomingTrack) -> bool:
    if not song_payload:
        return False
    payload_song_id = song_payload.get("id")
    if payload_song_id and track.song_id and payload_song_id == track.song_id:
        return True
    payload_artist = (song_payload.get("artist") or "").strip().lower()
    payload_title = (song_payload.get("title") or "").strip().lower()
    return (
        payload_artist == (track.artist or "").strip().lower()
        and payload_title == (track.title or "").strip().lower()
    )


def extract_telnet_response(log_payload: Dict) -> Optional[str]:
    logs = log_payload.get("logs")
    if not isinstance(logs, list):
        return None
    for entry in reversed(logs):
        context = entry.get("context")
        if not isinstance(context, dict):
            continue
        response_lines = context.get("response")
        if isinstance(response_lines, list) and response_lines:
            return response_lines[-1]
    return None


def wait_for_track_and_inject(
    client: AzuraCastClient,
    station_slug: str,
    station_id: Optional[int],
    preceding_track: UpcomingTrack,
    target_track: UpcomingTrack,
    telnet_command: str,
    lead_seconds: int,
    timeout_seconds: int,
    poll_interval: int,
) -> Optional[str]:
    if station_id is None:
        raise RuntimeError("Station ID is required to send telnet commands.")

    deadline = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=timeout_seconds)
    track_detected = False
    pushed_request_id: Optional[str] = None

    print(
        f"Waiting for preceding track '{preceding_track.artist} - {preceding_track.title}' to reach the injection point..."
    )
    while dt.datetime.now(dt.timezone.utc) < deadline:
        status = client.get_now_playing(station_slug)
        now_payload = status.get("now_playing") or {}
        song_payload = now_payload.get("song") or {}
        remaining = now_payload.get("remaining")

        if is_song_match(song_payload, preceding_track):
            track_detected = True
            if remaining is not None:
                print(f"Preceding track playing; remaining time: {remaining}s")
            if remaining is None or remaining <= lead_seconds:
                print("Injecting story via interrupting_requests before the selected song...")
                response = client.send_telnet_command(station_id, telnet_command)
                pushed_request_id = extract_telnet_response(response)
                break
        elif track_detected:
            print(
                "Preceding track finished earlier than expected; injecting story immediately before the selected song..."
            )
            response = client.send_telnet_command(station_id, telnet_command)
            pushed_request_id = extract_telnet_response(response)
            break
        elif is_song_match(song_payload, target_track):
            raise RuntimeError(
                "The selected song started playing before the story could be queued; injection would no longer precede the track."
            )

        time.sleep(max(1, poll_interval))

    if pushed_request_id is None:
        if not track_detected:
            raise RuntimeError(
                f"Timed out waiting for preceding track '{preceding_track.artist} - {preceding_track.title}' to play."
            )

    return pushed_request_id


def run(args: argparse.Namespace) -> None:
    load_dotenv()
    api_key = os.getenv("AZURACAST_API_KEY")
    if not api_key:
        raise RuntimeError("AZURACAST_API_KEY is not set in the environment.")

    base_url = args.base_url.rstrip("/")
    client = AzuraCastClient(
        base_url=base_url, api_key=api_key, verify_tls=args.verify_tls
    )

    stations = client.get_stations()
    station = None
    for station_entry in stations:
        shortcode = station_entry.get("shortcode") or station_entry.get(
            "station_short_name"
        )
        if shortcode == args.station:
            station = station_entry
            break
    if station is None:
        available = ", ".join(
            station_entry.get("shortcode", "?") for station_entry in stations
        )
        raise RuntimeError(
            f"Station '{args.station}' not found. Available: {available}"
        )

    print(f"Using station '{args.station}' ({station.get('name', 'unknown name')}).")

    now_playing_payload = client.get_now_playing(args.station)
    current_np_entry = now_playing_payload.get("now_playing") or {}
    current_song = current_np_entry.get("song") or {}
    current_remaining = current_np_entry.get("remaining")
    current_duration = current_np_entry.get("duration")

    print(
        f"Now playing: {current_song.get('artist', 'Unknown Artist')} - {current_song.get('title', 'Unknown Title')}"
    )

    raw_queue = client.get_upcoming_queue(args.station)
    upcoming_tracks = parse_upcoming_queue(raw_queue)
    if not upcoming_tracks:
        raise RuntimeError("No upcoming tracks found in station queue.")

    selection_pool = upcoming_tracks[: args.selection_count]
    selected_track = select_song_with_ai(selection_pool)
    print(
        f"Selected upcoming song: {selected_track.artist} - {selected_track.title} (queue_id={selected_track.queue_id})"
    )

    preceding_track: Optional[UpcomingTrack] = None
    for idx, track in enumerate(upcoming_tracks):
        if track.queue_id == selected_track.queue_id:
            if idx == 0:
                if not current_song or not current_song.get("title"):
                    raise RuntimeError(
                        "Unable to determine the currently playing song to schedule the story before the selected track."
                    )
                preceding_track = UpcomingTrack(
                    queue_id=current_song.get("id") or "now-playing",
                    song_id=current_song.get("id"),
                    artist=current_song.get("artist", ""),
                    title=current_song.get("title", ""),
                    starts_at=None,
                    duration=(
                        int(current_duration)
                        if current_duration is not None
                        else None
                    ),
                    raw={
                        "source": "now_playing",
                        "remaining": current_remaining,
                    },
                )
            else:
                preceding_track = upcoming_tracks[idx - 1]
            break

    if preceding_track is None:
        raise RuntimeError(
            "Could not locate the track that precedes the selected song in the upcoming queue."
        )
    monitoring_source = preceding_track.raw.get("source")
    if monitoring_source == "now_playing":
        print(
            "Story will play after the current song and before the selected track."
        )
    else:
        print(
            f"Story will play after '{preceding_track.artist} - {preceding_track.title}' and before the selected track."
        )

    station_display_name = (station.get("name") or args.station).strip()
    if args.station.lower() == "neuralforge":
        station_display_name = "NéuralForsh"
    story_text = generate_story_text(
        selected_track.artist, selected_track.title, station_display_name
    )
    assets = ensure_story_assets(
        selected_track.artist, selected_track.title, story_text
    )
    print(f"Story text saved to {assets.text_path}")
    print(f"Story audio saved to {assets.audio_path}")

    if args.dry_run:
        print("Dry-run mode enabled; skipping upload and queue injection.")
        return

    upload_response = client.upload_media(
        args.station,
        assets.audio_path,
        remote_path=f"AI Stories/{assets.audio_path.name}",
    )
    media_id = derive_media_id(upload_response, assets.audio_path.name)
    if not media_id:
        raise RuntimeError("Failed to determine media ID for uploaded story audio.")
    print(f"Uploaded story MP3. Media ID: {media_id}")

    upload_path = upload_response.get("path") if isinstance(upload_response, dict) else None
    if not upload_path:
        raise RuntimeError("Upload response missing storage path; cannot schedule playback.")

    full_media_path = f"/var/azuracast/stations/{args.station}/media/{upload_path}"
    story_duration = None
    if isinstance(upload_response, dict):
        length_val = upload_response.get("length")
        if length_val is not None:
            try:
                story_duration = int(float(length_val))
            except (TypeError, ValueError):
                story_duration = None

    telnet_command = build_interrupting_command(
        media_full_path=full_media_path,
        story_artist="NeuralCast AI",
        story_title=f"Historia: {selected_track.title}",
        duration=story_duration,
    )
    try:
        request_id = wait_for_track_and_inject(
            client=client,
            station_slug=args.station,
            station_id=station.get("id"),
            preceding_track=preceding_track,
            target_track=selected_track,
            telnet_command=telnet_command,
            lead_seconds=args.inject_lead_seconds,
            timeout_seconds=args.inject_timeout,
            poll_interval=args.poll_interval,
        )
    except RuntimeError as exc:
        print(f"Error while waiting to inject story: {exc}")
        print(
            "The story MP3 is uploaded; you can inject it manually via Liquidsoap telnet with:\n"
            f"  {telnet_command}"
        )
        raise
    if request_id:
        print(f"Story queued via interrupting_requests with request ID {request_id}.")
    else:
        print("Story queued via interrupting_requests.")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate a short story about an upcoming AzuraCast song and inject it immediately before that song."
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv("AZURACAST_BASE_URL", "https://192.168.1.226"),
        help="Base URL for the AzuraCast instance (default: %(default)s).",
    )
    parser.add_argument(
        "-s",
        "--station",
        default=os.getenv("AZURACAST_STATION", "neuralcast"),
        help="AzuraCast station shortcode (default: %(default)s).",
    )
    parser.add_argument(
        "--selection-count",
        type=int,
        default=3,
        help="Number of upcoming songs to consider when choosing via OpenAI.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Generate story files locally without uploading or queuing them.",
    )
    parser.add_argument(
        "--verify-tls",
        action="store_true",
        help="Verify TLS certificates when calling AzuraCast (disabled by default for local/self-signed certs).",
    )
    parser.add_argument(
        "--inject-lead-seconds",
        type=int,
        default=15,
        help="Seconds before song completion to trigger the story injection (default: %(default)s).",
    )
    parser.add_argument(
        "--inject-timeout",
        type=int,
        default=900,
        help="Maximum seconds to wait for the selected song to start playing (default: %(default)s).",
    )
    parser.add_argument(
        "--poll-interval",
        type=int,
        default=5,
        help="Seconds between successive now-playing polls (default: %(default)s).",
    )
    parser.add_argument(
        "--current-min-remaining",
        type=int,
        default=60,
        help="Include the current song in selection only if it has at least this many seconds remaining (default: %(default)s).",
    )
    return parser


if __name__ == "__main__":
    args = build_arg_parser().parse_args()
    run(args)
