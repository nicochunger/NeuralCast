# Private music discovery

Each station gets five downloaded suggestions to review in the web admin's
**Discovery** tab. The owner listens, chooses **Yes** or **No** for each song,
and can explain either answer. **Save feedback** preserves an unfinished review.
**Process feedback** saves and freezes all five decisions, runs a separate agent
to interpret them, and adds only the accepted recordings. It never starts another
discovery search, downloads more songs, runs station-wide sync, uploads host audio,
or changes scheduling. Generate suggestions is a separate action.

## Agent stages and owner authority

Both calls use the same shared runner as episode editorial work:
`services/editorial_agent.py`, with `codex exec --ignore-user-config --ephemeral`,
`gpt-6.1-sol`, medium reasoning for both discovery and feedback, a read-only temporary workspace, strict JSON output,
a 30-minute timeout, and a filtered environment without station/provider secrets.
Prompts, context, schemas, responses, and accepted results persist for inspection;
raw CLI authentication diagnostics do not.
Failures retain a safe category/message in `failure.json` and include that message
in the admin job log, without retaining raw provider or authentication output.

Discovery uses live web research. It receives station playlist definitions, the
catalog, prior suggestions/feedback, and preference history. Five different artists
are required, at least three absent from permanent playlists. Existing catalog
identities and previous suggestions are excluded. Each recording must have an
existing permanent playlist home, research links, and an exact HTTPS YouTube video
URL. Python validates identities, destinations, and URLs before downloading through
the existing yt-dlp helper. It never falls back to a search for an audition URL.
Audio is checked, tagged, and normalized using existing music helpers. These checks
do not prove that a video is the intended performance; owner listening is the final
recording-quality and taste check.

Each suggestion keeps its song-specific recommendation and adds a short, sourced
artist introduction: musical style, origin, and formation/start year when documented.
Unknown biographical details are omitted. An optional recording note is reserved
for a concrete concern such as an uncertain live/alternate version. Research-access
limitations stay in the diagnostic `uncertainty` field and agent receipts, rather
than appearing as listening guidance. Older batches without the new text fields
remain readable.

Feedback processing has web search disabled. It receives only the frozen batch,
its decisions, definitions, catalog, and preferences. Its placements must match
exactly the accepted candidate IDs; rejected, extra, missing, and repeated IDs are
refused. It cannot change a yes/no decision. Broader artist, playlist, or station
preferences require written owner reasoning; bare yes/no feedback can establish
only recording-level preferences. Preference updates remain incremental, cite
their supporting candidates, and are shown after processing. Original decisions
and reasoning are retained for subsequent discovery.

## Media and catalog behavior

Auditions live under `runtime/music_discovery/<station>/batches/<batch-id>/audio/`,
outside every station's live media tree. They are served only by authenticated
admin routes and the session-authenticated web proxy, with private/no-store cache
headers and GET/HEAD byte-range support. Credentials remain server-side. Auditions
are never exposed through the public episode routes or a public media URL.

The shared tagging helper already embeds album artwork and applies ReplayGain.
Discovery saves supported embedded raster artwork under the batch's `artwork/`
directory and serves it through the same authenticated admin boundary. The review
uses one row per song, with the cover, recommendation, artist introduction, player,
and feedback controls; it stacks on small screens. Missing covers show a neutral
placeholder. Cover copies remain available after audition MP3 cleanup, alongside
the batch history, without modifying the audition bytes or their approval hashes.

Accepted MP3 bytes are checked against the audition SHA256 and copied exclusively
into the chosen existing `songs/<playlist>/` directory. An existing different file
or a symlink conflict stops processing; it is never overwritten. Only those approved
files are installed. Catalog writes use `StationPlaylistCatalog`, preserve extra
columns/header order, avoid duplicate artist/title rows, and set new rows to
`Validated=False`. The next ordinary sync owns normal resolver validation and tag
reconciliation. A pending deletion marker in a destination stops promotion.

After accepted additions and preference persistence, all temporary audition MP3s
are removed, including accepted copies now installed in the authoritative media
tree. Rejected recordings are removed only from the private audition directory.
No `[DEL]` markers or existing-station deletions are generated. Batch documents,
feedback, hashes, and agent receipts are preserved. Back up this runtime directory
to retain learned preferences and review history; it is intentionally Git-ignored.

## Scheduling, retries, and concurrency

The optional cron template runs Sunday at 11:00 for NeuralForge and 12:00 for
NeuralCast, Europe/Zurich time. Both scheduled and manual generation skip/refuse
while a batch awaits review or feedback processing. Scheduled calls also skip
after a batch has been generated in the same station-local ISO week. The manual
**Generate suggestions** button can start another batch after processing finishes.
Stations have independent state and locks. Feedback processing is never scheduled.

A failed preparation keeps its same batch and completed recordings; **Retry
preparation** resumes it. Submitted feedback stays frozen if the agent or application
fails. **Retry processing** resumes the saved plan and completed song operations.
Submitting the same revision or processing a completed batch is idempotent. Optimistic
feedback revisions prevent stale browser sessions overwriting newer decisions.

Promotion holds the same `runtime/catalog-maintenance.lock` used by scheduled catalog
maintenance (or `NC_MAINTENANCE_LOCK_FILE`). Conflicts fail for retry. Do not run direct
sync commands or edit destination CSVs concurrently with promotion: those operations
do not all share this lock. Python applies accepted files and rows one at a time,
recording progress; this is resumable, not an all-or-nothing batch transaction.

## API and CLI

All routes require the existing bearer token:

- `GET /admin/discovery/{station}`: current batch, preferences, latest admin job.
- `POST /admin/discovery/{station}/generate`: detached discovery job.
- `PUT /admin/discovery/{station}/feedback`: `{batch_id, revision, feedback}`;
  feedback maps candidate IDs to `{accepted: boolean, reasoning: string}`.
- `POST /admin/discovery/{station}/process`: `{batch_id, revision}`; freezes the
  completed review and launches processing, or retries an already-frozen review.
- `GET`/`HEAD /admin/discovery/{station}/batches/{batch_id}/audio/{candidate_id}`:
  exact private audition recording. No filesystem path is accepted.
- `GET`/`HEAD /admin/discovery/{station}/batches/{batch_id}/artwork/{candidate_id}`:
  private embedded album cover, when available; no external image fetch is needed.

Background jobs use existing `/admin/jobs/{job_id}` status/log endpoints. CLI use:

```bash
python -m neuralcast.cli.music_discovery generate -s neuralforge --scheduled
python -m neuralcast.cli.music_discovery generate -s neuralcast
python -m neuralcast.cli.music_discovery process -s neuralforge --batch-id BATCH_ID
```

The process command requires previously submitted feedback; it cannot manufacture
approval. There is no discovery dry-run: generation downloads private auditions.

## Installation and validation

The runtime needs the same authenticated Codex CLI/model access as episode editorial
work, plus existing yt-dlp, ffmpeg, mp3gain, and metadata-provider access. The existing
YouTube cookie settings apply. No new secrets or Python dependencies are introduced.
Set `NC_CODEX_EXECUTABLE` to an explicit CLI path when multiple installations exist.
This VPS uses `/usr/bin/codex` (0.163.0); the older `/usr/local/bin/codex` (0.146.0)
rejects the pinned model under the service account. Shell and systemd PATH order
can select different installations. Keep the pinned model and verify the configured
executable from the service environment before enabling jobs.

Deploy the corresponding `neuralcast-web` changes and restart `neuralcast-admin-api`,
then verify `/healthz`. To enable weekly discovery:

```bash
sudo install -m 0644 deployment/cron/neuralcast-music-discovery /etc/cron.d/neuralcast-music-discovery
```

Offline workflow checks:

```bash
python -m pytest tests/boundary/test_music_discovery.py tests/unit/neuralcast/pipelines/episode_producer/test_codex.py tests/unit/neuralcast/admin_api
```

Tests mock agents and downloads, exercise actual temporary catalog/media files and
the authenticated API, and cover exact owner membership, retries, conflicts, input
validation, frozen revisions, preferences, and private seekable audio. Run the full
offline suite and the web production build/audio-persistence check before deployment.
These checks do not establish live Codex authentication, research quality, or YouTube
availability; an explicit first generation exercises those boundaries.
