Story Variation System Overview
===============================

This document summarizes how the story generator introduces controlled variety starting with the February 2025 update.

What changed
------------

* `story_variation.py` defines curated narrative and delivery variants plus preferred pairings that avoid odd combos. Each entry includes short instructions that still respect the Aspen-style tone while nudging the story toward a specific angle (anécdota cálida, dato curioso, postal sonora, etc.).
* `inject_story_snippet.py` computes a deterministic seed from the station slug, the selected song, and the following song. That seed tries to pick a curated narrative+delivery pairing first; if recency blocks everything, it falls back to independent draws. The same seed always produces the same selection under the same history.
* `src/neuralcast/assets/stories/story_prompt.md` and `src/neuralcast/assets/stories/tts_story_instructions.md` now contain placeholder tokens (`{{INTRO_STYLE}}`, `{{DELIVERY_VARIATION}}`, …) that the script fills with the chosen variant directions before calling OpenAI.
* A JSON history file (`src/neuralcast/assets/stories/style_history.json`) stores the most recent combinations per station so the selector can avoid repeating the exact same style in consecutive runs.

How selection works
-------------------

1. Seed generation concatenates the lowercased station, current song artist/title, and the next song artist/title, then hashes the string with SHA-256.
2. The selector first tries to draw from the curated pairings list with deterministic weighting, respecting the “avoid the last three uses” rule for both narrative and delivery. If nothing is available, it shrinks the avoid window and finally falls back to independent deterministic draws so it never blocks.
3. The template placeholders are replaced by the variant instructions. The resulting prompt stays very close to the original guidance while injecting the new color.

Managing history
----------------

* Location: `src/neuralcast/assets/stories/style_history.json`. Each station key stores up to 60 recent entries with the seed, narrative ID, delivery ID, and a timestamp.
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
      from story_variation import (
          compute_story_seed,
          select_variants_with_pairing,
          NARRATIVE_VARIANTS,
          DELIVERY_VARIANTS,
          PREFERRED_PAIRINGS,
      )

      seed = compute_story_seed('neuralcast', 'Soda Stereo', 'De Música Ligera', 'Fito Páez', 'Mariposa Tecknicolor')
      narrative, delivery, pair = select_variants_with_pairing(
          seed=seed,
          narrative_variants=NARRATIVE_VARIANTS,
          delivery_variants=DELIVERY_VARIANTS,
          pairings=PREFERRED_PAIRINGS,
          narrative_recent=[],
          delivery_recent=[],
          narrative_avoid_window=3,
          delivery_avoid_window=3,
      )
      print("Narrative:", narrative.style_id, narrative.description)
      print("Delivery:", delivery.style_id, delivery.description)
      print("Paired?" , bool(pair))
      PY

* Before publishing changes, run `python inject_story_snippet.py --station NeuralCast --dry-run` on a networked environment to confirm both the prompt and TTS instructions reflect the expected styles.
