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
   and research. Refine the brief using the direction field, **Approve brief &
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
