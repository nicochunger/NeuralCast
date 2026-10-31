# Automated manuscript workshop

Discuss and approve the concept, candidate recordings, host language, narrative
angle and timing with the owner. The workshop researches and writes a complete
manuscript, runs three independent reviews in parallel, then makes one final
content and delivery edit. It stops for owner manuscript approval before audio.
It never renders, uploads, publishes, schedules or changes station media.

## Station and language defaults

Use Argentinian Spanish (`es-AR`), natural voseo and restrained Rioplatense phrasing
unless the owner requests another language. The owner chooses NeuralForge or
NeuralCast during concept building. Ask if unspecified, resolve its current
Spanish channel, record that channel in the proposal and pass the station explicitly.
Currently these are `neuralforge-es` and `neuralcast-es`.

Welcome newcomers with "Bienvenidos" and introduce the configured brand's podcast,
theme and journey. Spoken NeuralForge mentions use "NeuralForsch" for TTS; titles,
summaries and metadata keep "NeuralForge". NeuralCast keeps its spelling. Do not
invent a presenter, episode number, date or schedule. Introduce bands on their own
merits, develop the bridges without restarting the show, and thank the listener
in the closing.

## Five jobs, three phases

| Job | Provider | Responsibility | Thinking/effort | Per-attempt deadline |
| --- | --- | --- | --- | --- |
| Research and draft | Codex, `gpt-6.1-sol` | Open sources, develop the whole story and attach evidence | high | 15 minutes |
| Factual support | Codex, `gpt-6.1-sol` | Independently verify claims, chronology and attribution | medium | 10 minutes |
| Narrative continuity | Gemini, `gemini-4-argon` | Assess development, depth, repetition and song purpose | low | 5 minutes |
| Conversational voice | Gemini, `gemini-4-argon` | Assess oral phrasing, personality and unforced asides | low | 5 minutes |
| Final edit | Gemini, `gemini-4-argon` | Reconcile findings and edit content, speech and performance | medium | 15 minutes |

The three reviews run in parallel after the draft is validated. Python starts the
jobs, retains each report and assigns stable finding IDs. The final editor sees
those findings directly and returns one resolution per ID: resolved, dismissed
with a reason, or unresolved. Unresolved findings and new consequential questions
prevent manuscript approval. Writer questions enter the same finding ledger.

Codex jobs use fresh, ephemeral, read-only sessions with web research enabled and
subagents disabled. Gemini jobs are direct structured API requests with no tools,
web search, file access or delegation. Review requests have an 8192-token output
limit; final edits have a 32768-token limit. Thinking consumes part of those
limits. SDK retries are disabled; the controller allows one targeted retry of a
failed job, while preserving accepted work. Authentication/configuration errors
and incomplete/token-limited responses stop for inspection instead of automatic
repetition. Manual retries resume accepted jobs. A normal successful workshop has
five model jobs; automatic retries can add at most one attempt per job.

All three independent reviews must complete before editing. A failed review does
not discard the completed reviews. Structural validation cannot establish natural
speech, historical truth or factual meaning; owner review remains required.

## Prompts and supplied context

- [Research and draft](../src/neuralcast/assets/stories/prompts/episode_editorial/write.md)
- [Factual support](../src/neuralcast/assets/stories/prompts/episode_editorial/factual_support.md)
- [Narrative continuity](../src/neuralcast/assets/stories/prompts/episode_editorial/narrative_continuity.md)
- [Conversational voice](../src/neuralcast/assets/stories/prompts/episode_editorial/conversational_voice.md)
- [Final edit](../src/neuralcast/assets/stories/prompts/episode_editorial/edit.md)

The single [podcast writing guide](../src/neuralcast/assets/stories/prompts/episode_editorial/writing_guide.md)
and three selected, labeled [voice examples](../src/neuralcast/assets/stories/episode_examples/library.json)
calibrate writing, narrative review, voice review and editing. Factual verification
receives the manuscript and evidence without style references. Every job receives
relevant approved scope, host identity/language, measured recording durations and
timing policy. The final editor also receives reviewer strengths to preserve and
verified additional sources. Operational source paths stay out of example context.

Write prepared storytelling with room for personality: occasional reactions,
asides, short detours and natural refinements, as if speaking to one interested
listener. Corrections must connect naturally, with "mejor dicho" where helpful.
There are no device or tag quotas. Preserve factual confidence, necessary
attribution and uncertainty; never invent experiences or dialogue. Avoid essay
framing, repeated lessons after anecdotes and uniform handoffs.

The final editor returns complete scripts by commentary ID. It can update sources
and companions when an evidenced content correction requires it. Source URLs must
already be in the supplied plan, initial research or verified review evidence.
Song order, identities, passage IDs, roles and word budgets remain fixed. Missing
evidence becomes an unresolved finding; the text-only editor cannot browse to
invent a repair. Companions must still describe the resulting narration accurately.

## Continuous passages and recording checks

Alternate opening → song → bridge → song → … → closing. Every music gap has one
complete host passage, companion and TTS generation. Connect paragraphs rather
than split anecdotes or analysis into adjacent commentaries. Host chapter titles
must be at most 40 characters. Scripts allow up to 30000 characters and a passage
budget up to 3000 words, within the approved episode narration budget.

The renderer adds 1.5 seconds of PCM silence before every host passage, inside
its chapter. Count this in total duration, outside the speech budget; do not
require a leading pause tag. Estimates include measured music, speech, tags and
lead-ins. Actual duration is measured after synthesis.

Resolve recording identity and edition questions when selecting media. Routine
provenance/listening checks belong in production notes for the owner to complete
before audio preparation. A suspected incompatible recording that changes the
story remains a consequential editorial question. Neither published credits nor
a file's tags prove its edition, and manuscript rewriting cannot perform a
listening check. Preserve each outstanding production note once.

## Input documents and approval

Use the project CLI and the standard working files:

```text
<station>/metadata/episodes/<NNN>-<episode-id>/
  proposal.json       # concept and embedded editorial_brief
  plan.json           # complete, authoritative renderer input
  manuscript.md       # derived song list and exact tagged narration
```

The proposal's `editorial_brief` contains `proposal_hash`, instructions,
`candidate_songs`, `initial_research`, `narration_words_per_minute` and
`duration_tolerance_seconds`. Candidate IDs and artist/title pairs must be unique
and resolve to exact library recordings. Selection/reordering follows the approved
instructions; no substitution or unapproved recording is allowed. The brief's
proposal hash references the normalized proposal without its brief or display
number. Concept approval binds both proposal and brief. Do not infer approval.

```bash
python -m neuralcast.cli.episode_producer init -s neuralforge --episode EPISODE_ID --proposal /tmp/proposal.json --episode-number NUMBER
python -m neuralcast.cli.episode_producer inspect -s neuralforge --episode EPISODE_ID
# After explicit owner approval of the concept and candidate songs:
python -m neuralcast.cli.episode_producer approve-proposal -s neuralforge --episode EPISODE_ID --hash CONCEPT_HASH
python -m neuralcast.cli.episode_producer editorial -s neuralforge --episode EPISODE_ID
# After explicit owner approval of the exact resulting manuscript:
python -m neuralcast.cli.episode_producer approve-manuscript -s neuralforge --episode EPISODE_ID --hash MANUSCRIPT_HASH
```

The installed/authenticated Codex CLI must have access to `gpt-6.1-sol`; direct
text jobs require the existing `GEMINI_API_KEY` with access to `gemini-4-argon`.
Music durations require ffprobe. The workshop does not need AzuraCast credentials.
Provider/model/effort settings, prompts, schemas, approved context and reference
examples contribute to the run hash. Changing them creates a new explicitly
requested run without modifying existing manuscripts, receipts or publications.

`--feedback '...'` gives owner direction for an explicitly requested workshop
revision. For style experiments on an unpublished complete manuscript, reuse its
evidence with the delivery command:

```bash
python -m neuralcast.cli.episode_producer delivery -s neuralforge --episode EPISODE_ID --feedback 'Keep the stories; make the narration more conversational.'
```

This uses the same Gemini final editor for one direct request, without research
or reviews. Only scripts may change; companions, sources and song/segment metadata
are locked. It requires the approved concept and no existing editorial blockers.
It snapshots the prior plan, manuscript and approval, writes an exact comparison,
and stops for renewed manuscript approval when text changes. Identical retries
reuse their original input and accepted output rather than polishing repeatedly.

## Receipts, limits and recovery

Each workshop keeps the following under
`runtime/episodes/<station>/<episode-id>/editorial/<run-hash>/`:

- `inputs.json` and previous plan, manuscript, editorial receipt and approval;
- accepted `write.json`, `factual_support.json`, `narrative_continuity.json`,
  `conversational_voice.json` and `edit.json`;
- `findings.json`, `final_plan.json`, `manuscript.md`, `edit_report.md`, `receipt.json`;
- per-job attempt directories with prompt, context, schema, returned response,
  safe failure diagnostics and `metrics.json`.

Delivery requests use the same layout under `delivery/<run-hash>/` with one edit
job. Receipts include each attempt's elapsed time, provider/model, and available
usage. Unknown usage is null, not zero. Gemini attempts also retain
`validation_schema.json`: array-size bounds are enforced locally, while the
provider schema omits them to keep its constrained-output grammar manageable.
Gemini records returned input/output and
thinking counts; Codex records reported turn-completion usage. Raw CLI logs are
temporary and discarded; authentication diagnostics can contain secrets. These
artifacts are not internal reasoning transcripts or complete browsing logs.

A job is cached only after schema and semantic validation. Missing/duplicate
passages or finding resolutions, unknown IDs, unsupported tags, word/duration
violations, sources outside supplied evidence and repeated-token corruption fail
validation. A failed job can receive its validation error and prior response in
one repair request. Successful jobs are not regenerated. Stored hash mismatches
stop execution. Installation checks concept approval and concurrent plan/Markdown
changes before replacing working documents. Rendering, uploads and publication
retain their independent approval and recovery boundaries.
