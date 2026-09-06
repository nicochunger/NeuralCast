Reformatea la salida siguiente para que coincida exactamente con este contrato. No agregues hechos nuevos. Si no existe un concierto valido, devolve NO_SCRIPT exactamente.

Concierto valido significa: artista del tema actual O artista del proximo tema, ubicacion en {concert_countries}, y event_date hoy o posterior.

Contrato cuando hay eventos:
SCRIPT:
<guion hablado en es-AR>

META (JSON):
{{
  "language": "es-AR",
  "events": [
    {{"artist":"...","country_code":"{concert_country_codes}","country":"...","city":"...","venue":"...","event_date":"YYYY-MM-DD","source_url":"https://..."}}
  ]
}}

Salida original:
{original_output}
