# Automated manuscript workshop

Episode production starts with a conversation: research the idea with the owner,
agree on its angle, candidate recordings, host language, duration, and narrative
direction. The owner approves that concept package. Three sequential headless
Codex calls then research/write, review, and revise the complete manuscript.
The result stops at a Markdown manuscript for a second owner approval. This
command never synthesizes, renders, uploads, publishes, or changes station media.

## Station and language defaults

The usual episode language is **Argentinian Spanish (`es-AR`)**, including natural
voseo and restrained Rioplatense phrasing. Apply this default during concept
building unless the owner requests another language; it need not be asked again
for every episode. Preserve artist, song, and album names as supplied.

The owner chooses **NeuralForge or NeuralCast** in the concept conversation.
Use that station's catalog, brand, host style, and media destination. If the
station is missing, ask during concept building rather than relying on the CLI's
NeuralForge default or the station used for a previous episode.

Resolve the station's Spanish channel from the current channel registry. The
current mapping is `neuralforge` → `neuralforge-es` and `neuralcast` →
`neuralcast-es`; both use `es-AR`. Set the proposal's `channel` accordingly and
pass the matching `--station` explicitly to commands. The approved proposal then
carries this choice through every editorial call and synthesis. Language and
station selection belong in the concept instructions and channel configuration;
the reusable stage prompts continue to follow the supplied host locale and brand.

## Main prompts

These files are the exact main prompts sent to the three fresh Codex sessions:

1. [Research and write](../src/neuralcast/assets/stories/prompts/episode_editorial/write.md)
2. [Review](../src/neuralcast/assets/stories/prompts/episode_editorial/review.md)
3. [Revise](../src/neuralcast/assets/stories/prompts/episode_editorial/revise.md)

All three also receive the full [writing guide](../src/neuralcast/assets/stories/prompts/episode_editorial/writing_guide.md),
[passage guidelines](../src/neuralcast/assets/stories/prompts/episode_commentary.md),
resolved host system/TTS instructions, the station playlist descriptions, approved
brief, candidate durations, and allowed vocal tags. The references are included
as text in the snapshot; agents do not have to discover repository files. The
stage prompt/schema determines the task/output, while references supply style.

Stage 1 performs additional online research, opens supporting sources, develops
the story, and supplies every script, companion summary, and claim/source map.
Stage 2 reads the whole manuscript with music breaks and reports specific issues
with segment IDs, severity, corrections, and acceptance criteria. It checks
continuity, depth, song placement, spoken delivery, evidence, and pacing, and
names strong material to preserve. Stage 3 addresses the issues, does targeted
research, checks the complete revised narrative, polishes delivery/tags, and
accounts for every issue. There is no automatic fourth review or indefinite loop.

## Input documents and first approval

Use the existing project paths:

```text
<station>/metadata/episodes/<episode-id>/
  proposal.json       # premise, channel, duration, commentary budget, outline
  brief.json          # approved instructions, candidate songs, initial research
```

The proposal schema is unchanged. `brief.json` uses this shape:

```json
{
  "version": 1,
  "proposal_hash": "SHA256_OF_NORMALIZED_PROPOSAL_FROM_INSPECT",
  "instructions": "Explore the relationships and consequences. Conversational storytelling; no spoken source references.",
  "candidate_songs": [
    {
      "kind": "music",
      "id": "candidate-one",
      "playlist": "Classic Metal",
      "artist": "Canonical Artist",
      "title": "Canonical Title",
      "rationale": "Its potential role in the approved story"
    }
  ],
  "initial_research": [
    {"url": "https://example.com/original-interview", "claim": "A specific supported fact or attributed recollection."}
  ],
  "narration_words_per_minute": 165,
  "duration_tolerance_seconds": 120
}
```

The artist/title pair and playlist must resolve to an existing exact recording.
Candidates have unique IDs and artist/title identities. Codex may select a subset
and reorder candidates; it cannot add or substitute recordings. The speaking rate
and tolerance are explicit estimates approved with the concept. The writer sees
measured music durations and must leave enough room for narration. Exact speech
and total duration are measured again by the audio renderer.

Initialize with `init --proposal PATH --brief PATH` if both files are ready.
Alternatively initialize the proposal first, obtain `proposal_hash` with inspect,
then write `brief.json` in the project directory before requesting concept approval:

```bash
python -m neuralcast.cli.episode_producer init -s neuralforge --episode EPISODE_ID --proposal /tmp/proposal.json
python -m neuralcast.cli.episode_producer inspect -s neuralforge --episode EPISODE_ID
# Write brief.json using that proposal_hash; discuss the whole package with owner.
python -m neuralcast.cli.episode_producer inspect -s neuralforge --episode EPISODE_ID
python -m neuralcast.cli.episode_producer approve-proposal -s neuralforge --episode EPISODE_ID --hash CONCEPT_HASH
```

**Use `concept_hash`, not `proposal_hash`, for the first approval when a brief
exists.** It binds the proposal, candidate list, instructions, initial research,
and timing policy. Changes invalidate approval. Plan documents continue to
reference `proposal_hash`. Old proposal-only projects retain their existing
proposal approval; adding a brief requires approval of the combined concept.
Approval commands record an approval explicitly given by the owner, never one
inferred from files or an automated agent's verdict.

## Run the three stages

```bash
python -m neuralcast.cli.episode_producer editorial -s neuralforge --episode EPISODE_ID
```

The controller uses `codex exec --ephemeral`, `gpt-6.1-sol` at high reasoning
effort, live web research, a read-only sandbox, and JSON-schema output. It needs
an installed/authenticated Codex CLI with access to that model, plus ffprobe for
music-duration measurement. It does not need Gemini or AzuraCast credentials.
Each call has a 30-minute timeout; there are three calls maximum per successful
run. Timeouts/interruption terminate the child process group. Fresh sessions see
only their supplied snapshots and preceding stage documents, not a resumed chat.
Station/provider secrets are excluded from the subprocess environment. Raw CLI
logs are temporary because authentication diagnostics may be sensitive.

The controller validates each result before accepting it. Checks include schema,
complete scripts and companions, candidate identity, unique IDs, factual sources
on bridges, supported TTS tags, no spoken URLs, word limits, duration estimates,
review references, and exactly one resolution per review issue. These mechanical
checks do not prove historical accuracy or editorial quality; the agent review
and owner's manuscript review remain essential.

Successful output:

```text
<station>/metadata/episodes/<episode-id>/
  plan.json           # final complete manuscript; renderer input
  manuscript.md       # song list, full tagged transcript, estimates, changes/gaps

runtime/episodes/<station>/<episode-id>/
  editorial_latest.json
  editorial/<run-hash>/
    inputs.json       # approved context, exact prompts, schemas, model/effort
    starting_plan.json
    previous_plan.json  # if a plan existed before installation
    write.json
    review.json
    revise.json
    final_plan.json
    manuscript.md
    receipt.json
    <stage>/attempt-NNN/  # prompt, context, schema, raw response/failure if any
```

All editorial outputs are Git-ignored project/runtime data. Completed stages are
hashed and reused on an unchanged rerun. A failed stage leaves prior results
intact; rerunning executes only stages without an accepted result. Failed or
invalid outputs never replace the active plan. Concurrent manual plan edits stop
installation rather than being overwritten. Every replaced active plan is backed
up inside that run. A prompt, host guidance, concept, recording, or feedback change
creates a new run rather than rewriting the preceding run's stage history.

For owner feedback, use `editorial --feedback 'Specific requested changes'`.
This starts a fresh three-stage pass against the approved concept and the supplied
feedback; it does not implicitly resume a model conversation. If feedback relies
on exact previous wording, include that wording in the instructions. A change to
concept or candidate recordings belongs in the brief and needs concept approval.

## Second approval and rendering

Read `manuscript.md`. It displays estimated duration, exact tagged narration,
music order, changes, unresolved issues, and the manuscript hash. Research sources
remain in the JSON dossier outside spoken text. Unresolved issues/open questions
are exported visibly with status `blocked` and prevent manuscript approval.
Resolve them through another editorial pass or explicitly reviewed manual edits.

After the owner approves the actual final manuscript:

```bash
python -m neuralcast.cli.episode_producer approve-manuscript -s neuralforge --episode EPISODE_ID --hash MANUSCRIPT_HASH
python -m neuralcast.cli.episode_producer validate -s neuralforge --episode EPISODE_ID
python -m neuralcast.cli.episode_producer prepare -s neuralforge --episode EPISODE_ID
```

Approval binds the normalized full plan and concept, including scripts, song
order, rationales, and evidence. Any change invalidates it. `prepare` checks before
TTS and again before delivery. It synthesizes the exact supplied scripts and
never writes replacements. TTS caches depend on spoken text and voice settings;
changing a reading summary alone need not regenerate speech. Publication still
requires separate explicit approval of the finished render; neither editorial
completion nor manuscript approval grants public-release approval.

## Existing episodes

The Codex workshop is the main writing path. The per-segment Gemini text-writing
fallback has been removed. Existing complete manual `plan.json` documents can be
reviewed and approved with `approve-manuscript` without rerunning Codex. An old plan
with briefs but no scripts must first become a complete manuscript. Previously
rendered files, narration caches, receipts, historical queue records, published
catalogues and guides are preserved. Existing public episodes are not rewritten
or regenerated. Production revisions may change because writing/TTS inputs have
changed; do not delete old receipts or media to force reuse.
