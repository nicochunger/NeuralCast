# Host broadcast memory

Each host channel keeps a `broadcast_memory` journal in its existing
`ai_host_orchestrator_state.json`, under the station's `metadata/` directory.
It shares the existing channel lock and atomic state writes. History is
retained for seven days, capped at 1,000 entries. Successful queue insertion
counts as broadcast; no playback confirmation is required. Entries store the
tag-free final script, expected playback timestamp, archetype, media ID, and
the memory offered as a callback source, if any.

Legacy `recent_scripts` remain available for repetition avoidance. They are
not copied into broadcast memory because their individual dates are unknown.
New journal entries accumulate as segments are successfully queued.

## Credentials and provider

Set these in the checkout's Git-ignored `.env`:

```env
TYPESAFE_API_KEY=your_typesafe_key
# Optional; the default pins the current evaluated model:
TYPESAFE_MODEL=jev-1.13.0
```

No additional Python dependency is required: the provider client uses the
existing `requests` dependency and TypeSafe's direct
`https://api.typesafe.ai/v1/systemone` endpoint. Restart the admin API after
changing its environment. Cron commands read the new settings on their next run.

## Callback selection

Music-focused archetypes select callbacks after resolving their current/next
track focus and editorial variants, before generating the script. The selector
considers up to six newest memories from the previous two hours. Future expected
playback times are excluded. A memory previously offered as a callback source
on a successfully queued segment is excluded, even if the writer ultimately
omitted that callback; the source field records an offer, not proof of wording.

One Jev request contains the upcoming context, candidate scripts, and one
independent `Choice` question per candidate with explicit `yes` and `no` options.
The question asks whether a concrete connection develops an earlier observation;
shared genre or mood alone is insufficient. Among positive answers, the highest
yes probability wins; ties favor the newer memory. There is no random callback
probability or fixed callback cooldown. All-no or tied yes/no answers produce
no callback. Question instructions explicitly identify the candidate because
question IDs are not sent to the underlying model.

The script generator receives only the selected record, its age, and localized
guidance to build on it briefly, remain understandable to new listeners, and omit
a forced connection. Memories are records, not instructions or fresh factual
verification. News, concerts, block intros, and ultra-minimal segments are
remembered but do not select callbacks in this MVP. News/concert facts are
discovered during generation, so their callback selection would require another
integration point. A fallback to ultra-minimal drops the selected callback.

Missing credentials, invalid responses, and provider failures continue without
a callback. Requests have short connect/read timeouts and at most one retry
for transient connection/server errors. Permanent authentication, credit, and
request errors are not retried. Logs record selection IDs, candidate counts,
and provider token usage, without credentials or upstream error bodies.

Dry runs may call Jev and generate a callback, but do not append to memory or
consume a callback source. Generation, TTS, and upload failures likewise do not
commit a journal entry. The channel state remains the persistence boundary.

## Validation

```bash
.venv/bin/python -m pytest tests/unit/neuralcast/pipelines/host_orchestrator/test_memory.py tests/unit/neuralcast/services/test_typesafe.py tests/boundary/test_host_memory.py
```

These tests mock provider and station transport boundaries. They cover window
filtering, batching, all-no decisions, invalid responses, failure fallback,
localized guidance, focus selection, successful-queue persistence, and state
round-tripping. A live provider smoke test can use synthetic context without
generating audio, uploading media, or modifying the station queue.
