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

Revue indépendante du lot 0 : un P1 trouvé (un détail masqué restait masqué après
raccourcissement du texte à largeur constante). Correction par notification
explicite `StatusLabel.text_changed` vers le recalcul coalescé ; test de
régression sans resize ni appel manuel au recalcul : réussi. Les huit tests de
mise en page/lanceur passent. P2 de bootstrap corrigé : lecture de la version uv
avec awk, téléchargement du Python du bootstrap par uv, prérequis uv documenté.
Nouvelle passe indépendante demandée après ces corrections.


## Tranche 1 — suivi pérenne

La seconde revue indépendante du lot 0 est sans P0/P1. Suite complète du lot 0 :
8565 réussites, 46 skips, 2 tests désélectionnés car leur CLI refuse Darwin.
Un échec DXF (`test_dxf_round_trip_preserves_position`) se reproduit aussi sur
la base amont inchangée ; il n'est pas masqué ni attribué à cette tranche.

Fiches individu/groupe/zone, dates partielles, états distincts et journal
multi-cibles implémentés. Noms/notes réutilisent les données existantes ; aucun
calendrier exact n'est inventé. Écritures GUI/MCP via une commande partagée,
idempotence persistante, sauvegarde d'origine, médias copiés et restauration
portable dans un nouveau dossier. Démonstration `examples/jardin-demo.ogp`
explicitement fictive, avec fond artificiel calibré. Aucun plan personnel trouvé.
Guides : `UTILISATION_JARDIN.md`, `FORMAT_SUIVI.md`, `HOME_ASSISTANT.md`.

Les formulaires ont été rendus hors écran à 13 points dans une petite fenêtre :
les commandes de pièces jointes ont été regroupées dans un menu pour éviter
leur découpage. Le contraste du texte sélectionné a été corrigé dans le thème.
Les tests vérifient les dimensions et la palette réelle, sans prétendre remplacer
l'essai visuel du propriétaire. Le scénario réel MCP vérifie authentification,
refus, répétition, modification, sauvegarde et réouverture. Les saisons françaises,
anglaises et allemandes et les virgules décimales sont prises en charge.

Revue et contrôles finaux de la tranche en cours. Aucun merge, tag ou release.

Première revue de la tranche : P0 reproduit sur les périodes structurées dont le
libellé est vide/arbitraire. Les deux formulaires conservent maintenant la période
originale lorsqu'on modifie une autre donnée ; tests des deux éditeurs et des
changements de date intentionnels. P1 corrigés : refus de suppression sur cible
verrouillée affiché sans exception non interceptée, icône existante du menu ajoutée.
Les noms longs de la liste sont abrégés avec infobulle complète.
Le run complet intermédiaire a été arrêté après les échecs d'iconographie pour
relancer sur ces corrections ; ce run ne constitue pas le résultat final.

## Bilan automatisé — état revu `09aa3b9`

Re-revue indépendante : aucun P0/P1, trois régressions reproduites et relancées
par le reviewer. Le groupe formulaires/dates/suppression/icônes passe : 61 tests.
Suite complète native arm64 hors écran (checkout isolé, PYTHONPATH explicite) :
8609 réussites, 46 skips, 2 tests désélectionnés (CLI explicitement Linux/Windows),
2 échecs en 479,52 s. Échec DXF déjà présent sur la base amont ; second échec :
l'inventaire exhaustif du test de sécurité MCP n'incluait pas les quatre nouveaux
outils. Inventaire complété sans changer le code de production, puis module de
gate d'authentification et client MCP réel relancés avec succès. Le run complet
est rapporté tel quel ; il n'a pas été réécrit en « tout vert ».

Ruff, Bandit HIGH=0, traduction complète EN/DE (2721 clés), verrou/export,
dépendances installées, parité agents, références de compétences, scan secrets
et sources wiki : réussis. Audit de dépendances : aucune vulnérabilité connue
hors exceptions héritées de la CI. Mypy direct : 1928 erreurs héritées contre
1943 sur l'amont inchangé, aucune augmentation par fichier ; cinq nouveaux
modules sans erreur. Aucun seuil ni baseline modifié. Les contrôles GitHub de
typage/dépendances Linux et Windows passent ; les suites GitHub se relancent
sur le commit final et leur état est consultable dans la PR.

Critère éditable mesuré : modification temporaire du titre dans les sources,
lancement d'un processus neuf, restauration puis second lancement neuf ; le
changement apparaît/disparaît sans installation ni construction. Sources
restaurées intégralement après l'essai, aucun GUI visible lancé pendant les tests.

PR brouillon : https://github.com/Sevoiger/open-garden-planner/pull/1.
Restent non validés : aspect et stabilité sur l'écran du propriétaire, origine
des clics apparents, et import/calibration d'un jardin réel faute de sources.
L'usage reste en anglais/allemand avec guide français. Home Assistant reste une
architecture future documentée. Pas de merge, tag, release ou livraison d'installeur.
