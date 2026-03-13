# Repo Cleanup Inventory (2026-02-22)

This file records the initial cleanup classification so future tidy-ups can continue from a known baseline.

## Kept at Repo Root (Active)

- `readme.md` (primary overview)
- `AGENTS.md` (repository workflow/rules)
- `GEMINI.md` (assistant-specific summary)
- Root CLI compatibility shims: `main.py`, `update_new_releases.py`, `inject_host_segment.py`, `schedule_generator.py`
- Station data directories: `NeuralCast/`, `NeuralForge/`

## Quarantined to `docs/archive/` (Historical Reference)

- `docs/archive/host_orchestrator/ai_host_orchestrator.md`
- `docs/archive/host_orchestrator/story_injector_prompt_archive.md`
- `docs/archive/host_orchestrator/tts_injection.md`
- `docs/archive/skills/simplify-code.skill`

These files are preserved for history/reference but are not considered current operational docs.

## Quarantined Binary Backup (`archive/`)

- No in-repo binary backup retained.

## Runtime-Generated Artifacts (Do Not Track)

- `deployment/deploy_host_orchestrator.zip`
- `logs/*.log`
- `*/duplicate_analysis.log`
- `*/metadata/ai_host_orchestrator_state.json`
- `*/metadata/ai_schedule_state.json`
- `src/neuralcast/assets/stories/snippets/` generated snippet outputs

## Intentionally Tracked Runtime-Adjacent Data

- `src/neuralcast/assets/stories/style_history.json` (used to avoid repeated styles across runs)

## Follow-Up Candidates (Not addressed in this pass)

- Larger internal module decomposition (`src/neuralcast/pipelines/*.py`)
- Further docs consolidation into `docs/` if root docs become noisy again
