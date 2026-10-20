# Évolutions possibles avec Home Assistant

La première tranche ne fournit ni connexion Home Assistant, ni identifiants,
service REST/MQTT, interface web ou commande d’équipement. Le `.ogp` reste la source
de référence des plantations, zones, associations explicites et interventions.

Les données potentiellement consultables sont les identifiants stables des zones et
plantations, libellés, dates partielles, états, appartenances aux zones et interventions
prévues ou réalisées. Le serveur MCP embarqué existant écoute uniquement sur la boucle
locale et nécessite une application ouverte. Ses lectures utilisent un instantané sur
le thread Qt. Les écritures passent par les mêmes validations et commandes que
l’interface, exigent l’activation des écritures et un jeton, puis restent en mémoire
jusqu’à l’enregistrement. Les outils de suivi sont `get_garden_record`,
`list_zone_plantings`, `search_garden_interventions`, `set_garden_card`,
`add_garden_intervention`, `edit_garden_intervention` et `delete_garden_intervention`.
Ils renvoient les identifiants réels. `persisted:false` ne prétend jamais qu’une
sauvegarde disque a eu lieu. Les clés de requêtes durables protègent les ajouts rejoués.

Un éventuel compagnon persistant pourrait exposer une interface REST ou MQTT
authentifiée sans imposer l’ouverture de l’application. Cela demanderait une décision
d’architecture distincte : propriété et verrouillage des fichiers, rédacteur unique,
et interdiction de réécrire indépendamment le plan pendant qu’OGP le modifie. Un premier
prototype en lecture seule pourrait consommer un export JSON produit volontairement.
L’exploitation et le déploiement d’un serveur constituent un chantier séparé.

De futurs liens avec des capteurs devraient référencer les UUID des zones et préciser
le lien entre identité du capteur, zone, type de mesure et unité. Les mesures brutes
horodatées resteraient séparées des interprétations et interventions manuelles. Une
mesure, recommandation ou demande d’automatisation ne prouve pas qu’un arrosage ou
un apport au sol a été effectué. Cette note n’autorise aucune commande d’irrigation.
