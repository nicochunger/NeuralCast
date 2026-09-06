# Multilingual host channels

The host orchestrator separates four reusable configuration layers. Three live
in `src/neuralcast/assets/stories/host_channels.json`:

- a **brand** selects the shared catalog/metadata directory, cadence, cover art,
  and language-neutral personality;
- a **locale** selects output language, TTS guidance/voice, presentation labels,
  schedule phrases, and deterministic fallback copy;
- a **channel** selects the AzuraCast target station and combines one brand with
  one locale.

Archetype behavior lives separately in
`src/neuralcast/assets/stories/archetype_profiles.json`. It defines reusable,
inheritable profiles for weights, cooldowns, lead times, generation settings,
search, news topics/freshness, and concert countries. News topics use stable
IDs and concert countries use ISO-style codes; their spoken labels are localized
in the same file.

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
The archetype name references a profile in `archetype_profiles.json` without
changing the channel's brand, catalog, language, or target stream. For example,
`neuralcast-en` keeps the NeuralCast identity and English locale while using
NeuralForge's speaking cadence, cooldowns, and enabled-archetype policy.

## Policy format and precedence

The policy file has three top-level catalogs:

- `news_topics`: stable topic IDs with localized labels;
- `concert_countries`: uppercase country codes with localized labels and input
  aliases;
- `profiles`: reusable archetype policies.

A root profile defines every `Archetype`. A derived profile uses `extends` and
changes only the required fields through `archetype_overrides`. At runtime the
effective values are resolved in this order:

1. Complete root profile.
2. Each inherited profile's `archetype_overrides`.
3. The selected channel's `archetype_overrides`.

An archetype policy can configure:

```json
{
  "enabled": true,
  "automatic": true,
  "weight": 0.15,
  "cooldown_seconds": 7200,
  "lead_time_seconds": 90,
  "temperature_range": [0.45, 0.85],
  "top_p_range": [0.88, 0.95],
  "hook_free_probability": 0.25,
  "search_enabled": true
}
```

The `news` archetype additionally owns `news.topic_ids`, `max_age_hours`, and
`preferred_max_age_hours`. The `concert_check` archetype additionally owns
`concert_check.country_codes`. These specialized lists are the only source for
the corresponding prompt and validation scope.

Use canonical identifiers in policy configuration and structured LLM metadata:

```json
{
  "topic_id": "switzerland_general",
  "country_code": "CH"
}
```

Human-readable `topic` and `country` values remain available for presentation,
but they do not define policy. Add translations to the catalogs instead of
placing translated strings in channel overrides.

For list overrides, `replace` cannot be combined with `add` or `remove` in the
same object. An override may not resolve to an empty topic or country list.

A channel can then apply narrow `archetype_overrides` without copying an entire
profile. List-valued settings support `add`, `remove`, or `replace`. For example:

```json
{
  "archetype_profile": "neuralforge",
  "archetype_overrides": {
    "news": {
      "news": {
        "topics": {"remove": ["argentina_politics_general"]}
      }
    },
    "concert_check": {
      "concert_check": {
        "countries": {"replace": ["CH"]}
      }
    }
  }
}
```

This is the effective policy for `neuralforge-fr`: its news pool retains all
NeuralForge topics except Argentine political/general news, and its concert
check accepts Swiss dates only. `neuralforge-es` inherits the unmodified
NeuralForge profile and continues to cover both Argentina and Switzerland.

Configuration is validated at startup. Unknown archetypes, topic IDs, country
codes, fields, inheritance cycles, invalid numeric ranges, and empty effective
topic/country lists fail fast. The resolved channel policy is used by selection,
prompt construction, format repair, post-generation validation, and runtime
logging, so a model response outside the configured scope is rejected.

After changing either JSON file, run at least:

```bash
python -m json.tool src/neuralcast/assets/stories/archetype_profiles.json >/dev/null
python -m json.tool src/neuralcast/assets/stories/host_channels.json >/dev/null
python -m pytest tests/unit/neuralcast/pipelines/host_orchestrator
```

To add a language, add a locale entry, its complete prompt directory, and its TTS
instruction file, then add one or more channel entries that reference it. To add
another stream for an existing language, only a channel entry is required. Add a
new shared archetype policy only when several channels need the same behavior;
otherwise prefer a small channel override.
