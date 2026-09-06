Reformate la sortie suivante pour qu'elle corresponde exactement à ce contrat. N'ajoute aucun fait. Si le contenu ne peut pas respecter le contrat, retourne exactement NO_SCRIPT.

Contrat :
- `topic_id` doit correspondre exactement à l'une de ces valeurs : {news_topic_ids}.
SCRIPT:
<texte parlé en fr-CH>

META (JSON):
{{
  "story_count": 1 or 2,
  "language": "fr-CH",
  "stories": [
    {{"topic_id":"...","topic":"...","headline":"...","source_url":"...","published_at":"ISO-8601"}}
  ]
}}

Sortie d'origine :
{original_output}
