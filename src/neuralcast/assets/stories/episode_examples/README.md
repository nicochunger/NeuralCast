# Episode voice examples

`library.json` contains calibration material for episode writing, review and
delivery. Each entry records a focused behavior, a passage or before/after pair,
an explanation, a review question and its provenance.

| Status | Meaning | Entries |
| --- | --- | --- |
| `owner_approved_example` | The owner explicitly approved the illustrated tone in the writing conversation. | 5 |
| `discussion_illustration` | An original example of a local phrasing principle, without a complete voice preference label. | 3 |
| `owner_requested_revision` | An exact excerpt from a documented owner-requested episode revision. Individual sentences have not been separately preference-rated. | 4 |
| `unrated_revision` | An exact saved delivery comparison for editorial assessment. | 2 |

Approved illustrations cover asides, a correction connected with “mejor dicho”,
a brief detour and return, tentative interpretation, and a small reaction.
Episode excerpts come from *Laberintos humanos* and *La voz que cambió todo*.
Their source fields identify the exact segment, snapshots and receipt. These
source files are operational data and are not committed with the example library;
the extracted passages are self-contained.

The owner-requested and unrated revisions are useful comparison material, not
automatic evidence that every “after” is better. Judge each in context. A null
`before` denotes a positive illustration, not a missing draft to invent.
Facts and musical observations in an example are not evidence for another episode.

Choose a small relevant set for a task instead of including the entire library
in every prompt. Reserve different owner-rated passages for evaluation: examples
shown during writing or review are not unseen test cases. Evaluate the ability to
preserve successful passages as well as the ability to improve weak ones.

The controller supplies three selected voice examples to writing, narrative and
voice review, and final editing. Factual verification receives evidence without
style examples. Only calibration fields and preference labels enter the snapshot;
operational source paths remain in this library. Selection is explicit in
`editorial.py` and extraction in `reviews.py`.
