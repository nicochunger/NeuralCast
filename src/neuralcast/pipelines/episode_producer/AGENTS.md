# Manual podcast producer

Before planning, producing, or publishing an episode, read the complete workflow:
[docs/episode_producer.md](../../../../docs/episode_producer.md).
Repository-wide instructions still apply. Run commands from the repository root.

## Concept defaults

- Default episode language: Argentinian Spanish (`es-AR`), with natural voseo and
  restrained Rioplatense phrasing, unless the owner explicitly requests another
  language. Do not ask the owner to choose the language again for every concept.
- The owner selects NeuralForge or NeuralCast during the concept conversation.
  Carry that choice into the station argument, proposal channel, brand, playlist
  selection, and media destination. Do not infer the station from the CLI default
  or a preceding episode; if it has not been specified for this concept, ask.
- Resolve the selected station's Spanish channel through the current channel
  registry (`host_channels.json`); currently `neuralforge-es` and `neuralcast-es`.
  Record the resolved channel in `proposal.json` before concept approval. All
  editorial jobs and TTS inherit that channel's language and host style.
- Assign the station-local publication number with `init --episode-number N`
  or `number --episode-number N`. Display metadata lives in `proposal.json`;
  document folders use zero-padded numbers such as `003-laberintos-humanos`.
  Keep the stable slug in `--episode`, runtime/media paths, and public URLs.
  The display-only number is excluded from normalized approval/render inputs;
  numbering does not change manuscript approval or require new approval.
  Preserve a backup of published catalogue snapshots before assigning numbers
  to existing publications. Do not rewrite historical render/editorial receipts.

## Operator workflow

The standard structure for new manuscripts is opening → song
→ bridge → song → … → closing. Each uninterrupted host passage is one commentary,
one complete script, one companion, and one TTS generation. Never split a passage
into adjacent commentaries; paragraphs must carry the previous song's thought
forward through the story into the next song without repeated setups. Set a
word budget for the whole passage (up to 3000 words, within the approved episode
budget). Keep host companion/chapter titles at most 40 characters. The renderer
inserts 1.5 seconds of real silence before every host passage, inside
its chapter, so do not require a leading pause tag. Silence counts toward total
duration, outside the speech budget. Use the editorial plan schema for manual
plans. Generated and published recordings are immutable production artifacts.

1. Read [docs/episode_editorial.md](../../../../docs/episode_editorial.md). Discuss
   initial research, topic, channel, narrative direction, language, timing, and
   candidate songs with the owner. Read the station playlist definitions, resolve
   existing recordings, and prepare `proposal.json` with an `editorial_brief`
   object containing instructions, candidate songs, research, and timing policy.
2. After the owner approves that package, use `inspect` and record
   `approve-proposal --hash CONCEPT_HASH`. This binds the proposal, embedded brief,
   and candidates;
   `proposal_hash` remains the plan's proposal reference, not the concept approval.
3. Run `editorial`: Codex researches and writes; Python runs Codex factual review,
   Gemini narrative review and Gemini voice review in parallel; one direct Gemini
   edit resolves findings and finishes content, speech and performance. There are
   five jobs in a normal successful run. Gemini uses `gemini-4-argon`, structured
   requests and no tools. Codex research jobs use `gpt-6.1-sol` with subagents
   disabled. The controller saves original reviewer findings with assigned IDs;
   the editor resolves, dismisses with a reason or leaves each unresolved.
   Unresolved findings block approval. All three reviews must complete.
   Use the single podcast writing guide and selected labeled examples. Host locale
   and personality belong in context; do not supply unrelated radio operation
   instructions or complete playlist definitions after concept approval.
   Return `manuscript.md` with exact tagged narration to the owner. This workflow
   must stop before audio and preserve all job snapshots and findings. Mechanical
   checks do not establish historical truth or natural speech.
   Each failed job gets at most one automatic targeted retry; accepted jobs are
   retained. Save elapsed time and available usage, with unknown usage as null.
   Do not retry an entire workshop to repair one report.
   Use `delivery --feedback '...'` for one direct Gemini narration edit of an
   unpublished complete manuscript with an approved concept and no editorial
   blockers. It reuses research, locks non-script metadata, saves before/after
   snapshots and uses the same final editor. Changed scripts need renewed owner
   manuscript approval. Routine recording checks stay in production notes for
   completion before audio preparation; suspected recording incompatibilities
   affecting the story remain consequential editorial questions.
   Guidance changes do not authorize rewriting or regenerating an episode.
4. Make the opening explicitly introduce a podcast: welcome listeners to a new
   episode of the configured brand's podcast, introduce the theme and its appeal,
   and briefly preview the journey before the first song. Address newcomers who
   may never have heard the live station. For Spanish, an appropriate opening is
   “Bienvenidos a un nuevo episodio del podcast de NeuralForsch…”; adapt it
   naturally to the topic, brand, and host language. Use "Bienvenidos" without
   doubling the Spanish greeting. "NeuralForsch" is the NeuralForge TTS spelling
   in spoken scripts only; titles, summaries, and metadata keep "NeuralForge".
   Introduce bands on their own merits in the opening, keeping relevant verified
   influences for their later song explanations. Do not invent an episode
   number or release schedule. Bridges should not repeat the opening welcome;
   the closing resolves the theme and thanks the listener. This also applies
   when the agent provides edited scripts directly in the plan.
5. After explicit owner approval of the final manuscript, record
   `approve-manuscript --hash MANUSCRIPT_HASH`. Run `validate`, then `prepare`.
   Preparation synthesizes the exact approved scripts, renders one MP3, and
   **automatically uploads it to AzuraCast for private review without queueing
   it**. Complete manual plans use the same manuscript approval gate. Give the owner the
   media location and edition; changed manuscripts require renewed approval.
6. After explicit approval of the finished recording for public on-demand
   release, run `publish --hash RENDER_SHA256 --description 'Listener-facing
   description'`. This publishes the permanent podcast and Episode guide.

Use `python -m neuralcast.cli.episode_producer ACTION -s neuralforge --episode
EPISODE_ID`. `init` requires `--proposal PATH` and accepts `--brief PATH`. Concept,
manuscript, and publication approvals require their respective `--hash`.

Concept, manuscript, and publication approvals are distinct. Never infer any from
an existing document or upload. Honor explicit approvals already given in the
conversation; do not ask the owner to approve unchanged material again. Changed
proposals or recordings need renewed approval for the applicable stage.

This is a podcast-only workflow. Do not queue episode audio through another tool,
schedule it,
or add it to a live rotation playlist as part of episode production. RSS and
cloud chapters read only the published catalogue. The existing live host
orchestrator remains separate.

## Ownership and reuse

### Standard working-file contract

- Use only `proposal.json`, `plan.json`, and `manuscript.md` in a completed
  episode's document folder. Initialization needs only `proposal.json`; editorial
  production adds the plan and readable manuscript before owner approval.
- Store `episode_number` and `editorial_brief` in `proposal.json`. The embedded
  brief is required for the editorial workshop. Manual manuscripts use the
  manuscript approval gate. Never infer approval.
- `plan.json` is the authoritative renderer input. Keep `manuscript.md` in sync
  after every narration edit, including speech tags. Keep only the final produced
  edition in a published episode's working folder.
- Do not add `episode.json`, `brief.json`, `proposal.md`, `script.md`, or
  edition-suffixed draft copies. Preserve old drafts in runtime backups and leave
  historical editorial/production receipts, approvals, and catalogue assets intact.
- `init --brief PATH` imports a supplied brief into the proposal without creating
  another working file. Editing the brief changes the concept hash.

### Package responsibilities

- `models.py`: strict proposal, plan, recording, evidence, and commentary schemas.
- `editorial_models.py`: approved concepts, focused findings and final edits.
- `editorial.py`: persisted jobs, parallel review, validation and Markdown export.
- `delivery.py`: final-edit boundaries and owner-readable before/after reports.
- `reviews.py`: review roles, curated examples and the single finding ledger.
- `services/editorial_agent.py`: shared read-only headless Codex boundary with web
  research, strict JSON, deadlines and usage capture.
- `services/editorial_text.py`: bounded, tool-free Gemini structured requests.
- `manuscript.py`: complete-script checks and exact owner approval before TTS.
- `storage.py`: project paths, hashes, atomic receipts, approvals, and locks.
- `library.py`: read-only resolution of exact catalog recordings and artwork.
- `narration.py`: resolved host guidance and TTS of frozen scripts; no text writing.
- `rendering.py`: audio assembly, loudness handling, and measured chapter timings.
- `service.py`: production and preview upload; no radio queue submission.
- `publication.py`: permanent website catalogue, approved audio, and guide assets.
- `rss.py`: station RSS and Podcasting 2.0 cloud chapters from published snapshots.
  Show settings, titled covers, and short chapter-title overrides live under
  `assets/podcasts/`. Keep RSS titles at most 40 characters; do not rewrite
  publication manifests or audio to shorten display titles.
- `presentation.py`: read-only live episode guides.

Reuse the existing host/provider tooling. Podcast writing guidance lives at
`src/neuralcast/assets/stories/prompts/episode_editorial/writing_guide.md`. Published episodes
use permanent Episode guide pages in the sister `neuralcast-web` project.
Do not add an episode-review page, scheduled production, or live-host changes.

## Operational boundaries

- Local editorial documents live in `<station>/metadata/episodes/<NNN>-<episode-id>/`
  generated assets, approvals, and receipts live under `runtime/episodes/`.
- Station MP3s are authoritative live media. Read existing recordings directly;
  do not run sync, download replacements, or edit catalog rows for production.
- Finished revisions are immutable. Preserve all production/upload receipts and
  any historical queue records; never clear them to force regeneration/retry.
- Preview uploads remain unassigned under `Episodes/`, outside host cleanup.
  An uncertain upload may have succeeded: inspect AzuraCast before retrying.
- Preserve and back up `runtime/episodes/catalogue/` with its referenced AzuraCast
  episode MP3s. Guide artwork is copied there for permanent publication.
- Do not regenerate or replace a published episode to apply new writing guidance.
  The podcast introduction guidance applies to future productions/revisions.
- Default tests mock providers, subprocesses, and remote station boundaries.
  The opt-in synthetic ffmpeg test needs no station media or credentials.
