# Multilingual host channels

The host orchestrator separates three reusable configuration layers in
`src/neuralcast/assets/stories/host_channels.json`:

- a **brand** selects the shared catalog/metadata directory, cadence, cover art,
  and language-neutral personality;
- a **locale** selects output language, TTS guidance/voice, presentation labels,
  schedule phrases, and deterministic fallback copy;
- a **channel** selects the AzuraCast target station and combines one brand with
  one locale.

`neuralforge-fr` targets the AzuraCast station shortcode `neuralforge_fr`. It
reads NeuralForge playlists and metadata and uses NeuralForge's shared
AzuraCast media root; it does not create a second music catalog. Its locale uses
a complete French prompt pack and a natural Swiss Romand accent direction.

`neuralcast-en` remains configured for the retained AzuraCast station
`neuralcast_shared_media_test`, but that station and its cron cycle are disabled.

Run a French test cycle with:

```bash
python -m neuralcast.cli.host_orchestrator \
  --channel neuralforge-fr \
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

Channels may independently select `cadence_profile` and `archetype_profile`.
These reference an existing station policy without changing the channel's
brand, catalog, language, or target stream. For example, `neuralcast-en` keeps
the NeuralCast identity and English locale while using NeuralForge's speaking
cadence, cooldowns, and enabled-archetype policy.

To add a language, add a locale entry, its complete prompt directory, and its TTS
instruction file, then add one or more channel entries that reference it. To add
another stream for an existing language, only a channel entry is required.
