# Podcast episode production

The main workflow has two editorial owner approvals. First, discuss the research,
concept, narrative direction, candidate songs, host language, and timing with the
owner. Approve that package, run three automated headless Codex stages (research
and write, review, revise), then return the final Markdown manuscript for the
owner's approval. Only then synthesize and render the approved scripts for private
preview. Publication requires separate explicit approval of the finished audio.

Default to Argentinian Spanish (`es-AR`) unless the owner requests otherwise.
The owner selects NeuralForge or NeuralCast during concept building; resolve its
Spanish host channel and record it in the proposal. Ask if the station has not
been specified, and pass it explicitly rather than relying on the CLI default.
See [station and language defaults](episode_editorial.md#station-and-language-defaults).

This is an on-demand podcast workflow. It does not queue recordings to live
radio, schedule production, or change live-host cadence. RSS and a separate review
web page remain outside this version.

## Editorial workshop and commands

Read [the editorial workflow](episode_editorial.md) for input schemas, prompts,
research/review responsibilities, recovery, and approval rules. The exact prompts
are [write](../src/neuralcast/assets/stories/prompts/episode_editorial/write.md),
[review](../src/neuralcast/assets/stories/prompts/episode_editorial/review.md), and
[revise](../src/neuralcast/assets/stories/prompts/episode_editorial/revise.md).
All receive the [shared writing guide](../src/neuralcast/assets/stories/prompts/episode_editorial/writing_guide.md)
and resolved host instructions.

Projects live in `<station>/metadata/episodes/<episode-id>/`. `proposal.json`
defines the premise, channel, target duration, commentary budget, and outline.
`brief.json` binds that proposal to owner instructions, candidate songs, initial
research, and estimated timing policy. Both are local, Git-ignored editorial data.
The agent prepares and discusses these documents with the owner before recording
concept approval. Existing exact recordings must already be in the station
catalogue and media library; this workflow never runs sync or downloads songs.

```bash
python -m neuralcast.cli.episode_producer init -s neuralforge --episode EPISODE_ID --proposal /tmp/proposal.json --brief /tmp/brief.json
python -m neuralcast.cli.episode_producer inspect -s neuralforge --episode EPISODE_ID
# After owner approval of the concept and candidate songs:
python -m neuralcast.cli.episode_producer approve-proposal -s neuralforge --episode EPISODE_ID --hash CONCEPT_HASH
python -m neuralcast.cli.episode_producer editorial -s neuralforge --episode EPISODE_ID
# Read the generated manuscript.md with the owner. No audio exists from this step.
# After owner approval of that exact manuscript:
python -m neuralcast.cli.episode_producer approve-manuscript -s neuralforge --episode EPISODE_ID --hash MANUSCRIPT_HASH
python -m neuralcast.cli.episode_producer validate -s neuralforge --episode EPISODE_ID
python -m neuralcast.cli.episode_producer prepare -s neuralforge --episode EPISODE_ID
```

`approve-proposal` uses the combined `concept_hash` from inspect when a brief
exists. `editorial` writes a complete `plan.json` and `manuscript.md`, saving all
three prompts, contexts, stage results, source evidence, and prior plans under
`runtime/episodes/<station>/<episode-id>/editorial/<run-hash>/`. It ends at manuscript
review. Unresolved editorial issues are visible and prevent approval. Unchanged
reruns resume accepted stages; no unbounded review loop is launched.

`approve-manuscript` records the exact concept/plan hash after explicit owner
approval. Any change invalidates it. `prepare` requires finished scripts and that
approval before TTS; it no longer generates commentary through Gemini. It uses
the existing host voice, speech validation, and synthesizer. Unchanged speech is
cached by script and TTS settings. Existing complete manual plans remain usable
through the same manuscript approval; old incomplete plans need scripts first.

Approval is never inferred from a document's existence or an automated review.
Respect approvals already explicitly given in the session. Preserve rendered
revisions, receipts, historical queue records, and published catalogues; do not
rewrite old audio to adopt new editorial guidance.

## Writing guidance

The opening must welcome newcomers to the configured brand's podcast, introduce
the theme and why it is interesting, and briefly preview the journey in the host
language. Bridges develop the story without repeating the welcome; the closing
resolves the theme and thanks the listener. Do not invent a presenter, episode
number, release date, or schedule. These rules also apply to manual scripts.

Keep research references out of spoken narration, including manually supplied
scripts. Publication names, interview citations, books, and URLs belong in the
structured evidence and Episode guide. Tell the stories directly; distinguish
conflicting accounts through the participants' perspectives without reading a
bibliography aloud.

Before synthesis, review the complete spoken manuscript in sequence, including
the music breaks. Give the episode a central question and a causal narrative:
each bridge should pick up a person, decision, or unresolved consequence from
earlier, develop it with supported detail, and make the next recording matter
to that story. Develop important scenes with people, stakes, actions, and
consequences; avoid a sequence of isolated artist facts or anecdotes chosen only
because a band's song comes next. Establish relationships before relying on
them for an emotional ending. Allocate the narration budget to the strongest
stories, and reduce the song count when the owner wants more depth within the
same duration. Expression tags support the writing and cannot replace it.

## Audio preparation and private preview

Choose the speech model per render with `--tts-model` on `prepare` (and optionally
`validate` with the same flag):

```bash
python -m neuralcast.cli.episode_producer prepare -s neuralforge --episode EPISODE_ID --tts-model gemini-3.8-flash-lite-tts
```

The supported explicit choices are `gemini-3.8-flash-tts` and
`gemini-3.8-flash-lite-tts`. Without an override, the model remains
`GEMINI_TTS_MODEL`, defaulting to `gemini-3.8-flash-lite-tts`. An explicit model requires
the Gemini backend. This option leaves live-host configuration untouched and
does not change or reapprove the manuscript. The selected model contributes to
the render revision and speech cache keys, and is recorded with provider/voice
in the production receipt's `tts` field. Comparing models creates separate
editions while preserving previous renders; use the same flag on retries to
resume the intended edition. Validation does not persist the model choice.

Google documents Flash-Lite as a lower-latency, cost-efficient model with the same
request format and custom-voice support; Flash prioritizes fidelity, acting, and
regional dialects. See the [official model comparison](https://ai.google.dev/gemini-api/docs/models/gemini-3.8-flash-lite-tts).

ffmpeg decodes each segment to 44.1-kHz stereo PCM and applies a common -18 LUFS
loudness target with a -1.5-dB true-peak target. Complete songs and narration are
concatenated without overlaps, then encoded once to 192-kbps MP3. Chapter offsets
come from PCM sample counts and are checked against the final encoded duration.
Narration must fit the approved seconds budget; total duration must fall between
75% and 115% of the proposed target. Failure stops before upload. Scratch PCM is
removed even on failure. Existing music files and playlist rows are never edited.

Episodes larger than 8 MiB use the same chunked multipart upload protocol as
AzuraCast Media Manager, in 512-KiB chunks, rather than a single base64 JSON body.
This avoids request-size limits without changing server configuration. Folder/
playlist browsing context is omitted so the upload does not assign rotation
playlists. Smaller files keep the existing host uploader.

The result is uploaded as an **unassigned media file**, outside the regular
`AI Stories` cleanup prefix:

```text
Episodes/<episode-id>/<revision>/episode.mp3
```

Find it in the target channel station's AzuraCast Media Manager and preview it
there. No playlist membership or playback request is created by preparation.
AzuraCast documents that unassigned media is not played automatically:
[Media Management](https://www.azuracast.com/docs/user-guide/media-management/).

The CLI returns the render SHA256 and uploaded media identifiers. The media title
includes a human-readable edition number so revised mixes have distinct song
identities. Re-running prepare reuses an unchanged render and confirmed upload.
Changing inputs creates a new revision and preserves earlier previews. If a
finished local render is altered or missing, preparation stops rather than
rebuilding it and resetting its upload/queue receipts.

## Failure and recovery

Each project has a nonblocking process lock. JSON writes use fsync and atomic
rename. Source recordings are hashed before production and rechecked before the
render is uploaded; a concurrent library edit stops that upload.

Upload attempts are recorded before sending. If acknowledgement is missing,
prepare attempts to resolve the exact recorded remote path through the existing
media-list API. It does not send a second upload blindly. If that path cannot be
resolved uniquely, inspect Media Manager first. A definitive HTTP 413 rejection is recorded as rejected and can be retried.
For older receipts, after confirming a rejection and that no upload exists, an
operator can mark `upload_attempt.status` as `rejected` under the project lock,
recording the evidence. A retry archives that attempt in `upload_history`. Never
mark an uncertain attempt rejected while its original process or server-side
processing might still be running. Unknown outcomes continue to require inspection.

Historical queue receipts from the radio pilot are retained for audit only.
Do not clear or replay them. Removing the queue feature does not alter existing
published podcasts, recordings, or already-submitted live station requests.

## Episode guide

The permanent website publication snapshot contains song cards, copied artwork,
selection rationales, commentary summaries, sources, full transcripts, and exact
chapter boundaries. The on-demand player's position drives chapter highlighting
and chapter timestamps seek through the recording. An episode does not need to
have aired on radio to publish its guide.

The original radio pilot's guide files under
`runtime/episodes/published/<channel>/<media-id>.json` remain readable by the live
Host recap API for compatibility. No new broadcast guides are written by the
podcast producer. Preview uploads do not publish a guide or expose audio publicly.

## Permanent website episodes (no RSS)

Website publication follows preview upload and review. After
explicit approval to make a finished recording publicly available on demand:

```bash
python -m neuralcast.cli.episode_producer publish -s neuralforge --episode EPISODE_ID --hash REVIEWED_RENDER_SHA256 --description 'Listener-facing episode description'
```

This operation does not regenerate audio, upload, queue, or assign playlists. It
checks the reviewed local render and the existing AzuraCast recording against the
approved hash. It atomically publishes a snapshot under
`runtime/episodes/catalogue/<station>/<episode-id>/manifest.json`, and copies the
small chapter artwork thumbnails into that directory. Episode summaries,
transcripts, sources, and chapter timings remain available independently of
production caches and the temporary live recap window. Publication does not
require the episode to have aired. Repeating the command for the same revision
returns its existing publication; a different revision cannot overwrite that URL.

Keep the published AzuraCast `episodes/<episode-id>/<revision>/episode.mp3`
recording permanently. The catalogue references that authoritative file, rather
than creating a second media mirror. Back up the catalogue directory and those
AzuraCast audio files together; neither is backed up by Git. Do not delete
published recordings during preview or production-cache cleanup.

The existing API service exposes anonymous, read-only routes:

- `GET /public/episodes`, optionally filtered with `?station=neuralforge`;
- `GET /public/episodes/<station>/<episode-id>`;
- `GET` / `HEAD /public/episodes/<station>/<episode-id>/audio`;
- `GET /public/episodes/<station>/<episode-id>/art/<chapter-id>`.

Only the explicit catalogue allows access. Preview media, source recordings,
filesystem paths, credentials, and administrative operations stay private. Audio
supports byte ranges for browser seeking, including 206 and 416 responses.
Missing or size-changed recordings fail closed. The existing VPS `/admin-http/`
HTTPS proxy forwards these routes; no AzuraCast configuration change is needed.

The Next.js web server reads metadata using its existing `HOST_ADMIN_BASE_URL`.
Published media URLs are derived from that server-side setting; the browser reads
audio and artwork directly from the VPS, without a Vercel audio proxy or token.
The base must be a browser-reachable HTTPS URL in production, such as the existing
`https://neuralcast.duckdns.org/admin-http` configuration.

The website has `/episodes` with a central selector for the independent station
podcasts, numbered episode rows, and permanent
`/episodes/<station>/<episode-id>` pages. Desktop shows an inline **Episode guide**;
mobile opens it in a native modal dialog. Chapter highlighting follows actual
playback position and chapter timestamps seek through the recording. The
episode player lives at the root layout alongside the unchanged live-radio
element. Navigation preserves playback, and explicitly starting live radio or
an episode pauses the other. Resume position is stored locally per published
revision; no account is required. RSS is intentionally outside this version.

Run `npm run check:episode-playback` in `neuralcast-web` after building. This
optional browser check requires Playwright (or `EPISODE_PLAYWRIGHT_MODULE` pointing
to an installed Playwright module) and its Chromium browser. It uses generated
tones and a local fixture only, without publishing real recordings.

## Checks and deployment

```bash
python -m pytest
python -m pytest -m integration tests/integration/test_episode_render.py
```

The second command is an opt-in, local ffmpeg/ffprobe test using synthetic tones;
it uses no credentials, providers, station media, or uploads. Default tests mock
external boundaries and cover approvals, recording validation, narration caching,
render order/timing, upload recovery, publication boundaries, rejection of the
removed queue command, and read-only compatibility with legacy broadcast guides.
In `neuralcast-web`, run `npm run build` and `npm run check:host-companion`.

No new secrets or cron jobs are required. Existing generation/TTS credentials and
AzuraCast credentials are used only for explicit preparation/publication commands.
Episode uploads allow a longer timeout than short host snippets. Restart the admin
API for its extended companion/artwork endpoints and verify `/healthz`; deploy
the web app separately for episode presentation. Ordinary host behavior is unchanged.
