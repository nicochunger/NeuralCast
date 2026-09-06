Reformate la sortie suivante pour qu'elle corresponde exactement à ce contrat. N'ajoute aucun fait. S'il n'existe aucun concert valide, retourne exactement NO_SCRIPT.

Un concert valide signifie : artiste du morceau actuel OU artiste du prochain morceau, lieu dans {concert_countries}, et event_date aujourd'hui ou plus tard.

Contrat lorsqu'il existe des événements :
SCRIPT:
<texte parlé en fr-CH>

META (JSON):
{{
  "language": "fr-CH",
  "events": [
    {{"artist":"...","country_code":"{concert_country_codes}","country":"...","city":"...","venue":"...","event_date":"YYYY-MM-DD","source_url":"https://..."}}
  ]
}}

Sortie d'origine :
{original_output}
