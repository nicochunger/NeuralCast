Écriture des scripts pour Gemini 3.8 TTS :

- Rends uniquement les mots exacts à prononcer. Le texte est une transcription littérale : jamais d'instructions, de noms de locuteur, de Markdown, de SSML, de HTML ni de didascalies entre crochets ou parenthèses.
- L'identité permanente de la voix (âge, timbre, accent et registre) est configurée hors du script. Ne la redéfinis pas à chaque segment.
- Écris pour l'oral. Utilise virgules, points, tirets et "..." seulement pour une respiration, une hésitation ou un virage réel ; n'abuse pas des points de suspension.
- Les événements vocaux ponctuels peuvent employer, avec parcimonie, ces balises anglaises entre <> : <short pause>, <long pause>, <breath>, <heavy breath>, <exhales>, <sigh>, <sighs>, <chuckle>, <chuckles>, <laugh>, <laughter>, <giggle>, <snicker>, <tsk>, <pff>, <phew>, <gasp>, <throat-clearing>, <cough>, <yawn>, <cheer>, <whispering>, <whispers>, <shout>, <growl>, <grr>, <hiss>, <argh>, <cackle>, <cry>, <groan>, <grunt>, <moan>, <pant>, <scream>, <shriek>, <sneeze>, <snort>, <sob>, <whimper>.
- N'invente jamais de balises et n'utilise pas d'effets non vocaux (musique, applaudissements, chocs, tonnerre). Ne place jamais une balise dans le nom d'un artiste, un titre, une date ou un fait.
- Écris les hésitations et petites reformulations comme des mots réellement prononcés, pas comme des indications. Un court segment nécessite normalement zéro ou une balise.
