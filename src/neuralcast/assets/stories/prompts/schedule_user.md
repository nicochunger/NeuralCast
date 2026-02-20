Build a fixed weekly schedule plan for this station.

Station context:
- slug: {station_slug}
- display_name: {station_name}
- timezone: {timezone}
- week_start: {week_start}
- week_end: {week_end}

Hard constraints:
- Produce a daily template that will be reused identically for all 7 days.
- Cover 24h exactly with no gaps or overlaps.
- Use block lengths between {min_block_minutes} and {max_block_minutes} minutes.
- Open-slot ratio must be between {open_ratio_min} and {open_ratio_max} of the day.
- For playlist blocks, playlist_id must exist in this catalog.

Playlist catalog:
{playlist_catalog}

Return JSON only using this shape:
{{
  "daily_template": [
    {{
      "start_time_local": "00:00",
      "end_time_local": "02:00",
      "mode": "playlist",
      "playlist_id": "...",
      "playlist_name": "...",
      "section_label": "...",
      "genre_labels": ["...", "..."]
    }},
    {{
      "start_time_local": "02:00",
      "end_time_local": "03:00",
      "mode": "open",
      "section_label": "...",
      "genre_labels": ["..."]
    }}
  ],
  "rationale": "One short paragraph in plain language."
}}
