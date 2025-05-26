#!/usr/bin/env python3
"""
main.py — AI-assisted local-network radio pipeline
-------------------------------------------------
• reads prompt.txt  → GPT-4o  → track list
• yt-dlp + ffmpeg  → MP3s
• mutagen          → ID3 tags
• GPT-4o           → fun-fact script (every N songs)
• ElevenLabs (or OpenAI TTS) → DJ voice clips
• moves files into AzuraCast  → triggers CheckMediaTask
"""

import os, subprocess, pathlib, time
from typing import List
import openai, mutagen
from mutagen.easyid3 import EasyID3

# ─── CONFIG ──────────────────────────────────────────────────────────────
OPENAI_KEY = os.getenv("OPENAI_API_KEY")  # required
ELEVEN_KEY = os.getenv("ELEVEN_API_KEY")  # required if TTS=True
STATION_PATH = "/var/azuracast/stations/my_station/media"

NUM_TRACKS = 30  # how many songs per run
HOST_EVERY_N = 10  # create one DJ clip every N songs
GENRE_TAG = None  # e.g. "Indie Rock" or leave None
TTS = True  # turn off if you only want music
VOICE_NAME = "Adam"  # ElevenLabs voice

PROMPT_FILE = "prompt.txt"
# ──────────────────────────────────────────────────────────────────────────

# —— helpers ————————————————————————————————————————————————————————


def get_prompt() -> str:
    return pathlib.Path(PROMPT_FILE).read_text().strip()


def call_gpt(prompt: str) -> str:
    client = openai.OpenAI(api_key=OPENAI_KEY)
    response = client.chat.completions.create(
        model="gpt-4o", messages=[{"role": "user", "content": prompt}]
    )
    return response.choices[0].message.content


def gpt_playlist(prompt: str, n: int) -> List[str]:
    raw = call_gpt(prompt)
    lines = [l.strip() for l in raw.splitlines() if "-" in l]
    return lines[:n]


def youtube_to_mp3(query: str, outfile: str):
    cmd = [
        "yt-dlp",
        f"ytsearch1:{query}",
        "-x",
        "--audio-format",
        "mp3",
        "--audio-quality",
        "0",
        "-o",
        outfile,
        "--quiet",
    ]
    subprocess.run(cmd, check=True)
    print(f"Downloaded: {outfile}")


def tag_mp3(path: str, artist: str, title: str):
    audio = EasyID3(path)
    audio["artist"], audio["title"] = artist, title
    if GENRE_TAG:
        audio["genre"] = GENRE_TAG
    audio.save()


def make_fun_fact(artist: str, title: str) -> str:
    prompt = (
        f"In one short, upbeat radio-host sentence (≤25 words), "
        f"share a fun fact about the song '{title}' by {artist}."
    )
    return call_gpt(prompt).strip('"\n ')


def tts(text: str, outfile: str):
    import elevenlabs as el

    el.set_api_key(ELEVEN_KEY)
    el.generate(
        text=text, voice=VOICE_NAME, model="eleven_multilingual_v2", output_path=outfile
    )


def azuracast_rescan():
    subprocess.run(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "web",
            "php",
            "/var/azuracast/www/bin/azuracast",
            "azuracast:sync:task",
            "CheckMediaTask",
        ]
    )


# —— main pipeline ————————————————————————————————————————————————


def main():
    prompt = get_prompt()
    tracks = gpt_playlist(prompt, NUM_TRACKS)

    music_dir = pathlib.Path(STATION_PATH, "rotation_seq")
    host_dir = pathlib.Path(STATION_PATH, "host_seq")
    music_dir.mkdir(parents=True, exist_ok=True)
    host_dir.mkdir(exist_ok=True)

    host_counter = 0
    for idx, line in enumerate(tracks, start=1):
        artist, title = [s.strip() for s in line.split("-", 1)]
        idx_str = f"{idx:03d}"

        safe_artist = artist.replace("/", " ")
        safe_title = title.replace("/", " ")
        song_path = music_dir / f"{safe_artist} - {safe_title}.mp3"
        if not song_path.exists():
            youtube_to_mp3(line, str(song_path))
            tag_mp3(str(song_path), artist, title)

        # every HOST_EVERY_N songs, create a DJ clip about *this* song
        if TTS and idx % HOST_EVERY_N == 0:
            host_counter += 1
            host_path = host_dir / f"{host_counter:03d} - host_{idx_str}.mp3"
            if not host_path.exists():
                snippet = make_fun_fact(artist, title)
                tts(snippet, str(host_path))

    azuracast_rescan()


if __name__ == "__main__":
    main()
