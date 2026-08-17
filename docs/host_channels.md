# Multilingual host channels

The host orchestrator separates three reusable configuration layers in
`src/neuralcast/assets/stories/host_channels.json`:

- a **brand** selects the shared catalog/metadata directory, cadence, cover art,
  and language-neutral personality;
- a **locale** selects output language, TTS guidance/voice, presentation labels,
  schedule phrases, and deterministic fallback copy;
- a **channel** selects the AzuraCast target station and combines one brand with
  one locale.

`neuralcast-en` currently targets the AzuraCast station shortcode
`neuralcast_shared_media_test`. It reads NeuralCast playlists and metadata and
uses NeuralCast's shared AzuraCast media root; it does not create a second music
catalog.

Run an English test cycle with:

```bash
python -m neuralcast.cli.host_orchestrator \
  --channel neuralcast-en \
  --force-archetype back_sell \
  --min-listeners 0 \
  --dry-run
```

Remove `--dry-run` only when the generated segment should be uploaded and
queued. Legacy `-s neuralcast` and `-s neuralforge` commands remain supported
and resolve to their Spanish channels.

Each channel has isolated state, locks, logs, generated snippets, and remote
media prefixes. A channel using shared storage must set
`media_owner_station` (or an explicit `liquidsoap_media_root`) to the station
whose physical media directory Liquidsoap can read.

To add a language, add a locale entry and its TTS instruction file, then add one
or more channel entries that reference it. To add another stream for an existing
language, only a channel entry is required.
