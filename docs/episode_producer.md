# Podcast episode production

The main workflow has two editorial owner approvals. First, discuss the research,
concept, narrative direction, candidate songs, host language, and timing with the
owner. Approve that package, run four automated headless Codex stages (research
and write, review, content revision, speech/delivery), then return the final Markdown manuscript for the
owner's approval. Only then synthesize and render the approved scripts for private
preview. Publication requires separate explicit approval of the finished audio.

Default to Argentinian Spanish (`es-AR`) unless the owner requests otherwise.
The owner selects NeuralForge or NeuralCast during concept building; resolve its
Spanish host channel and record it in the proposal. Ask if the station has not
been specified, and pass it explicitly rather than relying on the CLI default.
See [station and language defaults](episode_editorial.md#station-and-language-defaults).

This is an on-demand podcast workflow. It does not queue recordings to live
radio, schedule production, or change live-host cadence. Published episodes are
available through station RSS feeds and cloud chapters. A separate review web
page remains outside this version.

## Editorial workshop and commands

Read [the editorial workflow](episode_editorial.md) for input schemas, prompts,
research/review responsibilities, recovery, and approval rules. The exact prompts
are [write](../src/neuralcast/assets/stories/prompts/episode_editorial/write.md),
[review](../src/neuralcast/assets/stories/prompts/episode_editorial/review.md), and
[revise](../src/neuralcast/assets/stories/prompts/episode_editorial/revise.md), and
[speech/delivery](../src/neuralcast/assets/stories/prompts/episode_editorial/delivery.md).
All receive the [shared writing guide](../src/neuralcast/assets/stories/prompts/episode_editorial/writing_guide.md)
and resolved host instructions.

Numbered projects live in `<station>/metadata/episodes/<NNN>-<episode-id>/`
(for example `003-laberintos-humanos/`).
`proposal.json` stores the positive, station-local `episode_number` alongside
`episode_id`. This display-only field is explicitly excluded from normalized
approval/render inputs, so assigning it preserves approvals and render caches.
The stable slug remains the CLI `--episode` argument, public URL ID, runtime
folder, and media path. Do not prefix those identities with the folder number.

Assign an existing project's number and rename its document folder with:

```bash
python -m neuralcast.cli.episode_producer number -s neuralforge --episode laberintos-humanos --episode-number 3
```

`init` also accepts `--episode-number`. Numbers must be unique within a station;
NeuralCast and NeuralForge have independent sequences. The numbering command
also updates an already published manifest's `episode_number` without changing
its audio, artwork, publication date, revision, or URL. New publications copy the
number from `proposal.json`; public list/detail responses expose it when present.
Back up the published catalogue before assigning episode numbers.

### Standard working files

A completed episode has exactly these three working documents:

| Filename | Purpose | When required |
| --- | --- | --- |
| `proposal.json` | Stable episode ID, number, channel, theme, duration, outline, and embedded `editorial_brief` | From initialization onward |
| `plan.json` | Ordered songs and complete tagged narration, with summaries and sources; the authoritative renderer input | Before manuscript approval or rendering |
| `manuscript.md` | Human-readable song order and the same exact narration as `plan.json` | Before owner manuscript review; retain the final produced edition |

The `editorial_brief` object contains the writing instructions, approved candidate
songs, initial research, speaking-rate estimate, and duration tolerance. It is
required for the automated editorial workshop. Complete manual manuscripts use
the manuscript approval gate. The controller hashes the base proposal and brief
separately; edits to the brief invalidate concept approval.

Before editorial runs, only `proposal.json` needs to exist. After editorial runs,
all three files exist. If narration changes, update both `plan.json` and
`manuscript.md`; editing the Markdown alone never changes the renderer input.
Do not create separate `episode.json`, `brief.json`, `proposal.md`, `script.md`,
or edition-suffixed draft files in the working folder. `init --brief PATH` imports
a supplied brief into `proposal.json`.
Keep previous editions, backups, approvals, production receipts, and editorial
stage history under `runtime/episodes/`, outside the working document folder.
Never delete historical runtime receipts or published catalogue/audio as cleanup.

These documents are local, Git-ignored editorial data. The agent prepares and
discusses the concept and embedded brief with the owner before recording
concept approval. Existing exact recordings must already be in the station
catalogue and media library; this workflow never runs sync or downloads songs.

```bash
python -m neuralcast.cli.episode_producer init -s neuralforge --episode EPISODE_ID --proposal /tmp/proposal.json --episode-number NUMBER
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
four prompts, contexts, stage results, source evidence, and prior plans under
`runtime/episodes/<station>/<episode-id>/editorial/<run-hash>/`. It ends at manuscript
review. Unresolved editorial issues are visible and prevent approval. Unchanged
reruns resume accepted stages; no unbounded review loop is launched.

For a small spoken-phrasing or vocal-tag revision after owner feedback, use the
independent delivery pass instead of rerunning research and content review:

```bash
python -m neuralcast.cli.episode_producer delivery -s neuralforge --episode EPISODE_ID --feedback 'Make the transitions more conversational; keep expressive cues restrained.'
```

It requires the approved concept/editorial brief and a complete current plan.
One search-disabled Codex call returns exactly one script per existing commentary.
The controller locks segment IDs/order, songs, sources, summaries and other plan
metadata, validates speech tags/word budgets/timing, and writes `plan.json` plus
`manuscript.md` together. Factual meaning and attribution within rewritten speech
still need owner judgment; structural constraints alone cannot prove them.

Before/after text, the previous plan/manuscript/approval, input/output hashes and
listening notes are retained under `runtime/episodes/<station>/<id>/delivery/<hash>/`.
The CLI returns the report path and new manuscript hash. Unchanged retries reuse
the result; edits, feedback or guidance changes create another run. A failure
preserves the current working documents and previous attempts. Existing editorial
blockers require content revision; newly flagged content problems block approval.
Published episodes cannot be rewritten. No audio or upload occurs; approve the
new manuscript before rendering.

`approve-manuscript` records the exact concept/plan hash after explicit owner
approval. Any change invalidates it. `prepare` requires finished scripts and that
approval before TTS. It uses the configured host voice, speech validation, and
synthesizer. Speech is cached by script and TTS settings. Manual plans require
complete scripts and companions before manuscript approval.

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
The standard manuscript structure alternates one continuous host passage and
one song. Each bridge develops the previous song's thought and carries the story
into the next recording, in one script and one TTS generation. Paragraphs within
that script do not create separate segments or chapters. Host chapter titles are
at most 40 characters. The renderer inserts 1.5 seconds of real silence before
every host passage, including opening and closing, without requiring a leading
pause tag. The silence belongs to that host chapter and counts toward total
duration, outside the narration budget. Editorial estimates include it.
Rendering consumes the ordered audio parts and applies the same lead-in rule to
every host passage. Generated and published recordings are immutable artifacts.
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

Retain operational receipts for audit. Do not clear or replay them.

## Episode guide

The permanent website publication snapshot contains song cards, copied artwork,
selection rationales, commentary summaries, sources, full transcripts, and exact
chapter boundaries. The on-demand player's position drives chapter highlighting
and chapter timestamps seek through the recording. An episode does not need to
have aired on radio to publish its guide.

The live Host recap API reads guide files under
`runtime/episodes/published/<channel>/<media-id>.json`. Podcast guides are served
from the publication catalogue. Preview uploads do not publish a guide or expose
audio publicly.

## Permanent website episodes and RSS

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
revision; no account is required.

### Podcast RSS and Pocket Casts

The same API serves two independent RSS 2.0 feeds, without a directory submission:

- NeuralCast: `https://neuralcast.duckdns.org/admin-http/public/podcasts/neuralcast/feed.xml`
- NeuralForge: `https://neuralcast.duckdns.org/admin-http/public/podcasts/neuralforge/feed.xml`

Paste the complete feed URL into Pocket Casts' search field and follow the show.
Feed retrieval alone does not register the show with Apple or Spotify. The feed,
covers, and audio are publicly accessible; an unlisted feed is not access control.

`publish` is still the visibility boundary. Feeds read the catalogue on request;
no export command, cron job, second audio upload, or rendering is needed. Previews
are excluded, as are missing/size-changed audio and episodes in another language.
The initial shows are Spanish (`es-AR`). If another language is added, give it a
separate feed rather than mixing languages into these shows.

The API additionally exposes `GET`/`HEAD` routes:

- `/public/podcasts/<station>/feed.xml` (`application/rss+xml`);
- `/public/podcasts/<station>/cover.png` (square titled station artwork);
- `/public/episodes/<station>/<episode-id>/chapters.json`
  (`application/json+chapters`).

Each RSS item references its chapters using the Podcasting 2.0 `podcast:chapters`
tag. The JSON contains the existing exact chapter start times in seconds,
concise titles, available song artwork, and links to the permanent Episode guide
on `https://neuralcast.fm/episodes/<station>/<episode-id>`. It never changes MP3
tags, published snapshots, production receipts, or guide titles. Pocket Casts'
mobile apps support this format; web/desktop support is more limited.

Show metadata and public URL defaults live in
`src/neuralcast/assets/podcasts/shows.json`. Both shows initially use an explicit
content advisory because recordings may include explicit lyrics. Adjust this
setting to match the content. Covers live beside that file. Short editorial
chapter titles for existing long headings live in `chapter_titles.json`, keyed
by station, episode ID, and chapter ID. Other titles reuse the published heading;
all are capped at 40 characters at a word boundary with an ellipsis. Artist names
are appended to song titles only when the result fits that limit. Keep future
chapter headings short during editorial planning. No generation occurs on feed
requests.

Optional deployment overrides:

- `NEURALCAST_PODCAST_PUBLIC_BASE_URL`: public API origin **including** `/admin-http`;
- `NEURALCAST_PODCAST_WEBSITE_BASE_URL`: website origin, without `/episodes`.

These are non-secret URL settings. Absolute URLs are derived from configuration,
never incoming Host/forwarded headers. Feed GUIDs depend only on station and
episode ID and remain stable across renumbering, metadata edits, and domain
changes. RSS and chapter responses support conditional ETags and HEAD requests
with a 60-second cache lifetime. Podcast apps may apply their own polling cache.
Keep audio and catalogue backups as described above. Restart the admin API after
installing the new routes and verify `/healthz`, both feeds, chapters, artwork,
and a ranged audio request through the public HTTPS proxy.

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
render order/timing, upload recovery, publication boundaries, podcast-only
operations, and read-only live guide access.
In `neuralcast-web`, run `npm run build` and `npm run check:host-companion`.

No new secrets or cron jobs are required. Existing generation/TTS credentials and
AzuraCast credentials are used only for explicit preparation/publication commands.
Episode uploads allow a longer timeout than short host snippets. Restart the admin
API for its extended companion/artwork endpoints and verify `/healthz`; deploy
the web app separately for episode presentation. Ordinary host behavior is unchanged.
