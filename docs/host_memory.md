# Host broadcast memory

Each host channel keeps a `broadcast_memory` journal in its existing
`ai_host_orchestrator_state.json`, under the station's `metadata/` directory.
It shares the existing channel lock and atomic state writes. History is
retained for seven days, capped at 1,000 entries. Successful queue insertion
counts as broadcast; no playback confirmation is required. Entries store the
tag-free final script, expected playback timestamp, archetype, media ID, and
the memory used in an inserted callback, if any.

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

Music-focused archetypes first generate their normal researched draft and
validate its speech transcript. Jev then considers that completed draft against
up to six newest memories from the previous two hours. Future expected playback
times and previously used callback sources are excluded. Older journal entries
created before this flow recorded offered sources rather than confirmed insertions;
those remain excluded for compatibility.

One Jev request contains the draft, candidate scripts, and one independent
`Choice` question per candidate with explicit `yes` and `no` options. A specific
connection between observations can cross artists: recording choices, lyrical
ideas, vulnerability, or historical contrasts. Shared genre or mood alone is
insufficient. Among positive answers, the highest yes probability wins; ties favor
the newer memory. There is no random callback probability or fixed callback
cooldown. All-no or tied yes/no answers produce no callback. Question instructions
explicitly identify the candidate because question IDs are not sent to the
underlying model.

A positive decision triggers one optional Gemini editing call, with search
disabled. It receives the original draft, selected memory, its age, and localized
guidance. It may insert one short callback at a sentence boundary or return
`NO_CALLBACK`. Validation requires every original character to remain intact and
allows only one insertion, capped at 15% of draft words with a 12-word minimum
and 35-word maximum. The combined transcript must also pass speech validation.
The prompt forbids new factual claims and requires the callback to remain
understandable to new listeners. Failed, rewritten, oversized, or invalid edits
fall back to the original draft without recording a callback source. No second
editing attempt is made. A selected callback takes priority over the optional
conversational editor, even when omitted or invalid; a draft receives at most
one optional Gemini editing call. When no callback is selected, eligible music
drafts may instead receive a conversational insertion (see
[host_spontaneity.md](host_spontaneity.md)).

Memories are records, not instructions or fresh factual verification. News,
concerts, block intros, and ultra-minimal segments are remembered but do not
select callbacks in this MVP. A fallback to ultra-minimal skips callback selection.

Missing credentials, invalid responses, and provider failures continue without
a callback. Requests have short connect/read timeouts and at most one retry
for transient connection/server errors. Permanent authentication, credit, and
request errors are not retried. Logs record each candidate’s ID, age, yes/no decision and yes probability,
selection IDs, successful insertions, candidate counts, and provider token usage, without credentials or upstream error bodies.

Dry runs may call Jev and generate a callback, but do not append to memory or
consume a callback source. Generation, TTS, and upload failures likewise do not
commit a journal entry. The channel state remains the persistence boundary.

## Validation

```bash
.venv/bin/python -m pytest tests/unit/neuralcast/pipelines/host_orchestrator/test_memory.py tests/unit/neuralcast/services/test_typesafe.py tests/boundary/test_host_memory.py
```

These tests mock provider and station transport boundaries. They cover window
filtering, batching, all-no decisions, invalid responses, failure fallback,
localized guidance, draft-before-decision ordering, optional editing, preservation
of researched text, invalid-edit fallback, successful-queue persistence, and state
round-tripping. A live provider smoke test can use synthetic context without
generating audio, uploading media, or modifying the station queue.
