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
  four editorial stages and TTS inherit that channel's language and host style.
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
3. Run `editorial`: four sequential headless Codex calls research/write, review,
   revise the content, and polish speech/delivery. The last pass has web search
   disabled and can change only narration scripts, preserving the segment/song
   order, sources, companions and other plan metadata. All stage prompts and a shared writing
   guide live under `assets/stories/prompts/episode_editorial/`. Each receives the
   approved context, resolved host guidance, and preceding stage outputs. Return
   `manuscript.md` with its song list and exact tagged transcript to the owner.
   Stop for manuscript approval; this step must never generate audio. Preserve
   stage snapshots and review reports. Do not silently ignore unresolved issues.
   Use `delivery --feedback '...'` to run just the speech pass on an unpublished
   complete plan with an approved concept/brief. It saves the previous plan,
   manuscript and approval receipt, generates an exact before/after report under
   runtime, and updates the active plan/Markdown together. Changed scripts require
   manuscript approval again. Delivery cannot clear existing editorial blockers
   or rewrite published episodes; content problems go back to editorial revision.
   Apply the shared guide's spoken examples from Stage 1 onward. Stage 2 must
   assess conversational delivery with concrete excerpts, and Stage 3 must resolve
   the resulting issues rather than defer them. Stage 4 can rewrite sentences and
   paragraphs within a script while retaining necessary facts, qualifications,
   narrative purpose and locked metadata; it is not limited to copyediting.
   Check repeated analytical framing and uniform cadence, then choose purposeful
   vocal cues without quotas. Its final self-check is not an independent review;
   structural validation alone does not establish natural speech. Guidance edits
   do not authorize rerunning or changing an existing episode.
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
- `editorial_models.py`: approved concept/song brief and four stage contracts.
- `editorial.py`: persisted sequential editing, validation, and Markdown export.
- `delivery.py`: script-only patch validation and owner-readable before/after reports.
- `services/editorial_agent.py`: shared read-only headless Codex boundary with web
  research and strict JSON; episode editorial uses its default research-enabled mode.
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
`src/neuralcast/assets/stories/prompts/episode_commentary.md`. Published episodes
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
