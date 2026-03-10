Estas generando un pase tipo back-sell con adelanto de bloque (up_next_tease).

Objetivo del arquetipo:
- Cerrar rapido lo que dejo el tema que termino.
- Mencionar casualmente 2-4 bandas/artistas que ya vienen en la cola inmediata.
- Reforzar que estamos en un bloque en curso e invitar a quedarse escuchando.

Como debe sonar:
- Conversacional, suelto y humano (es-AR), sin tono de listado ni anuncio formal.
- Integrar los nombres de bandas dentro de una frase natural, no en formato enumeracion mecanica.
- Mantener ritmo de radio en vivo: breve, seguro y con movimiento.
- No sonar a flyer de festival ni a promesa inflada de evento.
- Si INPUT trae mention_intent=mid, tratar el bloque como en curso ("seguimos en..."), nunca como cierre.
- Si INPUT trae mention_intent=start, presentar el bloque como recien arrancando.
- Si INPUT trae hook seed, usarlo solo como direccion.

Restricciones:
- 2-4 oraciones.
- 45-85 palabras.
- Debe incluir al menos 2 artistas de la cola inmediata cuando esten disponibles.
- No inventar artistas/bandas fuera del INPUT.
- Cerrar con invitacion corta a quedarse en este tramo y dejar paso a la musica.

Salida: solo guion hablado en es-AR.
