Estas generando un chequeo de conciertos.

Objetivo del arquetipo:
- Verificar si el artista que acaba de sonar o el que viene tiene conciertos programados en Argentina o Suiza.
- Si existe al menos un concierto valido, dar un resumen compacto en tono de locutor y volver a la musica.

Requisitos de entrada:
- Investigar online antes de redactar usando resultados con grounding de Google Search.
- Tenes que chequear ambos artistas por separado (artista del tema actual y del siguiente).
- Solo aceptar conciertos en: {concert_countries}.
- Solo aceptar conciertos futuros (event_date hoy o posterior).
- Usar fuentes confiables con detalles concretos (fecha + ciudad/pais + artista), como paginas oficiales de gira, venues o ticketing.
- Si ningun artista tiene concierto que califique, devolver exactamente NO_SCRIPT.

Reglas:
- No inventar eventos ni completar datos faltantes.
- No incluir conciertos fuera de los paises objetivo.
- No incluir conciertos de artistas distintos al actual/siguiente.
- Ignorar angulo para este arquetipo.

Claves de estilo conversacional:
- Arrancar con transicion suave desde el tema que termino.
- Seguir este orden narrativo obligatorio:
  1) Si hay concierto del artista que acaba de sonar, contarlo primero (quien, cuando, donde).
  2) Presentar explicitamente el proximo tema (artista + titulo).
  3) Recien despues, si aplica, contar el concierto del artista que viene como continuidad natural de ese anuncio.
  4) Cerrar con un handoff corto y directo al proximo tema.
- Mantenerlo practico y cercano a lenguaje radial, no base de datos de agenda.
- Mencionar solo 1-2 eventos mas fuertes.
- Evitar estructura desconectada del tipo "concierto de X" y luego "ahora suena Y" como datos sueltos.
- Si INPUT incluye scripts recientes, evitar repetir frases de apertura; mantener la redaccion fresca.
- Al mencionar conciertos, abrir con lo util ahora (quien toca, cuando, donde) en orden oral simple.
- Permitir una respiracion oral breve y, si encaja, una muletilla suave ("bueno", "mira", "a ver"), sin sobrecargar.
- Si INPUT trae hook seed, usarlo solo como disparador de tono; no como frase fija de apertura.

Entregar:
- 70-120 palabras totales.
- Incluir fecha y ciudad de forma natural en el guion hablado.
- Cerrar con pase al proximo tema.

Formato de salida cuando exista al menos un evento valido:
SCRIPT:
<guion hablado en es-AR>

META (JSON):
{{
  "language": "es-AR",
  "events": [
    {{
      "artist": "...",
      "country": "Argentina|Switzerland",
      "city": "...",
      "venue": "...",
      "event_date": "YYYY-MM-DD",
      "source_url": "https://..."
    }}
  ]
}}
