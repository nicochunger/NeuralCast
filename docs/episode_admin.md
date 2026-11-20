# Episode creation and review

The authenticated web admin's **Episodes** tab is the owner workspace for each
station. Existing published episodes and local drafts are visible alongside new
projects. The authoritative documents remain `proposal.json`, `plan.json` and
`manuscript.md`; review notes, requests, snapshots and approvals live under
`runtime/episodes/`.

## Owner workflow

1. Choose NeuralForge or NeuralCast. **Suggest topics** proposes three narrative
   ideas using the station definitions, available catalogue and previous episodes.
   Enter your own topic or use a suggestion, then **Develop my idea**. Development
   researches a brief and resolves the selected existing recordings and duration.
2. Review the central question, outline, complete recording list, writing brief
   and research. Enter changes in **Feedback on this brief** below the brief and
   click **Apply feedback** to revise the same episode. Choose **Approve brief &
   queue** for weekly preparation, or **Approve brief & generate manuscript** now.
3. Read continuous host passages in listening order with full songs between them.
   Save overall and passage-specific comments. **Apply feedback** revises the
   current manuscript with saved evidence. Enable additional factual research
   when a request needs new sources; only that feedback receives focused Codex
   research before the Gemini edit. Revision notes and saved text comparisons
   explain the changes. New consequential questions remain visible.
4. **Approve manuscript & render** approves the exact displayed text and prepares
   an unassigned AzuraCast preview. Use the private chaptered player to listen.
   Add the current playback time to passage feedback when useful. Text changes
   return through manuscript review; previous audio is labelled as a comparison.
5. Review the publication description and click **Publish reviewed audio**. The
   submitted render hash is the public-release approval. Publication exposes that
   recording, guide and RSS item; it never queues the episode on live radio.

Published recordings and their working manuscripts are immutable. Developing a
new idea produces a separate project. The channel provides the current Spanish
locale and voice; episode TTS uses its dedicated storytelling instructions.

## Jobs, feedback and recovery

The existing admin job runner launches `python -m neuralcast.cli.episode_workspace
execute -s STATION --request-id ID`. Jobs continue after closing the browser and
retain stage output plus existing provider snapshots/metrics. An operation freezes
its inputs and owner approval hashes before launch. **Retry saved operation**
reuses its request and completed stages. A changed concept or manuscript requires
review before applying approval. Saved feedback uses optimistic revisions; stale
browser sessions cannot overwrite newer comments. Feedback edits never imply
manuscript or publication approval.

Every background action shows progress beside its controls and in a compact
sticky summary. The view includes elapsed time, observed stages, completion links
and failures with retry. Detailed logs expand inside the same progress card.
Stage state reads the frozen request, job output and saved editorial receipts;
the three focused manuscript reviews appear independently because they run in
parallel. Reloading the page reconnects to the current job without submitting
work. Episode workers stream output without buffering. Rendering reports voice
generation, audio assembly and private upload separately.

Private preview GET/HEAD routes require admin authentication and provide byte
ranges with `private, no-store`. Browser calls use the existing server-side token
proxy; credentials never enter the browser. Each preview is bound to a saved
production receipt and its audio hash. Published routes remain catalogue-only.

## Weekly preparation

Each station has an explicit weekly toggle and requested duration. The repository
cron template runs Sunday at 13:00 for NeuralForge and 13:15 for NeuralCast,
Europe/Zurich. It creates a normal detached admin job when enabled. New stations
start disabled until the owner enables their schedule in the panel.

A weekly job suggests topics and writes at most one approved queued brief. It
waits while a workspace episode needs manuscript or audio review. Only episodes
created or approved through the workspace enter that queue; other manual drafts
remain accessible. Weekly progress is saved per calendar week. Rendering,
feedback submission and publication are owner-triggered.

Install the schedule after deploying the API and web changes:

```bash
install -m 0644 deployment/cron/neuralcast-episode-workspace /etc/cron.d/neuralcast-episode-workspace
```

Restart `neuralcast-admin-api`, verify `/healthz`, and deploy `neuralcast-web`.
No new secrets or dependencies are needed.

## API

All routes require the existing admin bearer token:

- `GET /admin/episodes/{station}?episode_id=ID`: ideas, episode list, selected
  brief/manuscript, review notes, current audio and latest job.
- `POST /admin/episodes/{station}/actions`: frozen `ideas`, `develop`, `queue`,
  `write`, `revise`, `render` or `publish` operation, with the displayed revision.
- `POST /admin/episodes/{station}/retry`: resume an existing request ID.
- `PUT /admin/episodes/{station}/schedule`: explicit enablement and target duration.
- `PUT /admin/episodes/{station}/{episode}/feedback`: saved overall/section notes.
- `GET`/`HEAD /admin/episodes/{station}/{episode}/audio/{revision}`: private audio.
- `GET /admin/episodes/{station}/{episode}/history/{kind}/{run}`: saved manuscript.

The proposal and manuscript approval functions, renderer and publisher are the
same package functions used by the episode CLI. Topic planning and targeted owner
revision prompts live alongside the editorial prompts.

## Verification

Run the offline workflow and API tests with:

```bash
.venv/bin/python -m pytest tests/boundary/test_episode_workspace.py tests/unit/neuralcast/admin_api
```

In `neuralcast-web`, run `npm run build`, `npm run check:episode-admin` and,
with Playwright available, `npm run check:episode-admin-browser`. The browser
journey uses isolated authentication, synthetic episode data and generated test
audio; it makes no model calls or production mutations.

## Review workspace

The selected episode appears before creation controls. Its workflow indicator
shows brief, manuscript, audio and publication review. Creation and weekly
settings are collapsible; weekly duration is independent of a new idea's duration.
The approved brief, source links, recording auditions and production/listening
checks remain available during manuscript review.

Topic drafts, brief comments, manuscript feedback and publication descriptions
are saved on the current browser. The saved indicator identifies that scope.
Submitting feedback freezes it in the server request. Successful revision records
applied feedback and clears the pending review form. Rendering/publication reject
pending feedback; discarding comments is an explicit owner action.

Passage corrections use the authenticated review endpoint, validate the full
plan and timing, snapshot the previous plan, and update the Markdown manuscript.
Changed narration requires renewed render approval. Voice directions are saved
separately and affect episode narration without rewriting the manuscript. Voice
changes mark a previous recording as awaiting a new render.

Archiving preserves documents, receipts and recordings, hides the draft from the
normal list and excludes it from weekly preparation. Restore returns it to the
list. Published episodes cannot be archived through this interface.

Stop requests finish at the next observed stage boundary. Completed provider
outputs and narration caches remain available. An upload already in progress
finishes through the existing receipt handling. Public publication completes its
atomic operation. Retrying a stopped request clears its stop marker and reuses
accepted work.

Additional authenticated routes:

- `PUT /admin/episodes/{station}/{episode}/review`: passage correction, archive or
  restore, or episode voice directions, with the displayed episode revision.
- `GET /admin/episodes/{station}/{episode}/recording/{segment}`: audition an exact
  recording from the approved candidate list.
- `POST /admin/episodes/{station}/stop`: request a stage-boundary stop by request ID.

## Pipeline comparison

Open an unpublished episode and use **Approve brief & generate both comparison
versions** (or **Generate both comparison versions** for an approved brief).
This starts an isolated experiment from one frozen proposal, embedded brief,
candidate-recording inventory, writing guidance, host context and voice settings.
For a closer writing comparison, specify the selected songs and order in the
approved brief. Otherwise both writers independently follow its selection rules.

One arm uses Codex research/drafting and factual review with Gemini narrative,
voice and final editing. The other uses Gemini Neon throughout; only its research
and factual-review calls enable Google Search and URL Context. Python validates
and persists structured responses from both providers. Version A/B identities
are randomly assigned per experiment; reveal them after reviewing the prose.

Each version exposes the initial draft, final manuscript, attached evidence,
findings, editorial changes, unresolved questions, timing estimates and attempt
metrics. Provider usage is available after revealing identities; Codex subscription
usage is not converted to an invented API price. Sum of model attempt durations
includes parallel reviews and differs from wall-clock job duration. A single
paired run is exploratory rather than evidence for changing the default.

**Approve version … manuscript & generate audio** binds approval to that version's
exact manuscript hash. The other version remains unapproved. The existing speech
and mixing pipeline produces a local chaptered listening copy with the same
speech configuration and original recordings. It does not upload, publish or queue
comparison audio. Changed source recordings or speech configuration stop generation
rather than silently changing the experiment. Comparison audio uses authenticated,
byte-range-capable streaming through the existing admin proxy.

Experiments are stored under
`runtime/episodes/<station>/<episode>/comparisons/<request-id>/`. Each arm has
its own documents, editorial history, approvals, narration cache and production
receipts. Working episode documents and existing production receipts are preserved.
The default workflow and weekly preparation continue to use the hybrid pipeline.
A failed arm does not discard the other arm; retry resumes the saved experiment
and accepted jobs. Stop requests retain completed work at stage boundaries.
Published episodes cannot start comparison generation or approve new audio.

Additional actions are `compare` and `compare_render`; the latter requires
`comparison_id`, `variant` (`a` or `b`) and `comparison_manuscript_hash`.
Authenticated comparison views are available at
`GET /admin/episodes/{station}/{episode}/comparisons/{comparison}` and local audio
at `GET`/`HEAD /admin/episodes/{station}/{episode}/comparisons/{comparison}/{variant}/audio/{revision}`.

Episode target duration, narration budgets and passage word counts are advisory
starting estimates. Drafting, editing and rendering report actual/estimated length
without rejecting it for a timing difference. Gemini editorial stages use a
65536-token ceiling that includes thinking and output. Saved comparison inputs
retain their original prompts and provider settings; generate a new comparison
from the same brief to use updated settings. Existing attempts and receipts remain
available for inspection.

Comparison generation attempts one focused editorial resolution pass when the
final edit still reports blockers. It preserves both versions' distinctive writing
and original review history. Conflicting recollections can be resolved for the
manuscript by accurate attribution; unknown motives can be omitted. An uncertain
recording edition can be handled by removing edition-dependent narration while
keeping routine audition checks in production notes. It must not invent evidence
or clear a question that the remaining manuscript still depends on.

Existing blocked versions offer **Resolve research questions** in the comparison
panel. This submits `compare_resolve` with the comparison ID, version and displayed
manuscript hash. The saved revision includes before/after text and findings.
Resolution does not itself approve audio. The owner can approve generation without
reading the collapsed text; actual unresolved manuscript risks still block it.
