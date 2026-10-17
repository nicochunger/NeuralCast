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
  three editorial stages and TTS inherit that channel's language and host style.

## Operator workflow

1. Read [docs/episode_editorial.md](../../../../docs/episode_editorial.md). Discuss
   initial research, topic, channel, narrative direction, language, timing, and
   candidate songs with the owner. Read the station playlist definitions, resolve
   existing recordings, and prepare `proposal.json` plus `brief.json`.
2. After the owner approves that package, use `inspect` and record
   `approve-proposal --hash CONCEPT_HASH`. This binds both files and the candidates;
   `proposal_hash` remains the plan's proposal reference, not the concept approval.
3. Run `editorial`: three sequential headless Codex calls research/write, review,
   and revise the complete manuscript. All stage prompts and a shared writing
   guide live under `assets/stories/prompts/episode_editorial/`. Each receives the
   approved context, resolved host guidance, and preceding stage outputs. Return
   `manuscript.md` with its song list and exact tagged transcript to the owner.
   Stop for manuscript approval; this step must never generate audio. Preserve
   stage snapshots and review reports. Do not silently ignore unresolved issues.
4. Make the opening explicitly introduce a podcast: welcome listeners to a new
   episode of the configured brand's podcast, introduce the theme and its appeal,
   and briefly preview the journey before the first song. Address newcomers who
   may never have heard the live station. For Spanish, an appropriate opening is
   “Bienvenidos a un nuevo episodio del podcast de NeuralForge…”; adapt it
   naturally to the topic, brand, and host language. Do not invent an episode
   number or release schedule. Bridges should not repeat the opening welcome;
   the closing resolves the theme and thanks the listener. This also applies
   when the agent provides edited scripts directly in the plan.
5. After explicit owner approval of the final manuscript, record
   `approve-manuscript --hash MANUSCRIPT_HASH`. Run `validate`, then `prepare`.
   Preparation synthesizes the exact approved scripts, renders one MP3, and
   **automatically uploads it to AzuraCast for private review without queueing
   it**. There is no per-segment Gemini writing fallback. Complete existing manual
   plans can be approved through the same manuscript gate. Give the owner the
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

This is a podcast-only workflow. The radio queue command and service operation
have been removed. Do not queue episode audio through another tool, schedule it,
or add it to a live rotation playlist as part of episode production. RSS is
outside this version. The existing live host orchestrator remains separate.

## Ownership and reuse

- `models.py`: strict proposal, plan, recording, evidence, and commentary schemas.
- `editorial_models.py`: approved concept/song brief and three stage contracts.
- `editorial.py`: persisted sequential editing, validation, and Markdown export.
- `codex.py`: read-only headless Codex boundary with web research and strict JSON.
- `manuscript.py`: complete-script checks and exact owner approval before TTS.
- `storage.py`: project paths, hashes, atomic receipts, approvals, and locks.
- `library.py`: read-only resolution of exact catalog recordings and artwork.
- `narration.py`: resolved host guidance and TTS of frozen scripts; no text writing.
- `rendering.py`: audio assembly, loudness handling, and measured chapter timings.
- `service.py`: production and preview upload; no radio queue submission.
- `publication.py`: permanent website catalogue, approved audio, and guide assets.
- `presentation.py`: read-only support for the original radio pilot's guides.

Reuse the existing host/provider tooling. Podcast writing guidance lives at
`src/neuralcast/assets/stories/prompts/episode_commentary.md`. Published episodes
use permanent Episode guide pages in the sister `neuralcast-web` project.
Do not add an episode-review page, scheduled production, or live-host changes.

## Operational boundaries

- Local editorial documents live in `<station>/metadata/episodes/<episode-id>/`;
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
