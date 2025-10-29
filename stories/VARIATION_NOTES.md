Story Variation System Overview
===============================

This document summarizes how the story generator introduces controlled variety starting with the February 2025 update.

What changed
------------

* `story_variation.py` defines curated narrative and delivery variants. Each entry includes short instructions that still respect the Aspen-style tone while nudging the story toward a slightly different angle (anécdota cálida, dato curioso, paseo por la ciudad, etc.).
* `inject_story_snippet.py` computes a deterministic seed from the station slug, the selected song, and the following song. That seed selects both a narrative variant (for the text prompt) and a delivery variant (for TTS instructions). The same seed always produces the same pair.
* `stories/story_prompt.md` and `stories/tts_story_instructions.md` now contain placeholder tokens (`{{INTRO_STYLE}}`, `{{DELIVERY_VARIATION}}`, …) that the script fills with the chosen variant directions before calling OpenAI.
* A JSON history file (`stories/style_history.json`) stores the most recent combinations per station so the selector can avoid repeating the exact same style in consecutive runs.

How selection works
-------------------

1. Seed generation concatenates the lowercased station, current song artist/title, and the next song artist/title, then hashes the string with SHA-256.
2. Two deterministic draws run off that seed (one for narrative, one for delivery). The helper shuffles the candidate list with a seeded RNG, then picks the first entry that has not been used in the last three stories for that station. If all variants were used recently it falls back to the deterministic top pick, ensuring the output is still predictable.
3. The template placeholders are replaced by the variant instructions. The resulting prompt stays very close to the original guidance while injecting the new color.

Managing history
----------------

* Location: `stories/style_history.json`. Each station key stores up to 60 recent entries with the seed, narrative ID, delivery ID, and a timestamp.
* Dry-run behavior: running `python inject_story_snippet.py --dry-run` logs which variants it would use but **does not** write to the history file. History updates happen only after a story is successfully queued on AzuraCast.
* Reset: delete the JSON file to clear history. The loader will recreate it on the next non-dry-run execution. If the file becomes corrupted, the script emits a warning and starts fresh automatically.

Adding or adjusting variants
----------------------------

* Edit `story_variation.py` and adjust the `NARRATIVE_VARIANTS` or `DELIVERY_VARIANTS` tuples. Keep the instructions concise, in Rioplatense Spanish, and aligned with the Aspen warmth.
* Each variant must have a unique `style_id`. Use lowercase with hyphens (`"curious-fact"`) so history entries stay readable.
* When introducing new placeholders in the templates, ensure `generate_story_text` or `synthesize_story_audio` is updated to replace them.

Testing tips
------------

* Use the deterministic helpers in a Python shell to preview selections without calling the OpenAI APIs:

      python - <<'PY'
      from story_variation import compute_story_seed, deterministic_variant_choice, NARRATIVE_VARIANTS, DELIVERY_VARIANTS
      seed = compute_story_seed('neuralcast', 'Soda Stereo', 'De Música Ligera', 'Fito Páez', 'Mariposa Tecknicolor')
      print(deterministic_variant_choice(f"{seed}|narrative", NARRATIVE_VARIANTS, [], 3)[1])
      print(deterministic_variant_choice(f"{seed}|delivery", DELIVERY_VARIANTS, [], 3)[1])
      PY

* Before publishing changes, run `python inject_story_snippet.py --station NeuralCast --dry-run` on a networked environment to confirm both the prompt and TTS instructions reflect the expected styles.
