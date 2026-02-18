# Story Injector Prompt Composition

This folder contains the Gemini prompt templates used by `src/neuralcast/pipelines/story_injector.py`.

## Main prompt files

- `host_constitution.md`: base system constitution (station-level behavior and safety contract).
- `script_style_baseline.md`: global writing style baseline for spoken script.
- `wrapper_back_sell.md`: archetype wrapper for `back_sell`.
- `wrapper_system_check.md`: archetype wrapper for `system_check`.
- `wrapper_deep_dive.md`: archetype wrapper for `deep_dive`.
- `wrapper_news.md`: archetype wrapper for `news` (has `{story_count}`, `{news_topics}`, age-window placeholders).
- `wrapper_concert_check.md`: archetype wrapper for `concert_check` (has `{concert_countries}` placeholder).
- `wrapper_ultra_minimal.md`: archetype wrapper for fallback/minimal handoff.

## Repair prompt files

- `repair_news_contract.md`: strict reformat contract for malformed `news` output.
- `repair_concert_contract.md`: strict reformat contract for malformed `concert_check` output.

## How prompts are assembled

1. `build_system_prompt(...)` builds Gemini `system_instruction`:
   - `host_constitution.md` (formatted with `{station_name}`)
   - `script_style_baseline.md`
   - inline station personality line (`personality.script_profile`)

2. `build_prompt(...)` builds Gemini `contents` (user prompt):
   - archetype wrapper file (`wrapper_*.md`)
   - plus `format_shared_input(...)` block with runtime context:
     - current/next track metadata
     - selected angle
     - hook seed
     - banned opener/topic list
     - station/personality/time context

3. For `news` and `concert_check`, wrappers are rendered with runtime placeholders before concatenation.

4. `gemini_generate_text(...)` sends both pieces:
   - `system_instruction = build_system_prompt(...)`
   - `contents = build_prompt(...)`

## Modifiers and behavior knobs

- Archetype-specific randomness: `sample_generation_settings(...)` controls temperature/top-p ranges.
- Search grounding enabled in `should_enable_search(...)` for:
  - `deep_dive`
  - `news`
  - `concert_check`
- If `news`/`concert_check` output format is invalid, repair pass uses `repair_*.md` template.

## Editing notes

- Keep placeholders exactly as-is (example: `{station_name}`, `{story_count}`).
- In templates that are formatted with Python `.format(...)`, literal JSON braces must remain doubled (`{{` and `}}`).
