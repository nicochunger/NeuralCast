Reformat the following output so it exactly matches this contract. Do not add new facts. If no valid concert exists, output NO_SCRIPT exactly.

Valid concert means: current track artist OR next track artist, location in Argentina/Switzerland, and event_date today or later.

Contract when events exist:
SCRIPT:
<spoken copy in es-AR>

META (JSON):
{{
  "language": "es-AR",
  "events": [
    {{"artist":"...","country":"Argentina|Switzerland","city":"...","venue":"...","event_date":"YYYY-MM-DD","source_url":"https://..."}}
  ]
}}

Original output:
{original_output}
