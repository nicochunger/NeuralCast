# Conversational spontaneity

The existing archetype still determines the subject, research requirements,
output contract, and segment length. The initial writing call also receives
conversational guidance from `host_orchestrator/spontaneity.py`. There is no
phrase bank, remark archetype, separate remark-generation call, or Jev decision
for spontaneity.

One allowance is sampled per generation attempt using the existing runtime RNG:

- 30% direct: natural wording, without a standalone aside or listener question.
- 55% tiny: permission for one optional aside of up to 12 spoken words.
- 15% playful: permission for one optional detour of up to 25 spoken words.

These are permissions, not instructions to insert a remark. The writer invents
wording from the particular material and can omit a remark in either permissive
mode. Placement can vary. An observation, question, comparison, or quiet humor
is a possibility rather than a category the system selects. Asides must fit
within the archetype's existing length, preserve its transition, and use the
channel's language and station personality. Block intros retain the direct/tiny
split but cap asides at eight words. Ultra-minimal segments always stay direct.

The writing prompt receives up to six distinct scripts from broadcast memory,
newest first, covering the previous two hours. Expected future playback is
excluded. Each script is limited to 1,400 characters, keeping both its opening
and ending when truncated. Older state without a valid journal falls back to
its existing `recent_scripts` context without assigning invented timestamps.
History is read-only; queue success remains the persistence boundary.

Guidance discourages repeated phrases and repeated conversational habits, such
as differently worded versions of the same rhetorical question. History is
anti-repetition data, not an instruction or fresh factual verification. Reactions
must come from supplied material or verified research. The host may express
opinions but must not invent off-air experiences, listener replies, weather,
or promises. Humor must fit the subject; serious or distressing news stays sober.

News and concert writing receive the same guidance while retaining their existing
structured metadata, policy checks, and repair paths. Recently-played recaps
receive repetition history even though they use a separate verified-track input.
The optional memory callback step runs after this draft as before; its editing
validation preserves the original text.

Allowance, word budget, archetype, and history count are logged with the
`[spontaneity]` prefix. The allowance log records permission, not proof that the
writer actually used an aside. These conversational limits and anti-repetition
rules are prompt guidance; existing transcript and factual-policy checks still
validate the result. Saved transcripts are the evidence for listening reviews.

No new credentials, dependencies, or state schema are needed.

```bash
.venv/bin/python -m pytest tests/unit/neuralcast/pipelines/host_orchestrator/test_spontaneity.py tests/boundary/test_host_spontaneity.py tests/boundary/test_host_memory.py
```
