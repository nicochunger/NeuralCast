# **Project Specification: AI-Generated Local Radio Stream (Private Use)**

---

## **Objective**

Create a self-hosted internet radio station that runs 24/7 on a **local network**, streams to any radio player or browser via a local IP address, and dynamically populates its playlist using **AI-generated suggestions** and **YouTube-based MP3 downloads**.

---

## **System Overview**

### Components

1. **AzuraCast** – Self-hosted radio server (AutoDJ + streaming engine)
2. **ChatGPT API** – To generate tracklists based on prompts
3. **yt-dlp + ffmpeg** – To download MP3s from YouTube using artist + track name
4. **Tagging tool** – To embed `artist`, `title`, and optional `genre` metadata
5. **Python orchestration script** – To connect all components
6. **Optional**: Crontab or systemd timer to run updates daily/weekly

---

## **Workflow Diagram**

```
[User prompt] ──> [ChatGPT API] ──> [List of tracks]
                                     │
                                     ▼
                      [yt-dlp + ffmpeg] downloads MP3s
                                     │
                                     ▼
             [Tagging tool adds artist/title/genre metadata]
                                     │
                                     ▼
                  [MP3s copied to AzuraCast's AutoDJ folder]
                                     │
                                     ▼
               [AzuraCast streams playlist to LAN radio client]
```

---

## **Details and Configuration**

### 1. **AzuraCast Setup**

* **Host**: Local PC, server, or Raspberry Pi

* **Access URL**: `http://192.168.X.Y:8000/stream`

* **Install**:

  ```bash
  curl -fsSL https://install.azuracast.com | sh
  ```

* **Playlist Mode**: Use AutoDJ with scheduled or rotated playlists

* **Mount Point**: Use default (`/radio.mp3`) or define a custom one

---

### 2. **Tracklist Generation via ChatGPT**

* Use the ChatGPT API with a prompt like:
  `"Give me a playlist of 20 indie rock songs with artist and title only, in plain text"`

* The response will look like:

  ```
  Arctic Monkeys - Do I Wanna Know?
  The Strokes - Last Nite
  ...
  ```

* Save as `tracks.txt`

### 3. **YouTube MP3 Downloader**

* Use `yt-dlp` to:

  * Search top YouTube result via `ytsearch1:`
  * Download audio
  * Convert to MP3
  * Save as `Artist - Title.mp3`
  * Add ID3 tags (`--add-metadata`)

* Python script:

  * Reads `tracks.txt`
  * Cleans file names
  * Checks if file already downloaded
  * Tags MP3s using `eyeD3` or `mutagen`

* Optional genre tag: hardcoded by user prompt (e.g., `genre = "Indie Rock"`)

### 4. **File Management**

* MP3s are moved to:

  ```
  /var/azuracast/stations/[station-name]/media/
  ```

  or uploaded via AzuraCast’s web UI

* Optionally organize by folder:

  ```
  /media/IndieRock/
  /media/Synthwave/
  ```

* Playlist config in AzuraCast points to the correct folder(s)

---

## **Python Script Functionalities**

Script: `generate_playlist_and_download.py`

Features:

* Call ChatGPT API for playlist
* Save response to `tracks.txt`
* For each line:

  * Search YouTube via `yt-dlp`
  * Download MP3
  * Add ID3 tags: `artist`, `title`, `genre`
* Copy to AutoDJ folder

Optionally run:

```bash
python generate_playlist_and_download.py "Give me 15 vaporwave tracks"
```

---

## **Security & Access**

* Stream URL: e.g., `http://192.168.1.100:8000/radio.mp3`
* Access limited to local network
* No public exposure, so no license required

---

## **Optional Extensions**

* **Voice interludes**: Use TTS (e.g., ElevenLabs) to insert “Now playing…” snippets
* **Dynamic prompts**: Rotate genres per day/hour
* **Web interface**: Allow UI to generate new playlists
* **Smart radio app integration**: Set favorite station to the local IP

---

## **Basic Requirements**

| Tool             | Purpose                     |
| ---------------- | --------------------------- |
| AzuraCast        | Stream and manage the radio |
| ffmpeg           | Convert audio               |
| yt-dlp           | Download from YouTube       |
| Python 3         | Run orchestration           |
| ChatGPT API key  | Generate playlists          |
| eyeD3 or mutagen | Tag MP3s                    |

---

## **Deliverables (to be built)**

* `generate_playlist_and_download.py` – Core script
* `tracks.txt` – Output from ChatGPT
* `config.json` – Optional: store API key, genre, station path
* `README.md` – Setup and usage instructions

---

Let me know and I’ll start building the Python script, including the ChatGPT integration and MP3 tagging. Want that next?
