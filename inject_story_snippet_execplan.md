# Inject Story Snippets After Upcoming AzuraCast Songs

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work proceeds. Maintain it in accordance with `.agent/PLANS.md`.

## Purpose / Big Picture

NeuralCast currently schedules music via AzuraCast’s AutoDJ but offers no live context between tracks. After we finish this work, an operator can run `inject_story_snippet.py` to let the system pick an interesting upcoming song, ask OpenAI for a short spoken story, synthesize that narration, and insert it into the AutoDJ queue so the snippet airs immediately after the song. Success is demonstrated by seeing the story MP3 queued after the chosen track in AzuraCast’s upcoming list and hearing it on-air.

## Progress

- [ ] (pending) Confirm AzuraCast station slug and queue upload workflow via API probing.
- [x] (2025-01-10 15:32Z) Implemented initial `inject_story_snippet.py` with OpenAI story + TTS generation and AzuraCast integration stubs.
- [x] (2025-01-10 15:58Z) Dry-run succeeded after adding queue ID fallbacks; story files generated locally.
- [x] (2025-01-10 16:25Z) Switched to telnet-based interrupt queue injection; command waits for playback and reports request IDs.
- [ ] (pending) Run end-to-end verification (story creation, upload, queue check) against the live AzuraCast instance.

## Surprises & Discoveries

- Observation: The sandbox cannot reach the Raspberry Pi’s AzuraCast API, so live probing of endpoints is deferred to runtime on the target network.
  Evidence: HTTPS requests to 192.168.1.226 are not permitted from this environment, requiring defensive coding and runtime fallbacks.
- Observation: AzuraCast’s documented API exposes uploads via `POST /station/{station}/files` (base64 JSON) and does not allow queuing new items through `POST /station/{station}/queue`; only GET/DELETE are available. Need an alternative queuing strategy or confirmation of undocumented endpoints.
  Evidence: `openapi.yml` retrieved from the station lists no queue insertion route; direct POST returns HTTP 405 with “Method not allowed”.
- Observation: Telnet debug endpoint (`PUT /api/admin/debug/station/{id}/telnet`) accepts commands such as `interrupting_requests.push` to inject tracks into the Liquidsoap interrupt queue, which ensures playback immediately after whichever track is currently ending.
  Evidence: Invoking the telnet API with `command="help"` returned available commands, including `interrupting_requests.push`. Sending a push command responded with a numeric request ID.

## Decision Log

- Decision: Implemented AzuraCast queue insertion with multiple payload fallbacks because the precise API contract could not be probed from this environment.
  Rationale: Direct access to the Raspberry Pi instance is unavailable from the sandbox, so the script cycles through known payload variants and surfaces diagnostics if all fail.
  Date/Author: 2025-01-10 (assistant)
- Decision: Added queue ID fallback logic using song metadata when AzuraCast omits explicit queue identifiers.
  Rationale: `/api/station/<slug>/queue` responses on NeuralCast only return song attributes, causing the initial parser to drop entries and abort; fallbacks ensure upcoming tracks are usable.
  Date/Author: 2025-01-10 (assistant)
- Decision: Replaced REST queue insertion with a telnet-driven `interrupting_requests.push` flow that waits for the target song to play before injecting the story.
  Rationale: The public queue endpoint does not support POST, but the telnet API can push requests into Liquidsoap’s interrupt queue. Waiting until the song is playing guarantees the story airs immediately afterward.
  Date/Author: 2025-01-10 (assistant)

## Outcomes & Retrospective

To be filled in after validation.

## Context and Orientation

The repository root houses the playlist maintenance scripts (`main.py`, `playlist_utils.py`, `audio_utils.py`) and helper utilities. OpenAI helpers live in `openai_utils.py`, reusing `.env` credentials via `dotenv`. Station assets (playlists, songs, reports) are under `NeuralCast/`.

Stories resources already exist in `stories/story_prompt.md`, describing the storytelling style with `[TITLE]` and `[ARTIST]` placeholders, and `stories/tts_story_instructions.md`, defining voice guidance for TTS. The repository also contains `tts_injection.md`, which outlines the desired storytelling feature.

No existing module contacts AzuraCast. We will create a new top-level script `inject_story_snippet.py` to encapsulate the new functionality while reusing existing utilities (`openai_utils` and `playlist_utils.sanitize_filename_component`). AzuraCast resides at `https://192.168.1.226`, exposing its API at `/api/...`. The `.env` file already contains `AZURACAST_API_KEY`. OpenAI credentials are accessed via `OPENAI_API_KEY`.

## Plan of Work

Describe the exploratory steps first. Implementers should begin by introspecting the AzuraCast API to learn each station’s slug and numeric ID via `GET https://192.168.1.226/api/stations` (authenticated with `AZURACAST_API_KEY`). Fetch `GET https://192.168.1.226/api/station/{station}/queue` to inspect the upcoming list format and verify that only read operations are exposed. Because the host likely uses a self-signed certificate, plan on disabling TLS verification while issuing requests inside the script (set `verify=False` and suppress warnings, or surface a configuration flag).

Next, design `inject_story_snippet.py` with the following responsibilities:

1. **Configuration and AzuraCast session**: Read `AZURACAST_API_KEY` and optional `AZURACAST_BASE_URL` (defaulting to `https://192.168.1.226`). Instantiate a `requests.Session` with the API key sent as `X-API-Key` and TLS verification configurable (default to disabled for local dev). Add helper methods: `get_stations()`, `get_now_playing(station_slug)`, `get_upcoming_queue(station_slug)`, `upload_story_media(station_slug, file_path)`, and `send_telnet_command(station_id, command)` for Liquidsoap control. Record the media storage path returned by uploads so the Liquidsoap command can reference the on-disk file.

2. **Upcoming song selection**: Fetch the now playing payload (for context) and upcoming songs list. Extract a short list (e.g., the next three entries) and craft a compact prompt for `gpt-5-mini`, via `openai_utils.openai_text_completion`, asking it to pick the most story-worthy track and return a JSON object with the chosen queue ID or matching metadata. Parse its response to identify the target song and keep both the queue item ID (if present) and metadata (artist/title, internal AzuraCast `song_id`).

3. **Story generation**: Load `stories/story_prompt.md`, substitute `[TITLE]` and `[ARTIST]`, and call `openai_utils.openai_text_completion` (model `gpt-4o` or similar) to produce the narrative text. Save the raw text to `stories/Story_<Artist>_<Title>.txt`, sanitizing the name with `sanitize_filename_component`.

4. **TTS synthesis**: Load the TTS instructions from `stories/tts_story_instructions.md` and invoke `openai_utils.openai_speech` (with `voice="ash"`) to synthesize the MP3 file as `stories/Story_<Artist>_<Title>.mp3`.

5. **Media upload and story scheduling**: Store the generated TXT/MP3 under `stories/snippets/<station>/<YYYY-MM-DD>/` locally, and upload the MP3 to AzuraCast under `AI Stories/<station>/<YYYY-MM-DD>/`. Construct the Liquidsoap command `requests.push annotate:title="...",artist="...":<remote_path>` and poll `/api/nowplaying/{station}` until the selected song is on-air; queue the story as soon as that song starts (or immediately if it ends early) by issuing the command through `PUT /api/admin/debug/station/{id}/telnet`.
6. **Retention cleanup**: After a successful run, prune local snippets older than `keep_local_days` and delete uploaded snippets in AzuraCast older than `keep_remote_days` using `/api/station/{station}/files` and `/api/station/{station}/file/{id}`.

7. **Verification**: Log the telnet response (request ID) and confirm by checking now-playing updates or monitoring AzuraCast’s UI. Optionally expose a `--dry-run` flag that skips upload/telnet operations while still generating the text/MP3 locally for inspection.

While coding, add docstrings and minimal logging so operators can follow the flow. Guard execution behind `if __name__ == "__main__":` and accept CLI arguments such as `--base-url`, `--station`, or `--dry-run`.

## Concrete Steps

Originally, perform the following from the repository root:

1. Probe AzuraCast:
       python -m scripts.api_probe --base-url https://192.168.1.226 --api-key <key>
   Replace the placeholder with a small temporary script or Python REPL session to examine `/api/stations` and `/api/station/{slug}/queue`. Record the slug, media upload endpoint, and queue payload format inside this ExecPlan’s Decision Log once confirmed.

2. Implement the new script:
       (edit) inject_story_snippet.py
   Add helper functions, OpenAI integration, CLI parsing, and the main routine as described.

3. Optionally, if `openai_utils.openai_text_completion` needs to accept custom models or structured responses, extend it in `openai_utils.py`.

4. Run the script in dry-run mode to verify story generation:
       python inject_story_snippet.py --station neuralcast --dry-run

5. Run the full flow (requires network access to the Pi):
       python inject_story_snippet.py --station neuralcast
   Observe console logs for the telnet request ID and watch AzuraCast’s upcoming list; the story should appear in the interrupting queue once the target song approaches completion.

Update commands and notes here if tooling or arguments change during implementation.

## Validation and Acceptance

Acceptance requires successfully uploading and injecting one story. Run:

    python inject_story_snippet.py --station neuralcast

Expect output summarizing the chosen upcoming song, generated story file paths, uploaded media ID, and the telnet request ID returned after pushing to `interrupting_requests`. During playback, confirm that the story airs immediately after the selected song (e.g., by monitoring `/api/nowplaying/neuralcast` or listening to the stream).

## Idempotence and Recovery

Re-running the script may upload duplicate stories if the same track is selected. Because the prototype leaves MP3s in the library, note in the logs when an identical file already exists and either reuse or overwrite it. If the telnet push fails after upload, the media will remain in the library; operators can trigger it manually via the AzuraCast UI or rerun the script once the issue is resolved. Provide clear error messages and exit codes so reruns are safe after resolving issues.

## Artifacts and Notes

Capture key evidence once available, such as:

    Selected upcoming song: <Artist> - <Title> (queue ID XYZ)
    Story text stored at: stories/snippets/Story_<Artist>_<Title>.txt
    Story MP3 uploaded as media ID <UUID>; queued position immediately after <queue entry>.

Attach excerpts of the queue JSON response showing ordering to this section during implementation.

## Interfaces and Dependencies

New script `inject_story_snippet.py` must provide:

    def load_station_slug_and_queue(base_url: str, api_key: str, verify_tls: bool) -> dict:
        """Return station metadata including slug, IDs, and queue endpoints."""

    def choose_upcoming_song(client: AzuraCastClient) -> UpcomingTrack:
        """Use OpenAI (model gpt-5-mini) to select one upcoming song and return its metadata."""

    def generate_story_text(artist: str, title: str) -> str:
        """Produce a short story using stories/story_prompt.md and OpenAI."""

    def synthesize_story_audio(text: str, outfile: pathlib.Path) -> None:
        """Create TTS audio using stories/tts_story_instructions.md and the Ash voice."""

    def wait_for_track_and_inject(client: AzuraCastClient, station_slug: str, station_id: int, target_track: UpcomingTrack, telnet_command: str, *, lead_seconds: int, timeout_seconds: int, poll_interval: int) -> Optional[str]:
        """Wait for the selected song and push the story via the Liquidsoap interrupt queue so it airs immediately afterward."""

    def cleanup_local_stories(station_slug: str, keep_days: int) -> None:
        """Remove local snippet files older than the retention window and tidy empty folders."""

    def cleanup_remote_stories(client: AzuraCastClient, station_slug: str, keep_days: int) -> None:
        """Delete uploaded snippet files older than the retention window via the AzuraCast files API."""

Data classes like `UpcomingTrack` should clarify required fields (artist, title, song_id, queue_id). The client wrapper must expose methods for now-playing retrieval, upcoming queue inspection, media upload, file deletion, and issuing telnet commands. Depend on `requests`, `pathlib`, `dotenv`, and the local OpenAI utilities. Document any additional pip dependencies if new packages become necessary.
