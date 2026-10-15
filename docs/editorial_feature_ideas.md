# Editorial feature ideas

Status: release retention, host companion, and the manual episode pilot are implemented.
Captured from the product discussion on 2026-10-14. See
[release_editorial.md](release_editorial.md) for the retention implementation.

## Current direction

- Explore an editorial retention check for releases aging out of New Releases
  first. Decide whether a recording deserves a permanent catalog place before
  choosing its destination. The owner suggested an AI research pass as a possible
  implementation; subsequent discussion selected headless Codex and autonomous
  decisions with a maximum 20% discard budget.
- Revisit a listener companion for host transcripts, source links, and concert
  details afterward.
- Preserve authored radio episodes as the longer-term ambition below. This is an
  idea the owner has wanted to implement for several months.
- Leave out a recording-repair inbox: the existing agent-assisted replacement
  workflow already serves the owner well.
- Leave out favorites-based listening appointments: current audience size and
  the owner's existing workflow make this a low priority.

## Authored radio episodes

The manual pilot is implemented: see [episode_producer.md](episode_producer.md).
It uses proposal and final-airing approvals, automatically uploads rendered audio
for AzuraCast preview, and presents chapters through Host recap. Scheduling and
automatic recurring production remain future work. The broader design below
records that longer-term ambition.

### Intended experience

Create a deliberate musical journey in which track selection, order, researched
host commentary, and scheduling serve one editorial premise. Give each station
recognizable programs worth tuning in for.

Example: a 45-minute NeuralForge episode, "From folk melodies to fantasy metal,"
with selected recordings, an opening, two short connecting stories, and a closing
reflection. A NeuralCast counterpart could explore a particular Argentine scene
or the musical development of an artist, following its own editorial definitions.
Examples are illustrative, not approved track selections.

Eventually, Spanish and French NeuralForge channels could share an editorial
plan and music selections while retaining localized scripts and their own host
personality. Sharing a plan must not assume simultaneous playback across streams.

### Existing building blocks

- `pipelines/schedule_generator/` assigns playlists to scheduled blocks and
  generates presentation copy. It does not currently express an authored,
  ordered episode of individual songs and narrative beats.
- `pipelines/host_orchestrator/` generates researched scripts and TTS, inspects
  queues, uploads snippets, and inserts host segments.
- Scheduled block intros already prepare audio ahead of a boundary and recheck
  the queue before publication; see [host_block_intros.md](host_block_intros.md).
- Host memory already supports contextual callbacks; see
  [host_memory.md](host_memory.md). An episode adds planned progression across
  multiple songs, beyond opportunistic callbacks.
- Channel configuration separates brand, locale, and target station; see
  [host_channels.md](host_channels.md).
- The catalog and station editorial definitions supply recording identities and
  selection constraints. Existing live media should be reused.

### Proposed architecture

Introduce an episode-planning package under `src/neuralcast/pipelines/` with a
reviewable manifest: premise, intended audience, ordered track identities,
estimated durations, narrative beats, research sources, localized scripts,
prepared audio references, and approval/version state.

Keep responsibilities with the existing owners: catalog for playlist persistence,
scheduler for placement, host orchestration for speech generation and delivery.
Validate the feasible AzuraCast mechanism for ordered music playback before
committing to an execution design; host interrupt insertion alone is not proof
that full episode playback is supported reliably.

Track actual playback progress independently from preparation and queue insertion.
Current broadcast memory treats insertion as broadcast, so it cannot by itself
establish which episode beats listeners heard.

### Smallest useful version

1. One manually approved episode on one channel, using existing media.
2. A short, explicit track sequence and three prepared host interventions.
3. An operator-readable plan with sources, duration estimates, and audio preview.
4. A rehearsal covering queue changes, unavailable tracks, and duration drift.
5. One scheduled airing, with ordinary music as the fallback when the intended
   sequence cannot be maintained.

Only air commentary whose referenced track/context still matches playback. Avoid
promising a song until its delivery can be established. Keep unrelated rotation
and station catalogs intact.

### Value and tradeoffs

Evaluate whether the program feels coherent, distinctive, and worth hearing
again. Use owner/listener reviews, editorial preparation time, playback completion,
and available audience trends against comparable ordinary blocks. A small audience
may not support statistically meaningful retention comparisons.

Main costs are editorial preparation, researched factual accuracy, playback
coordination, and reduced spontaneity. Start with occasional special programs.
An earlier rough estimate was 8–12+ engineer-weeks for a fuller capability, subject
to an ordered-playback feasibility spike; this is not a delivery commitment.

### Decisions for a future design session

- First station, premise, duration, and intended frequency.
- How much selection and writing is automated versus explicitly reviewed.
- AzuraCast sequence control, interruption handling, and recovery behavior.
- Whether episodes recur, and how versions and repeat airings are tracked.
- Whether multilingual editions are independent airings or coordinated events.

## Release retention check: initial scope

The updater defaults to a 120-day window, configurable with `--days`.
Before this feature it migrated expired entries without a keep/retire decision.
It now reviews them before migration; media collisions can still leave entries
pending in New Releases.

Proposed decisions are retain (with a destination and rationale), retire from New
Releases without permanent promotion, or defer when evidence is insufficient.
Assess the particular recording against station taste and playlist definitions;
artist popularity, provider rank, and existing catalog volume are not sufficient.
Research can inform a decision but does not establish the owner's enjoyment.

The owner authorizes autonomous editorial decisions through headless Codex,
integrated into the existing weekly updater immediately before migration. Start
with a conservative 20% discard budget, selecting the weakest recording-level
fits and considering excessive representation from an album. The proposed
implementation uses a maximum, rounded down. Routine decisions do not require
manual review.

Use `gpt-6.1-sol` with `high` reasoning effort, explicitly pinned for this job:

```bash
codex exec --model gpt-6.1-sol -c 'model_reasoning_effort="high"' ...
```

Codex returns structured decisions; Python validates batch membership,
destinations, and the discard limit before applying them. Preserve explicit owner
decisions and record evidence so repeated updater runs do not endlessly reassess
the same release or discard another 20% of a previously reviewed batch. Failure
to research should defer the aging batch while allowing fresh discoveries to be
saved. Skip Codex when there are no aging candidates. The runtime integration is
implemented; live authentication and model access remain deployment checks.

Retirement semantics need explicit design: remove the New Releases CSV entry,
companion metadata, and its exact local media copy together to prevent sync
backfill. Preserve copies already present in permanent playlists. Do not use the
station-global `[DEL]` marker for this playlist-local operation.

## Host companion

Implemented in both NeuralCast and the sister `neuralcast-web` project. See
[host_companion.md](host_companion.md) for the summary-first UI, selected archetypes,
queue-success publication, 20-minute visibility window, and deployment notes.
