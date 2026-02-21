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
- El ratio de bloques open debe quedar entre {open_ratio_min} y {open_ratio_max} del dia.
- Para bloques playlist, `playlist_id` debe existir en este catalogo.
- Entre 22:00 y 06:00 no puede haber programacion de playlists; cualquier bloque que toque esa ventana debe usar `mode: "open"`.
- Mantener exactamente los mismos limites de bloque de la plantilla semilla deterministica de abajo.

Plantilla diaria semilla deterministica (podes mejorar labels/eleccion de playlists, pero conservando horarios):
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
      "section_label": "...",
      "genre_labels": ["..."]
    }}
  ],
  "rationale": "Un parrafo corto en lenguaje llano."
}}
