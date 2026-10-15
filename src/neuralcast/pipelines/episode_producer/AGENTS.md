# Manual episode producer

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
   `plan.json`, referencing the approved proposal hash. The document schemas and
   examples are in the workflow documentation and `models.py`.
4. Run `validate`, then `prepare`. Preparation generates commentary and summaries,
   synthesizes narration, renders one MP3, and **automatically uploads it to
   AzuraCast for preview without queueing it**. Give the owner its media location
   and edition so they can listen in AzuraCast. Revise and prepare again if asked.
5. Only after the owner explicitly approves the finished recording for airing,
   run `approve-and-queue --hash RENDER_SHA256` for that reviewed render.
6. Website publication is a separate, explicit approval. Use `publish --hash
   RENDER_SHA256 --description 'Listener-facing description'` to publish a
   permanent on-demand episode and its guide. Do not infer public publication
   approval from a preview upload or a radio queue approval. This never queues
   audio. Preserve `runtime/episodes/catalogue/` and the referenced AzuraCast
   episode recordings permanently and back them up together. No RSS is generated.

Use the package entrypoint, with explicit station and episode identifiers:

```bash
python -m neuralcast.cli.episode_producer ACTION -s neuralforge --episode EPISODE_ID
```

`init` also requires `--proposal PATH`. The two approval actions require their
respective `--hash`. Use `--help` for the supported arguments.

Proposal approval and final airing approval are distinct. Never infer either
from an existing document or upload. Honor explicit approvals already given in
the conversation; do not ask the owner to approve the same unchanged material
again. A changed proposal needs renewed proposal approval, and a changed render
needs renewed airing approval.

## Ownership and reuse

- `models.py`: strict proposal, plan, recording, evidence, and commentary schemas.
- `storage.py`: project paths, hashes, atomic receipts, approval checks, and locks.
- `library.py`: read-only resolution of exact catalog recordings and artwork.
- `narration.py`: existing channel personality, prompts, speech validation, and TTS.
- `rendering.py`: audio assembly, loudness handling, and measured chapter timings.
- `service.py`: production, preview upload, and explicit queue submission.
- `presentation.py`: approved listener guides and chapter artwork.
- `publication.py`: permanent website catalogue, approved audio, and guide assets.

Reuse the existing host/provider tooling. Episode writing guidance lives at
`src/neuralcast/assets/stories/prompts/episode_commentary.md`. The listener guide
belongs in the existing Host recap popup in the sister `neuralcast-web` project.
Do not add an episode-review page, automatic scheduling, or host-cadence changes
to this manual workflow.

## Operational boundaries

- Editorial documents live in `<station>/metadata/episodes/<episode-id>/`.
  Generated assets, approvals, and receipts live under `runtime/episodes/`.
- Existing station MP3s are authoritative live media. Read them directly; do not
  run sync, download replacements, or modify catalog rows as part of production.
- Preserve runtime receipts. Finished revisions are immutable; do not delete a
  receipt to force regeneration or bypass a previous upload/queue attempt.
- Upload previews remain unassigned media under `Episodes/`, outside ordinary
  host-snippet cleanup. Uploading is not permission to queue.
- A timeout can hide an accepted upload or queue request. Inspect AzuraCast and
  follow the documented recovery procedure before retrying an uncertain action.
- Default tests must mock provider, subprocess, and remote-station boundaries.
  The opt-in synthetic ffmpeg test requires no live station or credentials.
