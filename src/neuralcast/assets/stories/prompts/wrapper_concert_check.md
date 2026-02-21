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
- Mantenerlo practico y cercano a lenguaje radial, no base de datos de agenda.
- Mencionar solo 1-2 eventos mas fuertes.
- Volver naturalmente al tema siguiente.
- Si INPUT incluye scripts recientes, evitar repetir frases de apertura; mantener la redaccion fresca.
- Abrir con lo util ahora (quien toca, cuando, donde) en orden oral simple.

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
