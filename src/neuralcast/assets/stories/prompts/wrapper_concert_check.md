You are generating a Concert Check Snippet.

Archetype goal:
- Verify whether the artist that just played or the artist coming next has scheduled concerts in Argentina or Switzerland.
- If at least one valid concert exists, give a compact host-style update and return to the music.

Input requirements:
- Research online before drafting by using Google Search grounded results.
- You must check both artists independently (current track artist and next track artist).
- Only accept concerts in: {concert_countries}.
- Only accept upcoming concerts (event date is today or later).
- Use reliable sources with concrete event details (date + city/country + artist), such as official artist tour pages, venue pages, or ticketing pages.
- If neither artist has a qualifying concert, output exactly NO_SCRIPT.

Rules:
- Do not invent events or missing details.
- Do not include concerts outside the target countries.
- Do not include concerts for artists other than the current/next track artists.
- Ignore angle for this archetype.

Conversational style cues:
- Start with a smooth transition from the song that just ended.
- Keep it practical and close to radio language, not like a listings database.
- Mention only 1-2 strongest events.
- Bridge back to the next track naturally.
- If INPUT includes recent scripts, avoid reusing their opening phrases or repeated 3-5 word chunks.

Deliver:
- 70-120 words total.
- Include date and city naturally in the spoken script.
- End by handoff to next track.

Output format when at least one valid event exists:
SCRIPT:
<spoken copy in es-AR>

META (JSON):
{{
  "language": "es-AR",
  "events": [
    {{
      "artist": "...",
      "country": "Argentina|Switzerland",
      "city": "...",
      "venue": "...",
      "event_date": "YYYY-MM-DD",
      "source_url": "https://..."
    }}
  ]
}}
