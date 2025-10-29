Story Variation System for inject_story_snippet.py

This ExecPlan is a living document. Keep the sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` updated as work proceeds, following `.agent/PLANS.md`.

## Purpose / Big Picture

NeuralCast already injects Aspen-style stories after selected songs, but the stories and their delivery currently feel repetitive. After implementing this plan an operator can run `python inject_story_snippet.py --station NeuralCast --dry-run` and see that the generated story prompt and the TTS instructions vary in controlled, predictable ways that preserve the house style. Running the command again with the same seed produces the same combination, while different seeds rotate through alternative narrative cues and vocal deliveries. The system will also recall which combinations ran recently so it avoids repeating the same style twice in a row.

## Progress

- [x] (2025-02-14 11:08Z) Audited existing storytelling flow and identified deterministic seed inputs.
- [x] (2025-02-14 12:01Z) Introduced narrative and TTS variation palettes plus deterministic selector module.
- [x] (2025-02-14 12:24Z) Persisted recent usage history helpers and warnings for corrupt files.
- [x] (2025-02-14 12:58Z) Refactored templates and `inject_story_snippet.py` to apply deterministic variants and history tracking.
- [x] (2025-02-14 13:20Z) Documented variation system, captured CLI snippets, and outlined verification steps for a networked run.

## Surprises & Discoveries

Observation (2025-02-14 11:08Z): `generate_story_text` simply replaces placeholders in `stories/story_prompt.md` and sends the prompt to `openai_text_completion` without any seed control, so every variability lever must come from new prompt text. Confirmed by reading lines 116–142 of `inject_story_snippet.py`.
Observation (2025-02-14 13:12Z): The sandbox blocks network access, so `inject_story_snippet.py --dry-run` cannot hit the AzuraCast endpoints. Full validation must happen on a networked machine.

## Decision Log

Decision (2025-02-14 12:01Z): Created `story_variation.py` holding dataclasses plus curated variant lists so other modules can import a single source of truth. Returning the full variant object from `deterministic_variant_choice` avoids redundant lookups when applying template overrides.
Rationale: The template renderer needs access to multiple fields per variant, so returning only an ID would force another search. Keeping variants centralized ensures consistent house style guidance.

Decision (2025-02-14 12:58Z): Updated the Markdown templates with placeholder tokens and injected variant selections inside `inject_story_snippet.py`, saving style history only after successful queue injection and skipping persistence during `--dry-run`.
Rationale: Template placeholders let us vary phrasing without duplicating files, and deferring persistence ensures history reflects only actually scheduled stories while keeping dry-runs side-effect free.

Decision (2025-02-14 15:05Z): Added `stories/style_history.json` to `.gitignore` so runtime execution does not dirty the working tree.
Rationale: The history file is generated during normal runs and should not be tracked or committed.

## Outcomes & Retrospective

Validation steps executed inside the sandbox:

* `python -m py_compile inject_story_snippet.py story_variation.py` — confirmed the refactored modules compile.
* `python - <<'PY' ...` (see transcript in shell history) — demonstrated that the deterministic selector picks the expected narrative and delivery variants for a sample seed.

Dry-run against AzuraCast could not be exercised because network access is blocked (`Operation not permitted`). To validate end-to-end, rerun on a networked machine:

1. `python inject_story_snippet.py --station NeuralCast --dry-run`
2. Inspect the generated prompt and TTS instructions under `stories/snippets/<date>/` to confirm the selected variant phrases appear.
3. Review or reset `stories/style_history.json` as needed to observe rotation.

Documentation added in `stories/VARIATION_NOTES.md` explains the system, how to extend variants, and how to manage history.

## Context and Orientation

`inject_story_snippet.py` selects an upcoming song, renders `stories/story_prompt.md` into a complete prompt by replacing placeholders, calls `openai_text_completion` to obtain the story text, and then synthesizes audio via `openai_speech` using `stories/tts_story_instructions.md`. Both templates are static, so every run produces similar phrasing. The script already derives a `story_text` string before persisting files and sending them to AzuraCast.

Story-specific folders live under `stories/snippets/<station>/<YYYY-MM-DD>/`. No persistent state currently tracks which storytelling flavor was used. The code does not expose an explicit random seed, but it has deterministic inputs: the selected track’s `queue_id`, artist, and title, plus the station and the next track. We can hash those values to drive deterministic choices.

Templates are plain Markdown without Jinja-style placeholders. We will introduce lightweight token replacement to splice in variant snippets. The TTS instructions file is similarly static but can be expanded with placeholder tokens.

## Plan of Work

Describe the steps in order. Each milestone should leave the repository in a runnable state so progress is easy to resume. Reference concrete files and functions, and state the acceptance criteria for the milestone before moving on.

### Milestone 1 — Audit Existing Flow and Seed Inputs

1. Read `inject_story_snippet.py`, focusing on `generate_story_text`, `synthesize_story_audio`, and argument parsing to understand how the story text and instructions are consumed.
2. Identify which data points are always available when those functions execute (song artist/title, station slug, next song metadata). Document them in this ExecPlan under `Surprises & Discoveries` if any field is occasionally `None`.
3. Confirm how randomness currently behaves. If `openai_text_completion` is called without seed control, note that the prompt variation will be the main tool for change. Record observations in this plan.
4. Acceptance: clear inventory of the deterministic inputs that can act as a seed, noted either here or in code comments to be implemented later.

### Milestone 2 — Design Variation Palettes and Deterministic Selector

1. Create a new module `story_variation.py` at the repository root (sibling to `inject_story_snippet.py`). Define data structures for:
   - `NarrativeVariant`: describes intro tone, filler phrases, structural cues, closing outro style, and word choice hints.
   - `DeliveryVariant`: describes TTS instructions adjustments such as energy, pacing, pitch tweaks, and additional phrasing reminders.
2. Within `story_variation.py`, declare lists of curated variants (at least four narrative options and four delivery options). Keep all text in Spanish Rioplatense and consistent with Aspen warmth. Each entry should include:
   - `template_overrides`: brief phrases or bullet fragments to insert into the story prompt (e.g., alternate filler words, suggested angle like anecdote vs. curiosity).
   - `instruction_overrides`: sentences to append to the TTS instructions (e.g., “agregá un punto de entusiasmo al mencionar el artista”).
   - Optional metadata like `style_id` so the selector can track usage.
3. Implement `deterministic_variant_choice(seed_hash: str, available: Sequence[T], history: Sequence[str], avoid_window: int) -> T` using Python’s `random.Random` seeded with a hash of the seed string. The function should:
   - Shuffle deterministically, prefer the first variant not present in the latest `avoid_window` history, and fall back to the first element if all are exhausted.
   - Return both the selected variant and its identifier so callers can persist it.
4. Provide a helper `compute_story_seed(station: str, artist: str, title: str, next_artist: str, next_title: str) -> str` that normalizes inputs and hashes them (e.g., SHA256) to create a stable seed string.
5. Acceptance: the module can be imported and `deterministic_variant_choice` returns the same variant for identical seeds and different variants when the seed changes. Draft simple docstring doctests or comments demonstrating the behavior.

### Milestone 3 — Persist Style Usage History

1. Decide on a storage path `stories/style_history.json` (per station). Document format: map of station slug to list of recent selections, each entry containing `seed`, `narrative_id`, `delivery_id`, and timestamp.
2. Implement helper functions in `story_variation.py` (or a dedicated utility file if cleaner):
   - `load_style_history(path: pathlib.Path) -> Dict[str, List[StyleRecord]]`, returning an empty structure if the file is missing.
   - `update_style_history(history, station, seed, narrative_id, delivery_id, max_entries)` that appends a new record while keeping only the latest `max_entries`.
   - `save_style_history(path, history)` to write the JSON atomically (write to temp then replace).
3. Incorporate `avoid_window` logic by reading the recent records for the same station and extracting their IDs; pass these into `deterministic_variant_choice`.
4. Acceptance: running the helper functions in a REPL (documented here) would show history persisting to disk and trimming correctly.

### Milestone 4 — Refactor Templates and Inject Variants

1. Update `stories/story_prompt.md` to include placeholder tokens such as `{{INTRO_STYLE}}`, `{{BODY_STYLE}}`, `{{OUTRO_STYLE}}`, and `{{FILLER_WORDS}}`. Provide default sentences so the base style remains intact when no overrides are supplied. Include concise instructions on where to weave the placeholders.
2. Update `stories/tts_story_instructions.md` similarly with placeholders like `{{DELIVERY_VARIATION}}` and `{{PACE_ADJUSTMENT}}`.
3. Modify `inject_story_snippet.py`:
   - Import the new helpers from `story_variation.py`.
   - Before generating the story prompt, compute the seed, load history, pick narrative and delivery variants deterministically, and update the history file (skip during `--dry-run` if writing to disk is undesirable; explicitly state behavior in this plan).
   - Render the prompt template by replacing the placeholder tokens with the selected variant snippets; ensure fallback to defaults when a field is empty.
   - Render TTS instructions with delivery overrides (e.g., add sentences for excitement or calm pacing) while preserving the core text.
   - Pass the rendered instructions to `openai_speech`.
4. Ensure that the script writes the history update only after successful generation (both text and audio) to avoid polluting history on failures. Reuse existing utility functions for path handling.
5. Acceptance: running `python inject_story_snippet.py --station NeuralCast --dry-run` prints which variants were chosen (add log statement) and creates the story text/audio locally with varied prompts while maintaining friendly tone.

### Milestone 5 — Validation, Docs, and Handoff

1. Execute `python inject_story_snippet.py --station NeuralCast --dry-run` at least twice with different seeds (e.g., temporarily swap `selection_count` or inject known upcoming tracks). Capture the generated prompt snippets and TTS instruction excerpts to confirm variation and determinism (identical seed → identical output).
2. Update `readme.md` or create a new `stories/VARIATION_NOTES.md` explaining:
   - How the variation system works.
   - How to add new variants.
   - How to reset or inspect `stories/style_history.json`.
3. Document manual test steps in this ExecPlan under `Outcomes & Retrospective`, noting which commands were run and sample output.
4. Acceptance: documentation exists, dry-run artifacts show variation, and the history file reflects the recent selections without duplicating the last style.

## Recovery and Safe Re-runs

All new helpers should handle missing files gracefully. If the history file becomes corrupted, the loader must log a warning and reset to an empty structure; document this behavior. Regenerating a story with the same metadata will always produce the same variant, so rerunning the script is safe. Removing `stories/style_history.json` simply resets recent-tracking behavior.

## Interfaces and Data Structures

Define the following in `story_variation.py`:

    from dataclasses import dataclass
    from typing import Callable, Dict, Iterator, List, Optional, Sequence, Tuple
    import pathlib

    @dataclass(frozen=True)
    class NarrativeVariant:
        style_id: str
        description: str  # human-readable summary for logging
        intro_instruction: str
        body_instruction: str
        outro_instruction: str
        filler_words: str

    @dataclass(frozen=True)
    class DeliveryVariant:
        style_id: str
        description: str
        delivery_instruction: str
        pace_instruction: str
        additional_prompts: str

    def compute_story_seed(station: str, artist: str, title: str, next_artist: str, next_title: str) -> str:
        """Return a deterministic hash string that drives variant selection."""

    def deterministic_variant_choice(seed: str, variants: Sequence[T], recent_ids: Sequence[str], avoid_window: int, id_getter: Optional[Callable[[T], str]] = None) -> Tuple[int, T]:
        """Return the selected index and variant using a seeded PRNG while avoiding the most recent IDs."""

    def load_style_history(path: pathlib.Path) -> Dict[str, List[Dict[str, str]]]: ...

    def update_style_history(history: Dict[str, List[Dict[str, str]]], station: str, seed: str, narrative_id: str, delivery_id: str, max_entries: int) -> None: ...

    def save_style_history(path: pathlib.Path, history: Dict[str, List[Dict[str, str]]]) -> None: ...

    def iter_recent_ids(history: Dict[str, List[Dict[str, str]]], station: str, key: str) -> Iterator[str]: ...

Keep the module free of side effects so `inject_story_snippet.py` can import and orchestrate these utilities cleanly.

## Notes and Open Questions

If OpenAI responses remain too similar despite prompt variety, consider logging the selected instructions and actual completions for qualitative review. Future work could integrate additional metadata such as daypart or playlist category into the seed to widen variety.
