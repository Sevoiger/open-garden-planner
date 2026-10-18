# Progression — fork personnel macOS

Base: `603e3035cfdf345fa5f190d25cb95b5f325376e5` (1.30.10).
Branche: `feature/personal-macos-garden`. Mission: `/MISSION_AGENT_JARDIN.md`.
Aucun changement préexistant hormis la mission. Dépôt remonté à la racine
`OpenGardenPlanner` à la demande de l’utilisateur. `upstream` pointe vers cofade,
son URL de push est désactivée. Fork confirmé : https://github.com/Sevoiger/open-garden-planner. CLI officielle
locale `.tools/bin/gh`, connectée au compte Sevoiger ; origin pointe sur le fork.

## Diagnostic initial (18 octobre 2026)

Mac Darwin arm64. Python global 3.13, uv global 0.5.30 : non utilisés pour
l’environnement applicatif. Bootstrap local `.tools/bootstrap` avec uv 0.12.23,
Python natif 3.11.17 sous `.tools/python`, `.venv` verrouillé et installation
éditable. `uv pip check` réussi. Aucun changement du verrouillage.

| Déjà disponible | À adapter | À créer |
|---|---|---|
| Moteur 2D, centimètres, calques, sélection | Parcours simple des plantations provisoires | Scripts macOS, guide français |
| Métadonnées persistées des objets, parent_bed_id | Dates inconnues affichées comme aujourd’hui dans le panneau botanique | Dates partielles, groupe métier, états |
| Journal de notes positionnées | New Season efface notes et peut retirer les plantes | Interventions ciblées pérennes et undo |
| Écriture atomique et validation numérique | Sauvegarde avant enrichissement d’un ancien projet | Export et sauvegarde portable |
| MCP local, lectures et écritures protégées sur thread Qt | Étendre le contrat de suivi | Idempotence et parcours client MCP |

Sources personnelles absentes du dossier initial ; démonstration fictive à créer.
Tranche 1 en cours, aucun critère non vérifié ne doit être annoncé comme livré.

## Lot 0 — installation et corrections de présentation

- `scripts/setup_macos.sh` et `Lancer-Jardin.command` ajoutés.
- Autotest source `--selftest` : PASS (Qt 6.11.0 / Qt3D 6.11.0, MCP local).
- Verrou uv et export PEP 751 cohérents, dépendances compatibles.
- Première fenêtre lancée réellement ; comportement instable et découpage
  signalés par l’utilisateur, qui l’a ensuite fermée. Ne pas la rouvrir
  pendant les tests. Aucune validation visuelle positive ne peut être revendiquée.
- Rendus de diagnostic natifs (widgets non affichés) : Aqua laisse des surfaces
  sombres sous le thème clair ; Fusion corrige ce contraste. La barre d’état
  déborde à 800 px ; les détails optionnels sont maintenant masqués par priorité.
- 30 tests ciblés passent (thème, largeur 800/1200/1800, lanceur, espaces, accents,
  environnement absent, arrêt sans redémarrage). Nouveau composant typé sans erreur.
- L’origine des clics apparents reste non établie. Premier run complet interrompu
  après 307 réussites et 13 skips ; aucune preuve suffisante pour l’attribuer aux tests.
- Suite complète relancée hors écran, fenêtre réelle fermée.

Suivant : terminer les contrôles/revue du lot 0, puis fiches et dates partielles.

Contrôles supplémentaires : préparation exécutée deux fois avec succès ;
lanceur testé sans installation ni redémarrage automatique. Lint, sécurité HIGH,
parité agents, citations et cohérence wiki passent. Le contrôle des traductions
était bloqué par l’alias macOS du dossier temporaire ; correction du chemin
canonique, test d’échec avant / réussite après, puis catalogue complet validé.
Le garde-fou de typage amont refuse macOS par conception ; mypy direct valide
le nouveau composant sans erreur. Aucun seuil du dépôt n’a été modifié.
Les workflows hérités de release et de création mensuelle d’issue sont maintenant
réservés à l’amont pour empêcher une publication automatique du fork.
