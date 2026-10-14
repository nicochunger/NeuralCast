# New Releases editorial retention

The weekly New Releases updater reviews aging recordings before moving them into
permanent playlists. It invokes headless Codex with **`gpt-6.1-sol` and `high`
reasoning effort**. Decisions are automatic; routine review does not require
operator approval.

## Policy

Codex receives the station's current `playlist_descriptions.md`, permanent catalog
selections (including album names), the aging candidates, and previous decisions.
It can research recordings using live web search. It evaluates individual songs,
playlist fit, and redundant representation of an album rather than assigning all
tracks according to an artist's genre or popularity.

For each previously undecided batch, at most `floor(track_count / 5)` recordings
may be discarded. This is a **20% maximum, not a mandatory quota**. Batches smaller
than five cannot discard tracks. Weak evidence favors retention. Every kept track
must name an existing permanent playlist; every decision needs a rationale.

The release window remains `--days 120` by default. No new cron job is needed:
the existing Saturday maintenance command refreshes NeuralForge releases and
then syncs its media. NeuralCast uses the same policy when its updater is invoked,
but the existing weekly script does not refresh NeuralCast releases.

## Codex runtime requirements

- Install a Codex CLI supporting `exec`, `--ignore-user-config`, `--ephemeral`,
  `--output-schema`, and `--output-last-message` (development checked 0.159.2).
- Authenticate Codex for the Unix user running maintenance, with access to
  `gpt-6.1-sol`. Saved CLI authentication is reused; `CODEX_HOME` can locate it.
  Alternatively provide `CODEX_API_KEY` in that job's environment. The normal
  provider `.env` is not loaded by this adapter.
- Allow the Codex service and its web research capability through the deployment's
  network policy. CLI installation alone does not establish account/model access.

The adapter runs in a temporary snapshot directory with a read-only shell sandbox,
ignores interactive user configuration, and pins model, effort, and live search.
It does not receive AzuraCast, Gemini, or Spotify secrets in its subprocess
environment. Saved authentication and proxy/CA settings are retained. It has a
30-minute timeout and terminates its process group on timeout.

The first real use of account authentication/model access must be verified in the
scheduled environment. Offline tests mock this boundary; they do not prove live
Codex availability.

## Persistence and application

Validated decisions are atomically saved before media operations to:

```text
<station>/metadata/New Releases.editorial.json
```

The journal records batch identities, catalog input hash, model, reasoning effort,
timestamps, destinations, reasons, and research URLs. It is a decision journal,
not proof that every media operation completed. Outstanding CSV rows determine
which saved decisions need to be applied again. Retrying a partially completed
batch reuses its choices and never grants another 20% discard allowance.

The existing maintenance script includes this journal in catalog commits.
Discard decisions also act as discovery exclusions; existing manually maintained
`New Releases.exclusions.json` entries continue to apply.

- **Keep:** use the existing album-promotion and media-migration path, directed to
  Codex's chosen playlist. Newly added permanent rows remain unvalidated.
- **Discard:** remove only the unambiguous canonical MP3 in `songs/New Releases/`.
  The updater then removes its CSV row and companion metadata. Copies and rows in
  permanent playlists remain untouched. No station-global `[DEL]` marker is used.
- Ambiguous/noncanonical media, conflicting tags, symlinks within the source
  directory, and missing media roots leave the release pending with a warning.

Unavailable Codex, timeout, or invalid output keeps the aging batch pending while
fresh discoveries are saved normally. Malformed persisted journals fail closed
instead of being overwritten. Catalog changes during successful research abort
the update to avoid overwriting concurrent edits. Avoid editing catalogs during
maintenance; the snapshot check is not a transaction lock shared by all editors.
Direct updater runs use a station-scoped lock; scheduled maintenance additionally
holds its existing lock across refresh, sync, and publication.

## Preview and validation

```bash
python -m neuralcast.cli.update_new_releases -s neuralforge --dry-run
```

This can perform live discovery, Codex research, and album lookups, but does not
persist decisions or change station CSVs, metadata, or media. Temporary agent
files and an updater lock live outside station directories. A later apply may
produce different decisions because dry-run decisions are not persisted.

Offline tests:

```bash
python -m pytest tests/boundary/test_release_editorial.py tests/unit/neuralcast/pipelines/new_releases tests/boundary/test_catalog_maintenance_script.py
```
