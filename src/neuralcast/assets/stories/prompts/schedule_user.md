Armá un plan de programacion semanal fija para esta estacion.

Contexto de estacion:
- slug: {station_slug}
- display_name: {station_name}
- timezone: {timezone}
- week_start: {week_start}
- week_end: {week_end}

Restricciones duras:
- Generar una plantilla diaria que se reutilice identica los 7 dias.
- Cubrir 24h exactas sin huecos ni solapamientos.
- Usar bloques de entre {min_block_minutes} y {max_block_minutes} minutos.
- Usar duraciones variables de bloque (no repetir 3 horas en casi toda la grilla).
- El ratio de bloques open debe quedar entre {open_ratio_min} y {open_ratio_max} del dia.
- Para bloques playlist, cada `playlist_id` en bloques simples o cada elemento de `playlist_ids` en bloques combinados debe existir en este catalogo.
- Se permiten bloques playlist con mas de una playlist usando `playlist_ids` + `playlist_names` cuando la combinacion tenga sentido.
- Se permiten bloques open durante el dia si ayudan al flujo.
- `section_label` debe estar en español (evitar nombres en ingles).
- Entre 22:00 y 06:00 no puede haber programacion de playlists; cualquier bloque que toque esa ventana debe usar `mode: "open"`.

Plantilla diaria semilla deterministica (referencia / punto de partida; podes cambiar horarios, duraciones, labels y elecciones):
{deterministic_seed_template}

Catalogo de playlists:
{playlist_catalog}

Devolve solo JSON con esta forma:
{{
  "daily_template": [
    {{
      "start_time_local": "00:00",
      "end_time_local": "02:00",
      "mode": "playlist",
      "playlist_id": "...",
      "playlist_name": "...",
      "section_label": "...",
      "genre_labels": ["...", "..."]
    }},
    {{
      "start_time_local": "02:00",
      "end_time_local": "03:00",
      "mode": "open",
      "section_label": "Nombre en español",
      "genre_labels": ["..."]
    }},
    {{
      "start_time_local": "10:00",
      "end_time_local": "11:30",
      "mode": "playlist",
      "playlist_ids": ["...", "..."],
      "playlist_names": ["...", "..."],
      "section_label": "Bloque combinado en español",
      "genre_labels": ["...", "..."]
    }}
  ],
  "rationale": "Un parrafo corto en lenguaje llano."
}}
