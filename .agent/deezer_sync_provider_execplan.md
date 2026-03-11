# ExecPlan: Replace sync-time Spotify lookups with Deezer

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work proceeds.

This document must be maintained in accordance with `.agent/PLANS.md`.

## Purpose / Big Picture

The playlist sync pipeline currently depends on Spotify for two user-visible jobs: deciding whether a missing song exists at all, and guessing or confirming the correct album when a CSV row is incomplete. Spotify is no longer usable for this repository’s current app credentials, so sync needs a new primary provider. After this change, running `python main.py --station neuralforge --dry-run` should validate missing songs and backfill albums using Deezer first, while still keeping MusicBrainz and iTunes as fallbacks so niche tracks are not lost when Deezer has gaps.

The observable outcome is that sync no longer fails early because Spotify rejects requests. A human should be able to run the sync dry-run, see Deezer-backed validation and album lookup continue to work, and confirm the change with targeted tests that exercise Deezer-first provider ordering.

## Progress

- [x] (2026-03-11 14:40Z) Inspected the sync pipeline and confirmed the Spotify-dependent touchpoints are `src/neuralcast/services/validation.py`, `src/neuralcast/metadata/album_lookup.py`, and `src/neuralcast/pipelines/playlist_sync.py`.
- [x] (2026-03-11 14:40Z) Verified live Deezer responses for representative track, album, and track-detail queries and confirmed that Deezer exposes album title, release date, record type, contributor data, and rank needed for sync-time album matching.
- [x] (2026-03-11 14:46Z) Added `src/neuralcast/services/deezer.py` with anonymous Deezer request pacing, quota backoff, and basic track/album/detail helpers.
- [x] (2026-03-11 14:47Z) Replaced sync-time validation in `src/neuralcast/services/validation.py` so the active provider order is Deezer, then MusicBrainz, then iTunes, while keeping compatibility wrappers for the old `spotify_ok` function names.
- [x] (2026-03-11 14:50Z) Extended `src/neuralcast/metadata/album_lookup.py` with Deezer album candidates and iTunes album candidates, and added `prefer_deezer` without removing the older Spotify-capable path.
- [x] (2026-03-11 14:50Z) Updated `src/neuralcast/pipelines/playlist_sync.py` so missing-song album backfill explicitly asks for Deezer-first lookup instead of Spotify-first lookup.
- [x] (2026-03-11 14:52Z) Added focused automated tests in `tests/test_deezer_sync_provider.py` for Deezer-backed validation, Deezer-backed album candidate generation, iTunes fallback, and Deezer-first sync backfill.
- [x] (2026-03-11 14:53Z) Ran `pytest -q tests/test_deezer_sync_provider.py tests/test_playlist_sync.py` successfully (`6 passed`).
- [x] (2026-03-11 14:55Z) Started a real `python main.py --station neuralforge --dry-run` and confirmed it entered normal file-processing output without immediate Spotify permission errors.

## Surprises & Discoveries

- Observation: Deezer’s anonymous API returns enough metadata for sync-time album guessing, including `record_type`, `release_date`, and contributor information, but that information is split across search and detail endpoints rather than being fully present in one search result.
  Evidence: live checks against `https://api.deezer.com/search/track`, `https://api.deezer.com/track/<id>`, and `https://api.deezer.com/album/<id>` for `Ghost - Rats`.
- Observation: Deezer is still incomplete for niche catalog entries, so it cannot be the only provider in sync.
  Evidence: live searches for `Neperia - Minokawa` and album `Minokawa` returned empty Deezer results even though iTunes validation had previously succeeded in this repository.
- Observation: the current album-lookup fallback path only alternates between Spotify and MusicBrainz; iTunes is not currently involved in album guessing.
  Evidence: `src/neuralcast/metadata/album_lookup.py` contains `_spotify_candidates`, `_musicbrainz_candidates`, and two-provider fallback logic in `guess_album`, but no iTunes candidate builder.
- Observation: retaining the exported `spotify_ok` and `spotify_album_ok` names as wrappers avoids breaking any local scripts or notebooks that may still import those symbols directly.
  Evidence: internal repository searches showed no remaining code references, which made compatibility wrappers a low-risk safety measure.
- Observation: a full station dry-run starts normally after the provider swap, but it still takes real time because sync continues to walk the station catalog and retag files before it reaches every missing-song validation call.
  Evidence: the observed dry-run output progressed into normal MP3 processing (`Evergrey - OXYGEN!.mp3`) rather than failing immediately at import time or on the first provider call.
- Observation: this repository’s sync “dry-run” can still backfill playlist rows in some paths, so validation runs must be checked for unintended CSV diffs afterward.
  Evidence: a test run briefly changed `NeuralForge/playlists/Melodic Death Metal.csv` by backfilling `Neperia - Minokawa`; that change was reverted immediately after inspection.

## Decision Log

- Decision: introduce a small shared Deezer helper module under `src/neuralcast/services/` instead of duplicating request pacing and quota handling inside both validation and album lookup.
  Rationale: the sync pipeline needs the same anonymous Deezer request behavior in two modules, and a shared helper keeps the provider swap coherent without changing the existing standalone Deezer new-releases pipeline in this milestone.
  Date/Author: 2026-03-11 / Codex
- Decision: keep Spotify-capable code paths in `album_lookup.py` for compatibility, but make `playlist_sync.py` explicitly request Deezer-first album lookup.
  Rationale: the user asked to replace Spotify with Deezer in the sync pipeline, not to rewrite or delete every remaining Spotify-oriented path in the repository.
  Date/Author: 2026-03-11 / Codex
- Decision: add an iTunes album-candidate builder during this change.
  Rationale: the requested fallback set for sync is Deezer plus MusicBrainz plus iTunes, and album backfill should honor that same redundancy rather than stopping at Deezer and MusicBrainz only.
  Date/Author: 2026-03-11 / Codex

## Outcomes & Retrospective

The sync pipeline now uses Deezer as its primary metadata provider for missing-song validation and album backfill, with MusicBrainz and iTunes still present as fallbacks. The change is localized to the sync path: `validation.py` now validates against Deezer first, `album_lookup.py` can build Deezer and iTunes `AlbumMatch` candidates, and `playlist_sync.py` explicitly asks for Deezer-first lookup when backfilling albums.

The focused automated test suite passed, and a real `neuralforge` dry-run started successfully and reached normal file-processing output without reproducing the old Spotify permission failure immediately. The remaining caveat is operational rather than functional: anonymous Deezer quota limits can still slow a large sync, especially when many missing songs require album backfill.

## Context and Orientation

The repository’s main sync entrypoint is the root script `main.py`, which dispatches into `src/neuralcast/cli/sync_playlists.py` and then into `src/neuralcast/pipelines/playlist_sync.py`. During a sync, missing songs are first checked for existence and then, if necessary, their album field is verified or backfilled.

Song existence and album verification live in `src/neuralcast/services/validation.py`. That file currently talks to Spotify, MusicBrainz, and iTunes. Album guessing lives in `src/neuralcast/metadata/album_lookup.py`. That file currently prefers Spotify and can fall back to MusicBrainz, but it has no iTunes candidate provider yet. The sync pipeline calls these pieces from `_backfill_album_for_missing_song` in `src/neuralcast/pipelines/playlist_sync.py`.

In this repository, “validation” means asking a metadata provider whether a track or track+album combination appears to exist. “Album guessing” means searching provider metadata for the most likely studio album for a given artist/title pair and returning an `AlbumMatch` dataclass with a candidate album name, provider source, release date, confidence score, and ranking signals.

## Plan of Work

Add `src/neuralcast/services/deezer.py` as a shared helper module. It will provide anonymous Deezer request functions with per-request pacing, quota backoff, basic JSON validation, and simple convenience wrappers for track search, album search, album detail, and track detail. It will not introduce new persistent caches or files.

Update `src/neuralcast/services/validation.py` to import the new Deezer helper and add Deezer-backed validation functions. The active validation path will become `deezer_ok` or `mb_ok` or `itunes_ok` for songs, and `deezer_album_ok` or `mb_album_ok` or `itunes_album_ok` for album validation. The verbose album-validation payload will expose a `deezer` key instead of a `spotify` key because the active primary provider is changing.

Update `src/neuralcast/metadata/album_lookup.py` to add `_deezer_candidates` and `_itunes_candidates`. `_deezer_candidates` will search Deezer tracks, enrich album details via album IDs, and construct `AlbumMatch` objects using Deezer rank in place of Spotify popularity. `_itunes_candidates` will search iTunes songs and build lower-confidence fallback matches when Deezer and MusicBrainz have nothing useful. The provider-ordering code will be generalized so callers can ask for Deezer-first lookup without deleting the older Spotify-oriented path.

Update `src/neuralcast/pipelines/playlist_sync.py` so `_backfill_album_for_missing_song` explicitly calls `guess_album(..., prefer_deezer=True, prefer_spotify=False, allow_fallback=True)`. This keeps the change isolated to sync even though `album_lookup.py` remains backwards-compatible for other callers.

Add automated tests in a new file under `tests/` that patch the Deezer helper functions and verify three things: song validation succeeds on a Deezer match, album lookup produces a Deezer `AlbumMatch` with the expected metadata, and Deezer-first guessing falls through to iTunes when Deezer and MusicBrainz produce nothing.

## Concrete Steps

Run from the repository root:

    git status --short
    pytest -q tests/test_deezer_sync_provider.py tests/test_playlist_sync.py
    python main.py --station neuralforge --dry-run

The first command confirms the only unrelated local change is the user’s manual playlist edit. The second command exercises the new sync-provider tests and currently succeeds with `6 passed`. The third command is the manual validation step after implementation; it should no longer emit Spotify permission failures during missing-song validation and album backfill.

## Validation and Acceptance

Acceptance means three things are true.

First, unit tests pass for Deezer-backed validation and album lookup. In particular, a new test file should prove that `verified()` can succeed from a Deezer track result, `verified_album(..., verbose=True)` reports Deezer rather than Spotify, and `guess_album(..., prefer_deezer=True, prefer_spotify=False)` can build a Deezer `AlbumMatch` or fall back to iTunes.

Second, `tests/test_playlist_sync.py` still passes, proving that the provider swap did not break playlist persistence helpers.

Third, a human can run `python main.py --station neuralforge --dry-run` and observe that missing-song validation and album recheck proceed without Spotify 403 errors. Deezer quota warnings are still possible under heavy usage, but the sync path should continue to use Deezer, MusicBrainz, and iTunes rather than failing immediately because Spotify is unavailable.

## Idempotence and Recovery

The code edits are additive and safe to repeat. The new Deezer helper performs only read-only network calls. No station playlist CSVs or song files should be modified during unit tests. If the manual dry-run still surfaces a provider issue, rerun the same dry-run after the fix; there is no migration state to undo for this milestone.

## Artifacts and Notes

Relevant live-research evidence collected before implementation:

    Deezer search/track for Ghost - Rats returned the expected track plus album `Prequelle`.
    Deezer album detail for `Prequelle` exposed `record_type: "album"` and `release_date: "2018-06-01"`.
    Deezer searches for Neperia - Minokawa returned empty results, confirming the need to preserve MusicBrainz and iTunes fallback providers.

Validation evidence after implementation:

    pytest -q tests/test_deezer_sync_provider.py tests/test_playlist_sync.py
    ......                                                                   [100%]
    6 passed, 1 warning in 1.94s

Observed manual dry-run evidence:

    /home/nicou/.../NeuralForge/songs/Prog Metal/Evergrey - OXYGEN!.mp3
    No changes to .../Evergrey - OXYGEN!.mp3 are necessary

## Interfaces and Dependencies

The new shared helper module should export these functions from `src/neuralcast/services/deezer.py`:

    deezer_get(resource: str, *, params: Optional[dict[str, object]] = None) -> Optional[dict]
    search_tracks(query: str, *, limit: int = 10) -> list[dict]
    search_albums(query: str, *, limit: int = 10) -> list[dict]
    get_album(album_id: str) -> Optional[dict]
    get_track(track_id: str) -> Optional[dict]
    parse_release_date(date_str: str | None) -> Optional[datetime]

The album-lookup interface should continue to export:

    album_candidates(artist: str, title: str, *, prefer_spotify: bool = True, prefer_deezer: bool = False, limit: int = 50) -> List[AlbumMatch]
    guess_album(artist: str, title: str, *, prefer_spotify: bool = True, prefer_deezer: bool = False, min_confidence: float = 0.5, allow_fallback: bool = True) -> Optional[AlbumMatch]
    get_official_album_name(artist: str, title: str, *, prefer_spotify: bool = True, prefer_deezer: bool = False, min_confidence: float = 0.5, allow_fallback: bool = True) -> Optional[str]

Revision note (2026-03-11 / Codex): Created initial ExecPlan before editing the sync pipeline provider order.
Revision note (2026-03-11 / Codex): Updated the plan after implementation with the final provider order, added compatibility-wrapper reasoning, and recorded test plus dry-run evidence.
