# Manual radio episode production

This is an agent-operated workshop, not a scheduled producer. Discuss a premise
with the agent, approve its proposal, let it select existing recordings and write
commentary, then review the finished audio in AzuraCast. Rendering automatically
uploads for preview. Only a second, explicit approval queues that exact render.
There is no episode-review web page and no scheduler, cron, or host-cadence change.

## Documents and workflow

Projects live in `<station>/metadata/episodes/<episode-id>/`. `proposal.json` and
`plan.json` are local editorial source documents, ignored by Git along with the
rest of each station's episode project directory.
Approvals, locks, cached narration, render receipts, and audio live under
Git-ignored `runtime/episodes/<station>/<episode-id>/`. Keep that runtime directory
on this VPS: it contains upload and queue receipts and must survive restarts.

1. The agent researches the proposed topic and writes a proposal like this:

```json
{
  "version": 1,
  "episode_id": "folk-to-fantasy",
  "channel": "neuralforge-es",
  "title": "From folk melodies to fantasy metal",
  "premise": "Explore how folk melody becomes fantasy storytelling.",
  "target_seconds": 2400,
  "commentary_budget_seconds": 240,
  "outline": ["Folk roots", "Heavier arrangements", "Fantasy and theatricality"]
}
```

Initialize the project and inspect the resulting proposal hash:

```bash
python -m neuralcast.cli.episode_producer init -s neuralforge --episode folk-to-fantasy --proposal /tmp/proposal.json
python -m neuralcast.cli.episode_producer inspect -s neuralforge --episode folk-to-fantasy
```

2. Once the owner approves that direction, the agent records the reviewed hash:

```bash
python -m neuralcast.cli.episode_producer approve-proposal -s neuralforge --episode folk-to-fantasy --hash PROPOSAL_SHA256
```

This command records an approval already given by the owner; an agent must not
infer approval from the fact that a proposal exists. Editing the proposal
invalidates its approval. The initial feature implementation does not authorize
any particular episode or live airing.

3. The agent reads the station playlist definitions and catalog, researches
supporting facts, and writes `plan.json` in the project directory. It contains
`version: 1`, `proposal_hash`, and ordered `segments`. The following are segment
shapes, not approved song selections:

```json
{
  "kind": "music",
  "id": "folk-roots",
  "playlist": "Folk Metal",
  "artist": "Canonical Artist",
  "title": "Canonical Song",
  "rationale": "Explain the recording's specific role in the episode."
}
```

```json
{
  "kind": "commentary",
  "id": "opening",
  "role": "opening",
  "archetype": "short_story",
  "brief": "Introduce the approved premise and orient the listener.",
  "max_words": 90,
  "sources": [
    {"url": "https://example.com/interview", "claim": "The specific supported fact to use."}
  ]
}
```

Begin with an `opening` commentary, end with a `closing`, and include music.
Other narration uses `role: bridge`. Allowed writing archetypes are `short_story`,
`album_spotlight`, `era_snapshot`, `deep_dive`, and `block_intro`. Choose these for
their delivery guidance, not live-host selection weights. Segment IDs must be
unique. Use playlist stems, not CSV filenames or paths. Each recording resolves
to one canonical catalog identity and existing MP3; no sync or download occurs.
Ambiguous filenames, mismatched ID3 identities, and missing recordings stop work.

Factual research is supplied by the editorial agent as source URLs and claims.
The commentary writer uses this evidence without another research pass. A
commentary may include an explicitly edited `script`, but then must also provide
`companion: {title, summary, key_points}`. This supports requested revisions.

4. Validate, produce, render, and automatically upload:

```bash
python -m neuralcast.cli.episode_producer validate -s neuralforge --episode folk-to-fantasy
python -m neuralcast.cli.episode_producer prepare -s neuralforge --episode folk-to-fantasy
```

Preparation uses the channel's current brand, script style, locale, voice, TTS
instructions and model configuration. The existing host system prompt and speech
validation are reused. One commentary-writing call produces speech and its
reading summary. TTS uses the existing synthesizer. Unchanged narration is cached
by its inputs; edited or dependent narration is regenerated.

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

5. After the owner listens and approves the final recording for broadcast:

```bash
python -m neuralcast.cli.episode_producer approve-and-queue -s neuralforge --episode folk-to-fantasy --hash RENDER_SHA256
```

This checks the approved proposal, current plan/input revision, and exact finished
file hash. It reuses the uploaded file and submits one `requests.push` command,
using existing host transport helpers. It does not skip the current track, adjust
schedules, consume host cadence, or add the recording to a rotation playlist.
Repeated approval of an already-queued revision returns its receipt.

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

Queue intent is also written before submission. A timeout, missing request ID, or
failed receipt write leaves an uncertain state. Do not repeat the submission or
clear its receipt without inspecting AzuraCast. A replay is an explicit future
feature, not an automatic retry. Preserve receipts across deployments.

## Listener guide

Only final airing approval publishes the guide registry under
`runtime/episodes/published/<channel>/<media-id>.json`. Preview uploads do not
publish listener guides. The player already fetches public now-playing and recent
history. Its normalized feed now retains song IDs and sends at most nine current/
recent observations to the existing companion endpoint; no new station polling
or separate playback-confirmation process is introduced.

Matching approved episode IDs and start timestamps produce the episode companion.
It includes the premise, selected recordings, artwork thumbnails from existing
embedded album art, selection rationales, commentary summaries, source links,
full narration transcript, and exact chapter boundaries. Missing art falls back
to the station image. Artwork is served through an authenticated backend endpoint
and a credential-free public web proxy. Local paths and source hashes are removed
from listener responses.

The existing Host recap popup displays progress and highlights the current song
or commentary using the same corrected server clock as normal track progress.
An active episode takes precedence over ordinary recaps. Recent history retains
it for 20 minutes after the episode ends unless a newer eligible host recap
replaces it. The open player also retains the last episode guide if it drops out
of the limited history feed. An already-open popup remains readable as before.
Stream buffering can create a small difference between chapter highlighting and
what a listener hears. This guide does not change live audio ownership or playback.

## Checks and deployment

```bash
python -m pytest
python -m pytest -m integration tests/integration/test_episode_render.py
```

The second command is an opt-in, local ffmpeg/ffprobe test using synthetic tones;
it uses no credentials, providers, station media, or uploads. Default tests mock
external boundaries and cover approvals, recording validation, narration caching,
render order/timing, upload recovery, queue uncertainty, and companion selection.
In `neuralcast-web`, run `npm run build` and `npm run check:host-companion`.

No new secrets or cron jobs are required. Existing generation/TTS credentials and
AzuraCast credentials are used only for explicit preparation/publication commands.
Episode uploads allow a longer timeout than short host snippets. Restart the admin
API for its extended companion/artwork endpoints and verify `/healthz`; deploy
the web app separately for episode presentation. Ordinary host behavior is unchanged.
