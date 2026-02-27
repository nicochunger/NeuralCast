# Composicion de Prompts del Host Orchestrator

Esta carpeta contiene las plantillas de prompts de Gemini usadas por:
- `src/neuralcast/pipelines/host_orchestrator.py`
- `src/neuralcast/pipelines/schedule_generator.py`
- `src/neuralcast/pipelines/story_injector.py` (shim legacy de compatibilidad)

## Archivos principales de prompts

- `host_constitution.md`: constitucion base del sistema (comportamiento de estacion y contrato de seguridad).
- `personality.md`: carta de personalidad del host compartida por todos los arquetipos.
- `script_style_baseline.md`: linea base global de escritura para guion hablado.
- `tts_instructions.md`: instrucciones compartidas de entrega TTS para todos los arquetipos.
- `wrapper_back_sell.md`: wrapper de arquetipo para `back_sell`.
- `wrapper_system_check.md`: wrapper legacy/deprecado (ya no lo usa el runtime actual).
- `wrapper_deep_dive.md`: wrapper de arquetipo para `deep_dive`.
- `wrapper_news.md`: wrapper de arquetipo para `news` (incluye placeholders `{story_count}`, `{news_topics}`, ventanas de antiguedad).
- `wrapper_concert_check.md`: wrapper de arquetipo para `concert_check` (incluye placeholder `{concert_countries}`).
- `wrapper_block_intro.md`: wrapper de arquetipo para `block_intro` (introduccion de inicio de bloque).
- `wrapper_ultra_minimal.md`: wrapper de arquetipo para pase minimo/fallback.

## Archivos de reparacion

- `repair_news_contract.md`: contrato estricto de reformateo para salida `news` malformada.
- `repair_concert_contract.md`: contrato estricto de reformateo para salida `concert_check` malformada.

## Como se arman los prompts

1. `build_system_prompt(...)` arma `system_instruction` de Gemini:
   - `host_constitution.md` (formateado con `{station_name}`)
   - `personality.md`
   - `script_style_baseline.md`
   - linea inline de personalidad de estacion (`personality.script_profile`)

2. `build_tts_instructions(...)` arma las instrucciones de comportamiento TTS:
   - `tts_instructions.md`
   - ajuste inline opcional de personalidad TTS de estacion (`personality.tts_profile`)

3. `build_prompt(...)` arma `contents` de Gemini (prompt usuario):
   - archivo wrapper de arquetipo (`wrapper_*.md`)
   - mas bloque `format_shared_input(...)` con contexto de ejecucion:
     - metadata de track actual/siguiente
     - angulo seleccionado
     - semilla de gancho
     - lista de aperturas/temas bloqueados
     - contexto de estacion/personalidad/tiempo

4. Para `news` y `concert_check`, los wrappers se renderizan con placeholders en runtime antes de concatenar.

5. `gemini_generate_text(...)` envia ambas piezas:
   - `system_instruction = build_system_prompt(...)`
   - `contents = build_prompt(...)`

## Modificadores y perillas de comportamiento

- Aleatoriedad por arquetipo: `sample_generation_settings(...)` controla rangos de temperature/top-p.
- Grounding de busqueda habilitado en `should_enable_search(...)` para:
  - `deep_dive`
  - `news`
  - `concert_check`
- Si el formato de salida de `news`/`concert_check` es invalido, se ejecuta pase de reparacion con plantilla `repair_*.md`.

## Notas de edicion

- Mantener placeholders exactamente como estan (ejemplo: `{station_name}`, `{story_count}`).
- En plantillas formateadas con Python `.format(...)`, las llaves literales de JSON deben quedar dobladas (`{{` y `}}`).
