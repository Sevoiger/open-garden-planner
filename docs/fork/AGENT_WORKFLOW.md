# Codex GPT-6.1 Sol — méthode du fork personnel

Politique du propriétaire, 20 octobre 2026. `AGENTS.md` porte les consignes actives.
Ce fork privilégie les besoins personnels, les extensions localisées et l’exécution
native macOS. Les procédures de release de l’amont ne sont pas son mode de livraison.

## Ce que l’agent doit faire

Partir du résultat demandé, suivre le chemin réel dans le code et choisir la preuve
qui peut détecter un défaut. Décider des détails réversibles sans demander un accord
à chaque étape. Une vérification est utile si son échec changerait la conclusion.
Quand le parcours fonctionne et que les risques identifiés sont couverts, livrer.

Exemples de calibrage :

| Demande | Travail et preuve utiles | Point d’arrêt |
| --- | --- | --- |
| Corriger un panneau tronqué | Reproduire à la taille concernée, corriger le layout, vérifier le rendu et les tests UI affectés | Contenu accessible, aucune régression du parcours |
| Ajouter une propriété au catalogue | Étendre le modèle/service existant, vérifier une entrée ancienne et nouvelle ; save/reopen si persistée | Valeur correcte et compatibilité démontrée |
| Corriger undo sur une opération partagée | Reproduire l’opération, undo/redo, intégrations des appelants affectés et revue indépendante du diff | Régression corrigée, risques partagés couverts |
| Modifier les consignes | Lire les consommateurs, vérifier les formats/liens et les tests des scripts modifiés | Contexte chargeable, aucune suite applicative nécessaire |

Un test unitaire passant ne démontre pas la livraison d’un clic Qt ; un mock de
transport ne démontre pas un échange MCP ; une image offscreen ne démontre pas le
rendu natif. Choisir la frontière réellement affectée. Les échecs doivent être analysés,
pas masqués en assouplissant les assertions. Les fixtures et processus enfants doivent
rester isolés du stockage personnel.

`pytest-macos.ini` est un petit socle existant, facultatif, non un certificat global.
Une suite complète est pertinente pour une modification transversale ou un audit,
pas à chaque correction. Ne pas répéter une suite verte sans changement pertinent.
La revue indépendante est réservée aux risques définis dans AGENTS.md ; elle apporte
un regard technique, sans imposer une seconde campagne identique de tests.

## Skills : installation et choix

Les skills du projet sont installés dans `.agents/skills/`, versionnés et découverts
localement par Codex. Aucun téléchargement tiers nécessaire pour ce travail. Ne pas
les recopier dans le dossier utilisateur : cela créerait des doublons. Si la session
conserve l’ancien catalogue, redémarrer Codex pour recharger les instructions.

| Besoin | Skill |
| --- | --- |
| Ajouter une extension sans grossir le fork | `ogp-extension-design` (nouveau) |
| Comprendre les contrats persistants, commandes, frontières | `ogp-architecture-contract` |
| Déterminer la preuve technique utile | `ogp-validation-and-qa` |
| Réparer un environnement macOS | `ogp-build-and-run` |
| Diagnostiquer une cause encore inconnue | `debug-verbose` |
| Retrouver un incident ou une contrainte Qt connue | `ogp-debugging-playbook`, `ogp-failure-archaeology`, `ogp-qt-cad-reference` |
| Finaliser une demande ou un lot | `finalize-us`, `deliver-package` |
| Examiner une PR existante | `analyze-pr` |
| 3D, assets, mesures, domaine botanique | Skills spécialisés existants, uniquement pour le sujet concerné |

`openai-docs` et `skill-creator`, déjà fournis dans cette installation de Codex,
servent respectivement à vérifier la documentation OpenAI et à maintenir des skills.
Pas d’agent généraliste supplémentaire, plugin web ou skill d’audit à installer
pour développer cette application PyQt6.

Les cinq longues entrées d’investigation/recherche ont une entrée courte et un
fichier `references/technical-history.md`. Chercher un symptôme ou symbole avant
lecture ; les anciens exemples Windows et procédures amont sont des archives,
pas des obligations. Les autres références spécialisées restent disponibles pour
préserver les connaissances du projet. Les liens historiques et faits vérifiables
restent contrôlés, y compris dans les références déplacées.

`CLAUDE.md` et les fichiers suivis sous `.claude/` sont retirés. Les réglages locaux
non suivis restent intacts. Les contrôles CI valident désormais l’unique arbre Codex,
sans imposer de parité entre deux produits. Les tests de parité obsolètes ont été
remplacés par la validation des métadonnées et fichiers réellement chargés ; les
régressions applicatives sont conservées.

## Cible OpenAI et sources

La cible interprétée pour « strate 6.1 » est **GPT-6.1 Sol** (`gpt-6.1-sol`).
La documentation officielle le présente comme adapté au code complexe et conseille
de calibrer les vérifications à la modification. Cette politique de fork applique
ces principes ; elle ne prétend pas fournir un réglage universel certifié par OpenAI.
Le modèle et l’effort de raisonnement du compte ne sont pas modifiés par ce dépôt.
Les permissions du système restent indépendantes des consignes d’autonomie.

Sources officielles consultées le 20 octobre 2026 :

- [Famille GPT-6, GPT-6.1 Sol et recommandations de prompting](https://developers.openai.com/api/docs/guides/latest-model)
- [Chargement de AGENTS.md et budget des instructions](https://learn.chatgpt.com/docs/agent-configuration/agents-md)
- [Skills locaux, découverte et références à la demande](https://learn.chatgpt.com/docs/build-skills)

La racine reste sous le budget standard de 32 Kio. Garder les consignes durables
courtes ; écrire les détails dans le document qui les porte. Évaluer cette méthode
sur des demandes réelles, puis corriger les comportements observés plutôt qu’ajouter
une règle générale pour chaque incident.
