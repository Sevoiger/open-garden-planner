# Agents IA et vérifications du fork personnel

Politique du propriétaire révisée le 20 octobre 2026. Le guide opérationnel est
`AGENTS.md`, identique à `CLAUDE.md`. L'historique des fonctionnalités et leurs
limites reste dans `PROGRESSION.md`. Cette page explique le nettoyage ; elle ne
remplace pas les contrats métier ou les décisions d'architecture.

## Audit de la documentation

L'ancien guide racine représentait 53 947 octets. Il mélangeait l'historique des
livraisons amont, des procédures Windows, un mandat macOS ajouté en fin de fichier
et des obligations contradictoires de vérification. Les descriptions des skills
cumulaient des déclencheurs très larges. La revue demandait aussi une posture
hostile et des contrôles systématiques sans rapport avec le risque du diff.

La version courte conserve les limites du fork, les commandes macOS, l'isolation
des données, les contrats de persistance/undo/MCP, les traductions et la revue
indépendante. Elle renvoie aux documents techniques selon la tâche. Les anciens
récits de livraison restent consultables dans Git et dans leurs documents de
référence ; ils ne sont plus injectés à chaque démarrage d'agent.

Les descriptions des 23 skills sont raccourcies. Les trois procédures de gestion
des changements, de validation et de documentation sont réécrites. Les autres
références conservent leurs connaissances techniques avec un rappel explicite du
périmètre du fork. Les instructions des reviewers Claude et Codex sont alignées,
factuelles et proportionnées au diff. Le budget Codex revient à 32 Kio.

Ce choix suit les recommandations OpenAI : des instructions ciblées, des
références consultées selon le besoin et moins de recettes ou de tests répétés
sans nouvelle raison. L'article consulté vise GPT-6 Astra ; son application au
workflow GPT-6.1 est notre choix de maintenance, pas une certification du dépôt.
La documentation de génération de code cite explicitement `gpt-6.1-sol` comme
modèle utilisable dans Codex. Aucun modèle du compte n'est reconfiguré ici.

Sources officielles consultées le 20 octobre 2026 :

- [Repenser les skills et prompts](https://developers.openai.com/blog/rethinking-skills-and-prompts-for-gpt-6-astra)
- [Configuration des instructions AGENTS.md](https://learn.chatgpt.com/docs/agent-configuration/agents-md)
- [Génération de code avec Codex](https://developers.openai.com/api/docs/guides/code-generation)

## Vérifier l'essentiel

```bash
.venv/bin/python -m pytest -c pytest-macos.ini
```

Ce profil réutilise cinq fichiers de tests existants, sans nouvelle famille de
tests ni modification de la collecte complète. Il couvre :

| Parcours | Risque protégé |
| --- | --- |
| Fiches pérennes, interventions, sauvegarde et restauration | Dates inconnues/partielles, liens, undo, atomicité, portabilité |
| Catalogue → placement → undo → réouverture | Identité complète, entrées invalides, recherche et pagination |
| PNG personnel → plan → bibliothèque | Image portable, géométrie, champs inconnus et sauvegarde prudente |
| Menus et panneaux macOS | Confinement à l'écran, défilement, filtres et ouverture |
| Client MCP réel → écriture → réouverture | Refus sans authentification, rejeu, conflits et undo |

Le dernier parcours nécessite des sockets loopback autorisées. Une interdiction
locale doit être indiquée comme contrôle non exécuté, sans fabriquer de réussite.

Pour un changement précis, lancer seulement les fichiers ou cas concernés est
souvent suffisant. Pour une documentation seule, vérifier ses liens et la parité
des contextes suffit. Les autres contrôles suivent la nature du diff : traductions
si les textes changent, sécurité si une frontière d'entrée change, etc.

Pour la persistance, l'undo, le cœur partagé, les dépendances ou la plateforme,
élargir aux suites concernées et à la suite complète selon le risque. La commande
historique reste disponible :

```bash
.venv/bin/python -m pytest tests/ -v
```

Ne pas assimiler le profil court à une validation exhaustive. Les près de 10 000
cas historiques incluent les déclinaisons paramétrées ; ils ne correspondent pas
à 10 000 nouveaux scénarios à maintenir individuellement. Aucun test hérité n'est
supprimé pour faire baisser ce chiffre. Une suppression ou consolidation future
doit démontrer une duplication ou une obsolescence précise.

Pas de nouveau test automatique pour chaque détail. Étendre un parcours existant
si une régression importante le justifie ; éviter les tests qui recopient
l'implémentation ou figent une formulation. Une correction de documentation ou
un ajustement cosmétique réversible n'exige pas un test nouveau par principe.

## Git et worktrees

Conserver l'historique déjà publié et une PR brouillon par branche de travail.
Le dépôt personnel est `origin` ; le push amont est désactivé. Aucun merge, tag,
release ou publication wiki n'est autorisé par cette procédure.

Avant de supprimer les anciens worktrees : vérifier les fichiers modifiés et non
suivis, les données ignorées hors caches et l'absence d'activité. Les copies de
travail du nettoyage du 20 octobre sont archivées sous
`.local/worktree-backups/2026-10-20/`, ignoré par Git. Le manifeste contient les
commits de base, les chemins et leurs SHA-256 ; les archives comprennent aussi
un diff binaire. Restaurer dans un dossier temporaire et comparer avant toute
réapplication : ces snapshots sont antérieurs au code courant.
