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
- `wrapper_news.md`: wrapper para `news` (usa placeholders como `{story_count}`, `{news_topics}`, `{news_topic_ids}`).
- `wrapper_concert_check.md`: wrapper para `concert_check` (usa `{concert_countries}` y `{concert_country_codes}`).
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
   - si el canal configura `tts_instructions_override`, usa ese archivo como prompt
     completo y no agrega el ajuste de personalidad compartido

3. `build_prompt(...)` compone `contents` (prompt de usuario) con:
   - un wrapper `wrapper_*.md`
   - el bloque de contexto `format_shared_input(...)`

4. Para `news` y `concert_check`, se renderizan placeholders de wrapper antes de concatenar.

5. `gemini_generate_text(...)` envia:
   - `system_instruction = build_system_prompt(...)`
   - `contents = build_prompt(...)`

## Perillas de comportamiento

- `sample_generation_settings(...)` toma `temperature/top-p` del perfil de
  arquetipos efectivo para el canal.
- `should_enable_search(...)` toma `search_enabled` del mismo perfil.
- Si `news`/`concert_check` salen con formato invalido, se aplica pase de reparacion con `repair_*.md`.
- Temas de noticias, paises de conciertos y las demas perillas por arquetipo se
  resuelven desde `../archetype_profiles.json`. Los prompts muestran etiquetas
  localizadas, pero los contratos META usan IDs/codigos canonicos para poder
  validar el alcance de cada canal despues de generar.

## Alcance configurable de noticias y conciertos

- No escribir paises ni temas permitidos de forma literal en los wrappers. Usar
  `{news_topics}`, `{news_topic_ids}`, `{concert_countries}` y
  `{concert_country_codes}`.
- `news_topics` y `concert_countries` contienen etiquetas localizadas para que
  el modelo entienda el pedido; `news_topic_ids` y `concert_country_codes` son
  los valores estables que debe devolver en META.
- Cada historia de noticias debe devolver `topic_id` ademas del texto `topic`.
- Cada evento debe devolver `country_code` ademas del texto `country`.
- Los templates de reparacion deben conservar los mismos placeholders y
  contratos que sus wrappers. El validador rechaza hechos fuera del perfil del
  canal aunque el texto hablado parezca valido.

## Notas de edicion

- Mantener placeholders exactamente como estan (`{station_name}`, `{story_count}`, etc.).
- En templates con `.format(...)`, llaves literales de JSON deben ir dobladas (`{{` y `}}`).
- Al agregar un locale, traducir instrucciones y ejemplos, pero no traducir los
  IDs/codigos canonicos ni duplicar la politica de alcance dentro del prompt.
