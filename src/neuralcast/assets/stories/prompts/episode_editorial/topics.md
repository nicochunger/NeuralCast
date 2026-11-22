# Generate 10 diverse podcast episode ideas

Read context.json and artist_index.md in full. Suggest **10 distinct podcast
episode ideas** for the selected station in the configured host language
(Argentinian Spanish `es-AR` by default).

Rules:
- Use `editorial_definitions` (station spirit and taste) and `artist_index.md`
  (complete list of playlists and artists with track counts) so every suggested
  artist actually exists in the station's catalog with enough material.
- Spread the 10 ideas across **different playlists and subgenres** rather than
  clustering in the same two or three canonical bands.
- Check `previous_episodes` carefully (titles, premises, playlists, featured
  artists, and featured songs). Do NOT repeat a past episode's topic or central
  angle, and avoid reusing artists already featured in recent episodes unless
  framing a genuinely different story.
- For each idea, provide:
  - `title`: short, punchy episode title in natural language (at most 60
    characters, roughly 2–6 words). Keep only the core title with no subtitle or
    description, never include a colon (`:`), never use a two-part comma
    structure (`X, Y`), and do not include an episode number;
  - `premise`: the central human question and why a curious outsider would care;
  - `angle`: a concrete causal story arc (people, turning points, choices,
    consequences — never a generic band biography or trivia list);
  - `artists`: 3–6 artists from `artist_index.md` whose recordings can anchor
    the story;
  - `playlists`: the primary station playlist(s) involved.

Do not fabricate anecdotes or claim facts are already verified. No web search,
file edits, audio, or publication. Return Topics JSON only.
