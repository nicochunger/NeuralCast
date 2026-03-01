# Composicion de Prompts del Host Orchestrator

Esta carpeta contiene plantillas de prompts usadas por:

- `src/neuralcast/pipelines/host_orchestrator/main.py`
- `src/neuralcast/pipelines/schedule_generator/main.py`

## Archivos principales

- `host_constitution.md`: constitucion base del sistema (con `{station_name}`).
- `personality.md`: carta de personalidad del host.
- `script_style_baseline.md`: linea base global de escritura para guion hablado.
- `tts_instructions.md`: instrucciones compartidas para entrega TTS.
- `wrapper_back_sell.md`: wrapper para `back_sell`.
- `wrapper_up_next_tease.md`: wrapper para `up_next_tease`.
- `wrapper_deep_dive.md`: wrapper para `deep_dive`.
- `wrapper_short_story.md`: wrapper para `short_story`.
- `wrapper_news.md`: wrapper para `news` (usa placeholders como `{story_count}`, `{news_topics}`).
- `wrapper_concert_check.md`: wrapper para `concert_check` (usa `{concert_countries}`).
- `wrapper_block_intro.md`: wrapper para `block_intro`.
- `wrapper_ultra_minimal.md`: wrapper para fallback ultra minimo.

## Archivos de reparacion

- `repair_news_contract.md`: contrato de reformateo para salida `news` malformada.
- `repair_concert_contract.md`: contrato de reformateo para salida `concert_check` malformada.

## Como se arma el prompt final

1. `build_system_prompt(...)` compone `system_instruction` con:
   - `host_constitution.md`
   - `personality.md`
   - `script_style_baseline.md`
   - ajuste inline de personalidad de estacion (`personality.script_profile`)

2. `build_tts_instructions(...)` compone instrucciones de TTS con:
   - `tts_instructions.md`
   - ajuste inline opcional de personalidad (`personality.tts_profile`)

3. `build_prompt(...)` compone `contents` (prompt de usuario) con:
   - un wrapper `wrapper_*.md`
   - el bloque de contexto `format_shared_input(...)`

4. Para `news` y `concert_check`, se renderizan placeholders de wrapper antes de concatenar.

5. `gemini_generate_text(...)` envia:
   - `system_instruction = build_system_prompt(...)`
   - `contents = build_prompt(...)`

## Perillas de comportamiento

- `sample_generation_settings(...)` ajusta `temperature/top-p` por arquetipo.
- `should_enable_search(...)` habilita grounding de busqueda para:
  - `deep_dive`
  - `short_story`
  - `news`
  - `concert_check`
- Si `news`/`concert_check` salen con formato invalido, se aplica pase de reparacion con `repair_*.md`.

## Notas de edicion

- Mantener placeholders exactamente como estan (`{station_name}`, `{story_count}`, etc.).
- En templates con `.format(...)`, llaves literales de JSON deben ir dobladas (`{{` y `}}`).
