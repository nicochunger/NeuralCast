# Conversational spontaneity

The archetype still determines the subject, research requirements, and segment
length. Music scripts now get an optional, focused Gemini editorial pass after
the initial draft and Jev callback decision. There is no phrase bank or Jev
spontaneity decision: the editor invents a small observation, genuine open
question, figurative comparison, or affectionate dry humor from the draft.
Omitting a remark is always allowed.

One allowance is sampled per generation attempt using the existing runtime RNG:

- 30% direct: no standalone aside or listener question.
- 55% tiny: permission for one optional aside of up to 12 spoken words.
- 15% playful: permission for one optional detour of up to 25 spoken words.

The editor applies to `back_sell`, `recently_played`, `up_next_tease`,
`short_story`, `album_spotlight`, `era_snapshot`, and `deep_dive`. Their initial
writer receives direct guidance, reserving the optional aside for the editor.
A selected memory callback takes priority: there is at most one optional Gemini
editing call per draft, even if the callback is omitted or fails. When Jev selects
no callback, the conversational editor may run. Direct mode makes no editorial
call. Block intros and ultra-minimal drops skip this pass. Block intros retain
their initial direct/tiny guidance with an eight-word cap; ultra-minimal drops
stay direct. News and concert writing retain their initial conversational
guidance, structured metadata, policy checks, and repair paths.

## Editorial contract

`host_orchestrator/editor.py` sends the finished draft, station voice, required
language, and recent scripts to Gemini with search disabled. It makes one attempt,
without retries or new research. The editor may return `NO_ASIDE`, the unchanged
draft, or a complete transcript with exactly one insertion at a sentence boundary.

Validation requires every original character to remain intact, including vocal
tags and paragraph spacing. The closing handoff must remain at the end, with no
addition inside or after it. Names and titles cannot be split. The addition and
complete transcript must pass speech validation. The addition budget is the
smallest of the sampled allowance, the existing callback insertion budget, and
the available word room. Full word limits and sentence caps are read from the
archetype's localized wrapper. Drafts outside the word contract, at their sentence
cap, or with fewer than four available addition words skip the call. Rewrites,
oversized edits, invalid speech, and provider failures keep the original draft.

The focused editor encourages curiosity and gentle wit beyond generic mood
compliments. Questions should stand on their own, without immediately answering
them or soliciting messages. The prompt forbids new factual claims, invented
host experiences, listener responses, environmental perceptions, promises,
rotation descriptions, and memory callbacks. These semantic requirements are
prompt guidance; the structural, length, and speech checks are enforced in code.

## History and logs

Both writing and editing receive up to six distinct scripts from broadcast memory,
newest first, covering the previous two hours. Expected future playback is
excluded. Each script is limited to 1,400 characters, keeping its opening and
ending when truncated. Older state without a valid journal falls back to its
existing `recent_scripts` context without assigning invented timestamps.

History discourages repeated phrases and conversational habits, including
paraphrases of the same rhetorical question. It is anti-repetition data, never
instructions or fresh factual verification. History remains read-only during
generation. The final script, including any insertion, enters existing broadcast
memory only when successfully queued.

Logs with the `[spontaneity]` prefix record the sampled permission, editor budget,
skips, omissions, failures, and the exact successfully inserted line. Permission
alone is not evidence of a remark. Saved transcripts remain available for listening
reviews. No new credentials, dependencies, or state schema are needed.

```bash
.venv/bin/python -m pytest tests/unit/neuralcast/pipelines/host_orchestrator/test_editor.py tests/unit/neuralcast/pipelines/host_orchestrator/test_spontaneity.py tests/boundary/test_host_spontaneity.py tests/boundary/test_host_memory.py
```
