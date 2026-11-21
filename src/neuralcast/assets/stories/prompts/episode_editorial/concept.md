Develop the owner's topic into a proposed episode brief, not a manuscript.
Read context.json and catalog.tsv in full (catalog.tsv lists every available
song in the station's playlists as `playlist\tartist\ttitle\talbum\tyear`).
Research original interviews and official sources online; open sources before
attaching precise claims. Explain the central question, people, choices and
connected narrative.

Select complete recordings ONLY from catalog.tsv, copying `playlist`, `artist`,
and `title` verbatim. Check `previous_episodes` and avoid reusing songs already
featured in published episodes. Give songs unique stable IDs, a track-specific
narrative purpose (not just fitting the era) and a deliberate order. Keep the
outline anchored on the featured artists whose songs are selected, rather than
building the climax around non-featured acts.

Consider the requested duration as an initial estimate and allow meaningful
narration; there is no fixed narration or episode duration limit. The
controller will verify the selected MP3s on disk and measure their durations.
Follow the supplied podcast guide, station identity, language and owner
direction. The outline must develop a story rather than list band biographies.
Instructions must require all selected songs in the listed order. Flag
recording-edition questions in the brief without claiming to have listened.

Return Concept JSON with title, premise, outline, instructions,
candidate_songs and initial_research. Do not approve the concept, write
narration, change files, download media, render, upload, delegate or publish.
