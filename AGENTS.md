# Open Garden Planner — fork personnel macOS

Application PyQt6 de dessin et suivi du jardin. Ce fork reste léger : répondre
aux besoins du propriétaire et ouvrir des points d’extension utiles, en conservant
la compatibilité avec l’amont. Cible : Apple Silicon, exécution depuis les sources
avec `.venv/bin/python`. Répondre en français.

## Autonomie et résultat attendu

- Mener la demande jusqu’au résultat utilisable : comprendre le parcours concerné,
  modifier, vérifier techniquement, corriger les défauts introduits et livrer.
- Décider seul des choix locaux réversibles. Une ambiguïté mineure se résout avec
  une hypothèse annoncée. Questionner seulement si la réponse change le besoin,
  engage les données personnelles ou implique une action irréversible non autorisée.
- Un plan détaillé, une recherche web, un sous-agent ou une revue ne sont pas des
  passages obligés. Lire les sources pertinentes, pas toute la bibliothèque de skills.
- Pour une extension, réutiliser services, commandes et formats existants. Ajouter
  un point d’extension à la frontière réellement nécessaire ; éviter les frameworks,
  réécritures et couches génériques sans cas d’usage concret.
- Terminer quand le comportement demandé est démontré et les risques du diff couverts.
  Ne pas prolonger par des contrôles redondants ou du nettoyage hors périmètre.
  Bilan court : résultat, preuve, limite éventuelle, lien vers la PR.

## Vérification technique

Choisir la preuve selon ce qui peut casser, pas selon un quota de tests.

| Changement | Preuve attendue |
| --- | --- |
| Documentation / consignes | Références et formats valides ; tests des outils modifiés seulement |
| UI / catalogue | Tests existants affectés ; interaction ou rendu si le défaut est visuel |
| Géométrie / Qt | Coordonnées et transformations réelles ; événements viewport si concernés |
| Persistance / import / undo | Parcours isolé sauvegarde–réouverture, undo/redo et échec pertinent |
| MCP | Frontière concernée : authentification, validation, rejeu, thread Qt |
| Dépendance / plateforme / transversal | Intégrations affectées ; suite élargie ou complète si justifiée |

Reproduire un bug avant de conclure ; traces ciblées si la cause reste incertaine.
Un test doit protéger un comportement observable. Ajouter une régression seulement
si la couverture existante laisse un risque utile à couvrir. Conserver les régressions
héritées sauf obsolescence ou redondance démontrée. Après réussite, relancer uniquement
si une modification ou un doute concret le justifie. Un rendu offscreen ne prouve pas
l’acceptation visuelle native. Signaler les échecs préexistants et les contrôles impossibles.

```bash
bash scripts/setup_macos.sh                 # installation / dépendances seulement
.venv/bin/python -m open_garden_planner      # lancement natif, si nécessaire
.venv/bin/python -m pytest tests/path_to_affected_test.py
.venv/bin/python -m pytest -c pytest-macos.ini # petit socle facultatif, non exhaustif
.venv/bin/python -m ruff check src/ tests/ scripts/
.venv/bin/python scripts/check_agent_context.py # si contexte agent modifié
```

Les fixtures isolent données et réglages ; préserver cette isolation, y compris
pour les sous-processus. Ne jamais tester sur la bibliothèque ou le plan personnels.
Faire soi-même les vérifications accessibles ; ne demander au propriétaire que
les observations réellement impossibles à établir ici.

## Contrats à préserver

- `.ogp` : champs inconnus, compatibilité, écritures atomiques et sauvegardes.
  Les mutations passent par les services/commandes et l’undo/redo existants.
- Redimensionnement rectangulaire : `ui/canvas/geometry_apply.py` ; arrangement :
  `ui/canvas/arrange.py::build_arrange_command`. `CanvasScene.clear()` vide son
  suivi privé avant que Qt détruise les items.
- Distinguer identité botanique, observation et conseil inféré. Une date ou un trait
  inconnu reste inconnu. Import personnel : sauvegarde et rejeu sûr ; application
  fermée pour écrire dans son stockage utilisateur.
- MCP : conserver authentification, validation, idempotence et exécution sur thread Qt.
- Textes UI traduisibles via Qt ; compléter les catalogues enregistrés, compiler et
  exécuter `scripts/check_translations.py` selon `docs/TRANSLATING.md`.
- Garder hors Git secrets, données réelles et `MISSION_AGENT_JARDIN.md` (contexte privé).

## Livraison et revue

Inspecter état Git, remotes et commits récents avant les opérations Git. Travailler
sur une branche de fonctionnalité, préserver les changements étrangers et l’historique
publié. Commit cohérent, push uniquement sur `origin` (`Sevoiger/open-garden-planner`),
création ou mise à jour de la draft PR de cette branche. Pas de merge, release, tag,
publication wiki ou intervention amont. Aucun build Windows/Linux exigé pour ce fork.

Revue indépendante `senior-reviewer` pour changement à risque : persistance,
import, undo partagé, authentification, dépendance ou refonte transversale ; également
sur demande explicite. Les petites modifications ont une auto-relecture du diff.
Mandater la revue sur le diff exact et les risques identifiés ; corriger P0/P1,
faire revoir les corrections à risque, sans boucle de revues pour la forme.
Si indisponible, préciser la limite dans la PR. Pour la 3D, utiliser une revue visuelle
spécialisée quand le changement le justifie, avec mesures et images du parcours affecté.
Avant suppression d’un worktree : vérifier travail unique et usage actif, archiver si besoin.

## Références à la demande

- État du fork : `docs/fork/PROGRESSION.md` ; usage : `docs/fork/PLANTES_ET_DESSIN.md`.
- Choix du workflow et Codex GPT-6.1 Sol : `docs/fork/AGENT_WORKFLOW.md`.
- Skills locaux : `.agents/skills/` ; charger uniquement celui lié au problème.
- Contrats détaillés : `docs/09-architecture-decisions/` ; incidents :
  `docs/11-risks-and-technical-debt/`. Les procédures amont historiques ne sont pas
  des obligations de livraison de ce fork.

Mettre à jour le document qui porte le comportement changé. Aucun ADR ou récit de
bug obligatoire pour un petit correctif. `AGENTS.md` est l’unique consigne racine ;
aucun miroir Claude à maintenir. La configuration du compte Codex reste à son propriétaire.
