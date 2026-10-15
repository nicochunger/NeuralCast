# Manual podcast producer

Before planning, producing, or publishing an episode, read the complete workflow:
[docs/episode_producer.md](../../../../docs/episode_producer.md).
Repository-wide instructions still apply. Run commands from the repository root.

## Operator workflow

1. Discuss the topic, station/channel, premise, duration, and narrative arc with
   the owner. Write `proposal.json` and initialize the project with `init`.
2. After the owner approves that proposal, use `inspect` to obtain its hash and
   record the approval with `approve-proposal --hash PROPOSAL_SHA256`.
3. Read the station's playlist definitions and select exact recordings already
   present in its catalog and media library. Research supporting facts and write
   `plan.json`, referencing the approved proposal hash. See the workflow docs and
   `models.py` for document schemas and examples.
4. Make the opening explicitly introduce a podcast: welcome listeners to a new
   episode of the configured brand's podcast, introduce the theme and its appeal,
   and briefly preview the journey before the first song. Address newcomers who
   may never have heard the live station. For Spanish, an appropriate opening is
   “Bienvenidos a un nuevo episodio del podcast de NeuralForge…”; adapt it
   naturally to the topic, brand, and host language. Do not invent an episode
   number or release schedule. Bridges should not repeat the opening welcome;
   the closing resolves the theme and thanks the listener. This also applies
   when the agent provides edited scripts directly in the plan.
5. Run `validate`, then `prepare`. Preparation generates scripts and summaries,
   synthesizes narration, renders one MP3, and **automatically uploads it to
   AzuraCast for private review without queueing it**. Give the owner its media
   location and edition. Revise and prepare again if requested.
6. After explicit approval of the finished recording for public on-demand
   release, run `publish --hash RENDER_SHA256 --description 'Listener-facing
   description'`. This publishes the permanent podcast and Episode guide.

Use `python -m neuralcast.cli.episode_producer ACTION -s neuralforge --episode
EPISODE_ID`. `init` also requires `--proposal PATH`. Proposal approval and
publication require their respective `--hash`; use `--help` for arguments.

Proposal approval and publication approval are distinct. Never infer either from
an existing document or upload. Honor explicit approvals already given in the
conversation; do not ask the owner to approve unchanged material again. Changed
proposals or recordings need renewed approval for the applicable stage.

This is a podcast-only workflow. The radio queue command and service operation
have been removed. Do not queue episode audio through another tool, schedule it,
or add it to a live rotation playlist as part of episode production. RSS is
outside this version. The existing live host orchestrator remains separate.

## Ownership and reuse

- `models.py`: strict proposal, plan, recording, evidence, and commentary schemas.
- `storage.py`: project paths, hashes, atomic receipts, approvals, and locks.
- `library.py`: read-only resolution of exact catalog recordings and artwork.
- `narration.py`: existing channel personality, prompts, speech validation, and TTS.
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
