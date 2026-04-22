# Redesign Schedule Generator Open-Block Placement

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work proceeds.

This document must be maintained in accordance with `.agent/PLANS.md`.

## Purpose / Big Picture

After this change, the weekly schedule generator will place open blocks across the full day instead of clustering them near midnight, and those open blocks will be longer on average than the scheduled playlist blocks that fill the remaining time. A user will be able to run `python -m neuralcast.cli.schedule_generator --dry-run ...` and observe a daily template where open windows appear near evenly spaced anchor times across the day while the playlist blocks between them are shorter and more numerous.

## Progress

- [x] (2026-04-22 16:05Z) Investigated the active schedule generator and confirmed that `_choose_open_indices_anywhere(...)` in `src/neuralcast/pipelines/schedule_generator/generation.py` causes early-day alternating open blocks because it rewards transition count and does not score temporal spread.
- [x] (2026-04-22 16:10Z) Chose the redesign approach: place open windows first by time, make them longer on average, then partition the remaining gaps into shorter playlist blocks.
- [x] (2026-04-22 16:29Z) Implemented the new open-first scaffold builder in `src/neuralcast/pipelines/schedule_generator/generation.py`, including layout selection, bounded exact partitions, uniformly distributed playlist gaps, and shorter playlist partitions within each gap.
- [x] (2026-04-22 16:30Z) Added scaffold-level tests in `tests/test_schedule_generator.py` to prove open-block spread and average duration behavior.
- [x] (2026-04-22 16:31Z) Ran scheduler validation commands and recorded the outcomes below.
- [x] (2026-04-22 16:35Z) Ran the mandatory VPS redeploy script for schedule generator runtime changes.

## Surprises & Discoveries

- Observation: The current production scheduler does not use the older deterministic quiet-hours template builder; it uses `build_weekly_plan_with_code(...)` and `_build_randomized_scaffold(...)`.
  Evidence: `src/neuralcast/pipelines/schedule_generator/main.py` calls `build_weekly_plan_with_code(...)`, and `build_deterministic_daily_template(...)` is only referenced from tests.

- Observation: The current open-slot optimizer prefers maximum open/non-open transitions and breaks ties in favor of lower-index masks, which creates early alternating prefixes such as `POPOPOPOPOPPPPPPPP`.
  Evidence: local probe of `_build_randomized_scaffold(...)` with current defaults across multiple seeds produced repeated open index sets such as `[1, 3, 5, 7, 9]`.

- Observation: Simply preferring the midpoint of the open-ratio range still produced open blocks that were only slightly longer than playlist blocks; the layout chooser needed to optimize the duration gap explicitly.
  Evidence: the first post-refactor scaffold probe produced `open_avg 86.2` versus `playlist_avg 84.08`, which passed the letter of the requirement but not the intended differentiation.

- Observation: With default bounds (`30-90` minutes), a stronger separation emerges when the layout chooser prefers six open blocks and the maximum feasible playlist-block count, because that keeps open blocks near `~90` minutes while forcing playlist averages closer to `~75-78`.
  Evidence: the final scaffold probe produced `open_avg 85.5` and `playlist_avg 77.25` with the mode pattern `PPOPOPPOPOPPOPPOPP`.

## Decision Log

- Decision: Replace the existing “generic blocks first, mark some indices as open later” strategy with an “open blocks first, playlist gaps second” strategy.
  Rationale: Uniformly spreading open windows and making them longer on average are hard constraints that are awkward to express as soft scoring on a prebuilt block list. They are straightforward when open windows are placed directly in time.
  Date/Author: 2026-04-22 / Codex

- Decision: Preserve the existing downstream playlist assignment path in `_assign_playlists_to_scaffold(...)` and only change the scaffold generation layer.
  Rationale: The user requested placement and duration behavior changes for open blocks, not a rewrite of playlist weighting, combo presets, or weekly seeding.
  Date/Author: 2026-04-22 / Codex

- Decision: Build the day from alternating playlist gaps and open windows, with exactly one open window between each pair of playlist gaps.
  Rationale: This makes consecutive open windows impossible by construction and makes day-wide spread a property of the layout itself rather than a soft scoring preference.
  Date/Author: 2026-04-22 / Codex

- Decision: Choose layout candidates by preferring the largest difference between average open duration and average playlist duration, then use open-ratio midpoint distance only as a secondary tiebreaker.
  Rationale: The primary new user requirement is visible differentiation between open and playlist block lengths; ratio midpoint closeness should not override that.
  Date/Author: 2026-04-22 / Codex

## Outcomes & Retrospective

The schedule generator now places open windows by constructing the day as playlist gaps separated by open windows, rather than by marking some indices in a generic block list as open. The resulting schedules spread open blocks through the whole day and keep them longer on average than playlist blocks under the default `30-90` minute constraints. The downstream playlist assignment logic, validation, weekly expansion, and deploy workflow all remained intact.

## Context and Orientation

The active schedule generator lives in `src/neuralcast/pipelines/schedule_generator/`. `main.py` parses CLI arguments and calls `build_weekly_plan_with_code(...)` from `generation.py`. That function builds a raw day template, assigns playlists to every non-open block, validates the template, expands it across the week, and returns `WeeklySchedulePlan` from `models.py`.

In the current implementation, `_build_randomized_scaffold(...)` creates one full-day list of block durations and then `_choose_open_indices_anywhere(...)` selects which indices should be open. Because that chooser scores only total open minutes, total open-slot count, and number of state transitions, it routinely creates schedules that alternate open and playlist blocks early in the day and then use only playlist blocks later. The redesign will change only this scaffold-building stage.

For this plan, “open block” means a daily template block with `mode="open"` that is applied to all enabled playlists so AzuraCast can do weighted random rotation during that window. “Playlist block” means a block with `mode="playlist"` that is assigned to one playlist or curated combo. “Uniformly spread” means open blocks should appear near evenly spaced anchor positions across the day rather than all near the beginning or end.

## Plan of Work

Edit `src/neuralcast/pipelines/schedule_generator/generation.py` to replace `_choose_open_indices_anywhere(...)` and the current `_build_randomized_scaffold(...)` logic with a new scaffold pipeline.

First, add helpers that choose an open-block count and target open minutes from the configured bounds. Then compute evenly spaced open-block anchor centers across the day and assign one open window to each anchor with small deterministic jitter from the seeded random generator. Open-window durations must be sampled from a longer range than playlist-window durations so the resulting average open duration is higher.

Next, build the playlist portions by looking at the gaps before, between, and after open windows. Partition each gap into durations using a shorter-duration target than the open windows, while still respecting the existing `min_block_minutes` and `max_block_minutes` bounds. Convert the resulting ordered windows into `raw_blocks` covering `00:00` to `24:00` without overlaps or gaps.

Keep `_assign_playlists_to_scaffold(...)` unchanged apart from consuming the new raw scaffold. The downstream validation, weekly expansion, and plan hashing must remain intact.

Finally, update `tests/test_schedule_generator.py` with direct scaffold-level or plan-level assertions that prove two behaviors: open blocks are distributed across the whole day, and their average duration exceeds the average duration of playlist blocks. The tests should fail against the old scaffold logic and pass after the change.

## Concrete Steps

From repository root (`/home/nicou/Dropbox/Documents/Projects_and_Coding/Media_and_Content/NeuralCast`):

1. Edit `.agent/schedule_generator_open_distribution_execplan.md` to keep this plan current while working.
2. Edit `src/neuralcast/pipelines/schedule_generator/generation.py` to add the new open-first scaffold builder.
3. Edit `tests/test_schedule_generator.py` to add deterministic assertions for spread and duration bias.
4. Run:
   - `PYTHONPATH=src python tests/test_schedule_generator.py`
   - `PYTHONPATH=src python - <<'PY' ... scaffold inspection snippet ... PY`
5. Run the mandatory deploy command from repository root:
   - `./deployment/redeploy_host_orchestrator_rsync.sh`

Expected post-change inspection output should show open windows across the day rather than as an early alternating prefix. A representative example is:

    open blocks:
    01:30-03:30
    06:30-08:30
    11:30-13:30
    16:30-18:30
    21:00-23:00

The exact times may vary with the weekly seed, but the windows should occupy multiple day segments instead of all appearing before midday.

## Validation and Acceptance

Acceptance requires all of the following:

- `PYTHONPATH=src python tests/test_schedule_generator.py` passes.
- A direct scaffold or plan inspection shows open blocks occurring throughout the day, not only at the front.
- The average open-block duration is greater than the average playlist-block duration for the inspected deterministic sample.
- The mandatory VPS redeploy script completes successfully, or clearly reports an external connectivity failure.

The new tests should assert observable behavior, not implementation details. In particular, they should prove that open-block centers cover at least early, middle, and late portions of the day and that average open duration exceeds average playlist duration.

## Idempotence and Recovery

This change is safe to rerun because the generator remains seeded and deterministic for the same inputs. If a new scaffold helper fails validation for certain bounds, the code should raise a `ScheduleValidationError` rather than silently creating an invalid partial day. Tests can be rerun repeatedly without side effects. The VPS deploy script uses rsync and is safe to rerun; if it fails due to SSH or rsync availability, fix the external issue and rerun the same command.

## Artifacts and Notes

Implementation evidence will be recorded here after code and validation complete.

Validation results:

    $ PYTHONPATH=src python tests/test_schedule_generator.py
    ...............
    ----------------------------------------------------------------------
    Ran 15 tests in 0.030s
    OK

    $ PYTHONPATH=src python - <<'PY'
    ... inspect _build_randomized_scaffold(...)
    ...
    modes PPOPOPPOPOPPOPPOPP
    open_avg 85.5 playlist_avg 77.25
    00:00 01:17 playlist 77
    01:17 02:17 playlist 60
    02:17 03:29 open 72
    03:29 04:59 playlist 90
    04:59 06:29 open 90
    ...
    19:30 21:00 open 90
    21:00 22:30 playlist 90
    22:30 24:00 playlist 90

    $ ./deployment/redeploy_host_orchestrator_rsync.sh
    [deploy] Syncing src/ (with delete + excludes; excluded files are preserved on VPS)...
    [deploy] Syncing vps_requirements.txt...
    [deploy] Syncing deployment/...
    [verify] Key deployed entrypoints:
    ...
    /root/radio_host_orchestrator/src/neuralcast/pipelines/schedule_generator/main.py
    ...
    [deploy] Done.

## Interfaces and Dependencies

Keep `build_weekly_plan_with_code(...)` as the public schedule-construction entrypoint. The new scaffold helpers should stay in `src/neuralcast/pipelines/schedule_generator/generation.py` and operate on plain Python lists and dictionaries so they can feed the existing `_assign_playlists_to_scaffold(...)` function.

No new third-party dependencies are required. Reuse existing helpers from `template.py` such as `format_hhmm(...)`, `validate_daily_template(...)`, and the duration partition fallback where helpful.

Revision note (2026-04-22 / Codex): Created ExecPlan for the open-block redistribution redesign after investigating the current alternation-heavy scaffold behavior.
Revision note (2026-04-22 / Codex): Updated the plan after implementation, test validation, and VPS redeploy to capture the new open-first scaffold strategy and its observed behavior.
