# Select and rank the strongest episode ideas

Read context.json in full. It contains `candidates` (the generated list of
episode ideas, indexed 0..N-1), `previous_episodes`, `editorial_definitions`,
and any `owner_topic` direction.

Evaluate every candidate with fresh eyes against four criteria:
1. **Human drama & causal pull**: real turning points, choices, and
   consequences rather than a band biography or era overview.
2. **Outsider hook**: a curious listener outside the subgenre will care within
   the first minute.
3. **Song-as-protagonist potential**: the featured artists and angle naturally
   support 4–5 songs that carry the story forward rather than acting as filler.
4. **Freshness & genre rotation**: strong distance from `previous_episodes`
   (topic, featured artists, and recently covered playlists/genres).

Return TopicSelection JSON with up to **4 ranked picks** (best first, unique
`index` values referencing `candidates`). Index 0 of `ranked` is the #1
recommended episode to develop next. Give each pick a concise, concrete
`reason` explaining why it beat the other candidates.

No web search, file edits, audio, or publication. Return TopicSelection JSON
only.
