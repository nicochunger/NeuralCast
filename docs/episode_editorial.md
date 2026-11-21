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

## Five jobs, sequential editorial workshop

All editorial stages run via headless Antigravity CLI (`agy`) using Gemini 4 Neon:

| Job | Model / Effort | Responsibility | Web Search | Per-attempt deadline |
| --- | --- | --- | --- | --- |
| Research dossier (`research`) | `gemini-4-neon-high` | Independently verify primary sources, narrative arc and per-song cues (`song_notes`) | enabled | 10 minutes |
| Draft (`write`) | `gemini-4-neon-medium` | Write the complete manuscript strictly from the verified dossier | disabled | 10 minutes |
| Editorial review (`editorial_review`) | `gemini-4-neon-medium` | Assess narrative clarity, outsider orientation, song engagement and spoken voice | disabled | 7 minutes |
| Final edit (`edit`) | `gemini-4-neon-medium` | Resolve required findings, remove essay crutches, split oral beats and direct vocal tags | disabled | 15 minutes |
| Factual drift check (`fact_check`) | `gemini-4-neon-low` | Detect factual drift introduced during editing; auto-triggers one repair edit on major drift | disabled | 3 minutes |

Each stage executes in an isolated temporary directory containing `context.json`
(plus `writing_guide.md` and `voice_examples.md` for writing/review/edit stages)
with `--disable-slash-commands`, `--sandbox`, `--json-schema`, and
`--output-format json`. Deterministic `prose_diagnostics` (flagging essay
crutches, generic handoffs, and unbroken walls of text) are supplied to
`editorial_review` and `edit`. Major findings and research/editorial questions
require explicit resolutions; minor style suggestions can be applied directly.
Unresolved blockers or persistent major factual drift prevent manuscript
approval.

## Prompts and supplied context

- [Research dossier](../src/neuralcast/assets/stories/prompts/episode_editorial/research.md)
- [Draft](../src/neuralcast/assets/stories/prompts/episode_editorial/draft.md)
- [Editorial review](../src/neuralcast/assets/stories/prompts/episode_editorial/editorial_review.md)
- [Final edit](../src/neuralcast/assets/stories/prompts/episode_editorial/edit.md)
- [Factual drift check](../src/neuralcast/assets/stories/prompts/episode_editorial/fact_check.md)

The single [podcast writing guide](../src/neuralcast/assets/stories/prompts/episode_editorial/writing_guide.md)
and eight selected, labeled [voice examples](../src/neuralcast/assets/stories/episode_examples/library.json)
calibrate drafting, editorial review and final editing.

Write prepared storytelling with room for personality: occasional reactions,
asides, short detours and natural refinements, as if speaking to one interested
listener. Corrections must connect naturally, with "mejor dicho" where helpful.
There are no device or tag quotas. Preserve factual confidence, necessary
attribution and uncertainty; never invent experiences or dialogue. Avoid essay
framing, repeated lessons after anecdotes and uniform handoffs.

The final editor returns complete scripts by commentary ID. It can update sources
and companions when an evidenced content correction requires it. Source URLs must
already be in the supplied plan, initial research or verified review evidence.
Song order, identities, passage IDs, roles and word-budget estimates remain fixed.
Passage word counts, narration budgets and total duration are advisory starting
estimates. They do not reject drafts, edits, approvals or renders. Missing
evidence becomes an unresolved finding; the text-only editor cannot browse to
invent a repair. Companions must still describe the resulting narration accurately.

## Continuous passages and recording checks

Alternate opening → song → bridge → song → … → closing. Every music gap has one
complete host passage, companion and TTS generation. Connect paragraphs rather
than split anecdotes or analysis into adjacent commentaries. Host chapter titles
must be at most 40 characters. Scripts allow up to 30000 characters; each
passage carries an advisory word estimate.

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
`candidate_songs`, `initial_research`, `narration_words_per_minute` and an optional, non-enforcing `duration_tolerance_seconds` value. Candidate IDs and artist/title pairs must be unique
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
text jobs require the existing `GEMINI_API_KEY` with access to `gemini-4-neon`.
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
passages or finding resolutions, unknown IDs, unsupported tags, sources outside supplied evidence and repeated-token corruption fail
validation. A failed job can receive its validation error and prior response in
one repair request. Successful jobs are not regenerated. Stored hash mismatches
stop execution. Installation checks concept approval and concurrent plan/Markdown
changes before replacing working documents. Rendering, uploads and publication
retain their independent approval and recovery boundaries.
