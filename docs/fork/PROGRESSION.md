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


## Retour propriétaire — menus et accueil (2026-10-18)

Le propriétaire juge le reste utilisable mais signale : ouverture au survol à droite,
catégories hors écran dès Trees, menus vides selon l'affichage et filtres au clic vides.
Deux captures locales montrent un popup coupé et les actions d'accueil illisibles.
`toPatch/` est exclu de Git. Le reste de son essai constitue un retour positif partiel,
pas une validation de tous les parcours ni une autorisation de merge/release.

Reproduction mesurée : catégorie 638×866 placée à x754/y589 sur écran800×800 ;
contenu des CTA d'accueil haut de -5/-6 pixels. Corrections : ouverture explicite
au clic par défaut, placement partagé borné à l'écran de l'ancre, contenu défilant,
accueil défilant sans compression des boutons. 59 tests ciblés passent, dont toutes
les catégories dans les trois modes de thème et le dernier objet accessible.
La boîte combo simple affiche ses options en clair et sombre dans la sonde ; le
cas des filtres vides reste à localiser, une précision a été demandée au propriétaire.
Revue et contrôles complets de ce delta à terminer. Voir ADR-060.


Précision du propriétaire : les filtres vides sont dans les catégories en haut.
Sonde avant/après sur le même scénario : filtre ground_cover dans Trees => zéro
résultat ; fermeture, thème sombre et réouverture => filtre encore actif/zéro
vignette avant, filtres vides/23 vignettes après correction. La réouverture remet
texte et caractéristiques à zéro. Un résultat vide affiche un libellé explicite et
Clear filters permet de retrouver les objets sans fermer le panneau. Le scénario
est testé sur la même instance au travers d'un changement de thème. 73 contrôles
ciblés réussis ; confirmation native des corrections encore nécessaire.

### Bilan final du correctif de menus

État de production testé : `b836787`. Suite complète hors écran dans un checkout
figé : **8626 réussites, 46 skips, 2 tests CLI incompatibles Darwin désélectionnés,
1 échec**, en 526,68 s. Le seul échec est le round-trip DXF déjà reproduit sur la
base amont inchangée. Aucun nouvel échec. Revue indépendante du delta sans P0/P1,
34 tests relancés séparément par le reviewer et réussis. Ruff, Bandit HIGH=0,
traductions EN/DE complètes (2723 clés), parité, citations, secrets et sources wiki
passent ; aucune augmentation des erreurs mypy par fichier. PR personnelle toujours
en brouillon, aucune fusion/release. La session du propriétaire n'a pas été fermée :
enregistrer, quitter puis relancer le lanceur charge les nouveaux menus. Confirmation
visuelle native encore attendue sur survol/clic, bords d'écran et filtres/thèmes.


## Deuxième lot de captures et plantes classiques (2026-10-18)

Deux nouvelles captures montrent le renommage de calque trop bas et un chevauchement
liste/opacité/boutons quand plusieurs panneaux sont ouverts. Sonde avant changement :
minimum du panneau conservé à192px alors que son sizeHint passe à336px après ajouts ;
ligne rendue26px, éditeur18px pour un besoin31px. Après correction : ligne39px,
éditeur31px et plancher mis à jour sur les demandes de disposition hors animation.
Tests de croissance/réduction avec autre panneau ouvert : réussis, clair et sombre.

Le catalogue contient déjà118 espèces hors ligne. Quatre listes classiques de9 plantes
chacune sont préparées à partir de ces données, avec import explicite (application
fermée), copie de sauvegarde, refus de données illisibles et reprise sans doublons.
Aucune plantation réelle n'est ajoutée. Les 36 fiches sont maintenant installées dans
les quatre collections de la bibliothèque locale, application fermée et après revue.
Le fichier existant a été sauvegardé ; une deuxième exécution laisse ses octets
inchangés et n'ajoute aucun doublon. 77 contrôles ciblés réussis sur `d2a8c90` ; revue
indépendante favorable, avec 27 tests relancés. La revue avait identifié un plancher
de hauteur conservé lors de la fermeture d'un panneau autonome : corrigé et couvert
par des clics réels sur son titre. Dessin réel/cote encore absents ; guide
PLANTES_ET_DESSIN.md ajouté, dont l'explication rayon<angle en centimètres/degrés.
Confirmation visuelle native des calques encore nécessaire.

Suite complète dans le checkout figé `b11f53c` : **8634 réussites, 46 skips,
2 tests CLI Darwin désélectionnés, 1 échec**, en 550,72 s. Seul échec : largeur nulle
au round-trip DXF, déjà reproduit sur la base amont inchangée. Le correctif ultérieur
`d2a8c90` est validé séparément par les 77 contrôles ciblés et la re-revue indépendante.
Ruff, Bandit HIGH=0, traductions, parité, citations, secrets et sources wiki passent ;
mypy reste à 1928 erreurs héritées sans augmentation par fichier. Aucun seuil modifié.


## Molette, catalogue français et fiches import01 (2026-10-18)

Le propriétaire construit désormais son plan à la main. Aucun croquis n’est repris
ni aucune plante positionnée automatiquement. Un geste de molette sur un menu fermé
ne modifie plus sa valeur : il défile dans le formulaire. Les listes ouvertes et le
clavier restent utilisables. La sonde mesure avant correction index1 vers2 sans
défilement, puis index1 conservé avec60px de défilement. Les gestes pixel du pavé
tactile sont aussi couverts ; aucune modification du zoom ni des champs numériques.

Installation locale effectuée application fermée, après revue : 118 fiches embarquées
avec noms français, 80 fiches génériques « variété à préciser » et 37 fiches privées
du lot import01. Ce sont 235 fiches dans le pack, pas 235 espèces distinctes. Les
fiches personnelles préexistantes restent présentes. Onze nouvelles collections
sont accessibles dans Plant Lists ; les plantes importées sont aussi recherchables
hors ligne. Les anciennes listes Classiques ont leurs noms d’origine passés en
français : 31 changements effectifs, cinq noms déjà identiques en français/anglais.
Les autres données et personnalisations sont préservées. Les sources JSON/PDF restent
hors Git. Les fichiers locaux existants ont leurs sauvegardes ; la relance a laissé
les deux stockages strictement identiques. Les recherches Tomate, Sauge générique,
Sauge officinale et Taiga ont été vérifiées dans la bibliothèque réellement installée.

La revue a identifié une réapparition des fiches supprimées lors du rejeu : le marqueur
bloque maintenant tout le lot, pas uniquement sa liste. La régression supprime une
fiche et enrichit sa source sous la même clé, puis vérifie l’absence de toute écriture.
Revue finale 4095b12 favorable ; 19 tests import/molette relancés indépendamment.
105 contrôles ciblés initiaux réussis ; le contrôle final inclut cette régression.
Les six guides de ce dossier sont compilés dans un PDF local, avec sommaire cliquable,
signets et pagination ; les deux annexes techniques ont été traduites en français.
La lecture visuelle et la suite complète restent à consigner une fois terminées.
