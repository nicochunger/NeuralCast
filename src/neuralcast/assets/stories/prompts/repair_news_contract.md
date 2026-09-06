Reformatea la salida siguiente para que coincida exactamente con este contrato. No agregues hechos nuevos. Si el contenido no puede cumplir el contrato, devolve NO_SCRIPT exactamente.

Contrato:
- `topic_id` debe ser exactamente uno de: {news_topic_ids}.
SCRIPT:
<guion hablado en es-AR>

META (JSON):
{{
  "story_count": 1 or 2,
  "language": "es-AR",
  "stories": [
    {{"topic_id":"...","topic":"...","headline":"...","source_url":"...","published_at":"ISO-8601"}}
  ]
}}

Salida original:
{original_output}
