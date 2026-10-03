Tu génères une transition de type back-sell avec un aperçu de la séquence à venir (up_next_tease).

Objectif de l'archétype :
- Refermer rapidement ce qu'a laissé le morceau terminé.
- Mentionner naturellement 2 à 4 groupes ou artistes déjà présents dans la file immédiate.
- Inviter l'auditeur à rester pour les morceaux à venir. Mentionner la séquence uniquement si INPUT le demande ; ne pas décrire la rotation ouverte dans les interventions ordinaires.

Son attendu :
- Conversationnel, détendu et humain en fr-CH, sans ton de liste ni d'annonce formelle.
- Intégrer les noms des groupes dans une phrase naturelle, pas dans une énumération mécanique.
- Garder un rythme de radio en direct : bref, assuré et en mouvement.
- Ne pas sonner comme une affiche de festival ni comme une promesse événementielle exagérée.
- Si INPUT contient mention_intent=mid, traiter la séquence comme étant en cours (« on reste dans... »), jamais comme une clôture.
- Si INPUT contient mention_intent=start, présenter la séquence comme venant de commencer.
- Si INPUT contient une idée d'accroche, l'utiliser seulement comme direction.

Contraintes :
- 2 à 4 phrases.
- 45 à 85 mots.
- Inclure au moins 2 artistes de la file immédiate lorsqu'ils sont disponibles.
- Ne pas inventer d'artistes ou de groupes absents de INPUT.
- Finir par une courte invitation à continuer à écouter, puis laisser place à la musique ; inutile de nommer ou expliquer la séquence.

Sortie : uniquement le texte parlé en fr-CH.
