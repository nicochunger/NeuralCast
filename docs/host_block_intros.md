# Scheduled block intros

The host prepares an intro up to ten minutes before a scheduled block starts.
Initial selection uses queue playback timestamps, choosing the first song at or
after the start; playlist membership is not used. The prepared script and audio
are persisted in the channel state and reused on subsequent cron runs.

Queue timestamps are estimates. Once prepared, the same target and predecessor
may retain the intro boundary if the target estimate shifts up to 60 seconds
before the scheduled start. This allowance applies only to a prepared target;
it does not select an earlier song when initially planning an intro. If an
earlier queued song moves after the start, it becomes the new target as before.

Publication requires the target to be next in the queue and its predecessor
to be currently playing, with at least 15 seconds remaining. Playback and queue
are rechecked after upload, before the request is submitted. Changed songs or
schedule context prevent publication of the old intro. The playback boundary
must reach the scheduled start, or its 60-second allowance when the prepared
target estimate has moved earlier.

If the prepared target has already started, the intro is skipped. The block's
schedule bookkeeping retains `intro_missed=true`, preventing subsequent cron
runs from moving that intro to song two. This does not record a spoken start
mention or create a broadcast-memory entry. Dry runs do not persist this flag.

Regression tests:

```bash
.venv/bin/python -m pytest tests/unit/neuralcast/pipelines/host_orchestrator/test_block_intros.py tests/boundary/test_block_intro_cron.py
```
