# Progression — fork personnel macOS

## Audit technique des écarts du fork — 23 octobre 2026

Les six points résiduels ont ensuite été traités : copies des points Qt dans le
test DXF (le service import/export était correct), sélection du `uv` local épinglé,
support du contrôle mypy Darwin avec référence indépendante, et neuf citations
actualisées. Les quatorze cas initialement en échec passent. Le verrou réel et le
contrôle mypy macOS passent ; les budgets Linux/Windows sont inchangés.
La validation est complétée par 43 tests natifs Cocoa et une revue des captures
françaises des fenêtres principale, données jardin et orientation. Revue
indépendante du complément : aucun défaut identifié. Détails et limites dans le
rapport ci-dessous ; le bilan de la première campagne est conservé comme historique.

[Rapport détaillé](AUDIT_TECHNIQUE_2026-10-23.md) : revue des parcours modifiés
depuis `603e303` jusqu’à `e80a4ff`, corrections des ruptures de références lors
des transformations, des matériaux après réouverture, des surfaces tournées
regroupées, des objets Qt temporaires et de l’interface d’orientation.
Les projections de zone et l’export CSV passent d’un coût quadratique à un
parcours linéaire ; à 5 000 objets fictifs, environ 2,1 s deviennent 5 à 16 ms.

La campagne large initiale donne 9 970 réussites, 48 skips et 14 échecs ; huit
échecs sont résolus. Les contrôles finaux ciblés donnent 547 réussites distinctes,
avec revue indépendante des corrections à risque, Ruff et traductions valides.
Les limites alors signalées (DXF, outillage, citations et validation native) ont
été réexaminées dans le complément ci-dessus. Aucun plan ni stockage personnel
utilisé pour les tests.

## Édition des contours denses — 20 octobre 2026

La capture du propriétaire a été reproduite sur une capsule fusionnée de
255 sommets : l'édition créait 510 poignées et 510 annotations superposées.
Au-delà de 24 sommets, les annotations passent en infobulles et les poignées
conservent leurs indices d'origine, filtrées selon visibilité et espacement en
pixels, avec un plafond de 128 sommets et 128 milieux d'arête. Seules les arêtes
assez longues à l'écran proposent une insertion. Zoom et déplacement de vue
renouvellent les poignées sans supprimer celle en cours de glissement.
Aucun sommet du modèle n'est supprimé ni simplifié.

Validation : 63 tests ciblés (polygone, fusion, poignées, polylignes, courbes et
rendu) réussis ; les cinq régressions passent aussi en Qt Cocoa natif, dont
le double-clic et le drag dans le viewport, l'undo/redo et l'insertion sur un
bord visible aux extrémités hors écran. Rendu natif inspecté ; gate de traduction,
Ruff et diff check réussis. Revue senior indépendante : défaut de visibilité
initial des milieux corrigé puis revu sans nouveau finding. Le plan personnel
modifié dans examples/jardin-demo.ogp reste hors du commit.

## Colonne étroite et fusion multiple — 20 octobre 2026

La colonne de droite accepte une largeur réduite sans être bloquée par le contenu.
Le séparateur est plus facile à saisir ; les détails de plante proposent
Rechercher / Charger / Créer, empilés si nécessaire. Le contenu plus large reste
accessible par défilement horizontal.

Une sélection de formes fermées reliées par contact ou chevauchement se fusionne
via clic droit > Combiner les formes > Fusionner. Rectangles, cercles, ellipses
et polygones sont acceptés ; un seul undo restitue les originaux. La géométrie,
les métadonnées compatibles et le style sont conservés par les mécanismes existants.
Les sujets botaniques, objets liés/contraints et métadonnées incompatibles sont
refusés avant confirmation. Les courbes deviennent des contours polygonaux.

Validation : 82 tests ciblés réussis, 9 tests Qt Cocoa natifs et inspection native
à 240 px et du contour rectangle + deux cercles. Undo/redo et sauvegarde–réouverture
couverts. Une attente de scrollbar dépendant d'un layout périmé a été remplacée
par un débordement réel après redimensionnement. Les 2 759 clés EN/DE/FR sont
complètes et recompilées ; Ruff et diff check passent. Revue senior indépendante,
remarque de couverture corrigée, aucun défaut source restant identifié.
Données de test isolées ; aucun plan personnel utilisé.

## Confort des contrôles et français — 20 octobre 2026

Survol des panneaux sans changement de fond. La molette ne change plus les
valeurs numériques, dates, listes et curseurs, ni la navigation des menus ouverts ;
le défilement des panneaux/formulaires et le zoom du plan sont conservés.
La FR emploie « association » pour les guildes de plantes, avec explication dans
les dialogues agrandis. Les colonnes de rôles et boutons à texte long ne sont
plus contraints à des largeurs trop petites.

Ajout du verrouillage/déverrouillage de position au clic droit via les commandes
de contrainte FIXED existantes, sans nouveau champ de stockage. Cadenas corrigé
pour le repère Y-up, y compris pour les objets sans ancre de dessin. La revue a
révélé un contournement par X/Y dans Propriétés ; il est corrigé et sa régression
vérifie aussi le déplacement après déverrouillage et son annulation.

Validation : 195 tests ciblés réussis avant les deux régressions supplémentaires
(coordonnées verrouillées, largeur des boutons FR), elles-mêmes réussies ;
39 tests de menus contextuels existants réussis. Les 18 tests verrouillage et
molette passent également en Qt Cocoa natif. Inspection native des dialogues
860×700 et 620×320 et du cadenas ; audit des 38 dialogues en FR avec police
agrandie et petit écran. Catalogues EN/DE/FR complets et recompilés, Ruff et
diff check réussis. Revue senior indépendante : P1/P2 corrigés puis corrections
revues sans nouveau finding. Aucun plan ni réglage personnel utilisé pour les tests.

## Français sélectionnable — 20 octobre 2026

Le registre Qt propose désormais **Français** dans **View > Language**. Le choix
est mémorisé et chargé au redémarrage ; le menu devient **Affichage > Langue**.
Le catalogue source et son binaire compilé couvrent les 2 751 clés actives de
l'inventaire : menus, dialogues, outils, suivi du jardin et noms usuels intégrés.
Les noms scientifiques, saisies personnelles et contenus externes restent intacts.

Le rendu natif a révélé des noms d'outils laissés en anglais : ils sont maintenant
traduits à la création de la vue, sans modifier les identifiants des outils. Trois
libellés de construction/rognage ont aussi été ajoutés aux catalogues EN/DE.
Les lettres d'accès des menus français sont distinctes dans chaque menu.

Validation : contrôle complet inventaire/variables/catalogues/binaires réussi,
38 tests ciblés menus, langue mémorisée et outils réussis après correction ;
sélection du français au redémarrage et raccourcis de menus également vérifiés
par deux tests Qt Cocoa natifs. Fenêtre et préférences inspectées dans les thèmes
clair et sombre sur des réglages et bibliothèques de test isolés. Ruff et
`git diff --check` passent. Auto-relecture du diff ; acceptation sur le plan
personnel distincte des contrôles techniques.

## Illustrations retrouvées, import01 et panneaux réglables — 20 octobre 2026

Les 246 illustrations et 15 silhouettes n'avaient pas été supprimées par le
retour usine des fiches ; leur section est de nouveau visible par défaut. Les
fiches de cultivar utilisent un symbole d'espèce/genre ou une association
explicite validée dans le registre ; le rendu conserve l'identité botanique et
les symboles délibérément choisis. Les PNG personnels restent prioritaires.

Le placement par choix dans le catalogue ou la recherche demande par défaut
centre puis bord. Une case dans les catégories permet de retrouver le placement
automatique en un clic ; le glisser-déposer conserve sa taille initiale connue
ou générique. Choisir une emprise ne modifie pas l'envergure botanique à maturité.

Les panneaux de droite peuvent défiler indépendamment, se replier et être
redimensionnés par leur bord inférieur. Les hauteurs sont conservées en session,
avec double-clic pour rétablir la hauteur initiale ; Properties dispose de plus
de place au départ. Le contrôleur conserve l'ordre, les états et le mode
historique pour ses autres appelants.

Réinstallation locale, application fermée, des seules 37 fiches d'import01 via le
service existant : 26 dessins d'espèce/genre, 10 rapprochements signalés et le PNG
personnel du rosier. Le catalogue actif comporte 118 + 37 fiches, sans réinstaller
le gros lot Permapeople. Staging isolé, vérification des 37 associations et rejeu
sans doublons effectués. Planche et rapport dans `output/import01/`, ignorés par Git.
Le guide porte aussi la recherche d'extensions : aucun pack ou marketplace
utilisable trouvé dans les sources publiques examinées.

Validation : 129 tests placement/catalogue/import/panneaux et 532 tests de rendu,
listes et PNG réussis (660 tests distincts). Trois contrôles ciblés réussissent
également en Qt Cocoa natif : drag de poignée, maintien de hauteur, placement
centre/bord. Ruff, inventaire/catalogues/binaires de traduction et comparaison
mypy locale avec les budgets Linux passent sans assouplissement des seuils.
Revue indépendante du nouveau lot et revue complémentaire du chemin PNG/persistance
antérieur réalisées : aucun finding P0/P1/P2. Les données personnelles sont restées
hors du périmètre de la revue et du dépôt.

## Prise en main, formes et textures — 20 octobre 2026

Branche `feature/garden-usability-shapes-textures`, basée sur le travail personnel
déjà présent. Les menus de plantes affichent d'abord les fiches botaniques et
replient les illustrations génériques. Le profil précédent est effacé lorsqu'une
plante sans fiche est sélectionnée. La mini-vue suit aussi les changements de
taille du viewport, sans attendre un redimensionnement de toute la vue.

Import de remplissages PNG personnels dans les surfaces 2D : image intégrée au
`.ogp`, répétition à largeur réglable en cm, proportions/couleurs/alpha conservés,
annulation et retour aux motifs intégrés. Le décodeur borné est partagé avec les
visuels personnels de plantes. La fusion existante **Boolean > Union** a été
vérifiée sur un rectangle à deux bouts arrondis ; les cercles utilisent leur vraie
géométrie, sans agrandissement dû au trait ou à l'ombre.

Retour local au catalogue d'origine effectué à la demande du propriétaire,
application fermée, après archivage vérifié des deux stockages de plantes.
Aucune donnée personnelle n'est ajoutée au dépôt. Le guide
`PLANTES_ET_DESSIN.md` porte l'usage, la restauration et la recherche d'une
traduction française existante : aucun catalogue FR trouvé dans les 24 branches
amont et les cinq autres forks publics examinés.

Validation : 264 tests formes/remplissages/persistance/panneaux/catalogue et
40 tests profil botanique/mini-vue/visuels personnels réussis ; trois contrôles
de positionnement et profil réussis également avec Qt Cocoa natif, hors sandbox.
La tentative Cocoa confinée s'interrompait avant création de la fenêtre ; aucune
modification de l'application n'a été nécessaire pour le lancement natif.
Ruff et contrôle source/catalogues/binaires des traductions réussis.
Après les diagnostics CI, les nouvelles surfaces Qt ont reçu leurs annotations
explicites et l'import PNG son export typé. Les trois modules PNG passent mypy
sans erreur ; l'analyse locale avec cible de typage Linux ne dépasse aucun budget
existant. Aucun seuil ni baseline n'a été assoupli. Les 93 tests affectés par ces
dernières corrections et le contrôle des traductions ont de nouveau réussi.
La revue indépendante `senior-reviewer` a été demandée sur le diff indexé mais
n'a pas pu démarrer, quota du service atteint. Auto-relecture effectuée ; le
chemin PNG/persistance a ensuite reçu la revue complémentaire décrite plus haut.
L'acceptation visuelle sur le plan
habituel du propriétaire reste distincte de ces vérifications techniques.

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
108 contrôles ciblés sous isolation réussis ; 11 contrôles enfant/confinement
réussis et relancés par le reviewer. Deux anciens tests avaient lu les noms de la
bibliothèque personnelle : la suite a été arrêtée, les fiches précédentes et les
235 fiches importées comparées en lecture seule et confirmées intactes. Le résolveur
de données est maintenant redirigé avant collection et par test, et les tests enfants
qui construisent l’application reçoivent un prélude explicite. Chemins de production
inchangés ; la re-revue de f34e618 est favorable.
Les six guides de ce dossier sont compilés dans un PDF local, avec sommaire cliquable,
signets et pagination ; les deux annexes techniques ont été traduites en français.
PDF local de 16 pages : toutes les pages rendues et inspectées, 23 signets,
sommaire cliquable et aucun texte hors des marges. Suite complète finale isolée sur `f34e618` : **8656 réussites, 46 skips,
2 tests CLI explicitement incompatibles Darwin désélectionnés, 1 échec**, en 802,70 s.
Seul échec : largeur nulle au round-trip DXF, déjà reproduit sur la base amont inchangée.
Aucun nouvel échec. Après les tests, les deux bibliothèques réelles ont exactement
les mêmes empreintes qu’avant la suite. Ruff, Bandit HIGH=0, traductions EN/DE,
parité, citations, secrets et sources wiki passent ; mypy reste à 1928 erreurs héritées
sans augmentation par fichier. Confirmation visuelle native de la molette encore
nécessaire. PR personnelle en brouillon, sans fusion ni release.

## Préférences et audit des formulaires — 19 octobre 2026

La capture fournie dans toPatch confirme une compression du formulaire Préférences.
Le probe isolé mesure des boutons à 0–12 px pour 17 px de texte ; le groupe Agent API
fait 82 px pour un minimum de 235 px. Après correction, les boutons font 31 px et
les groupes retrouvent leur minimum. Les 38 types de boîtes personnalisées et le
formulaire d'association à une guilde partagent maintenant une protection contre
la compression : défilement du contenu, boutons terminaux séparés, défilement
horizontal des longues rangées, limites de l'écran et conservation des tailles
maximales explicites. Les largeurs fixes de textes/boutons ont été assouplies.
L'audit couvre les onglets, les champs, les groupes et les rectangles de texte des
boutons, en anglais sombre 13 pt et allemand clair 18 pt sur 600 × 440. La sauvegarde
et la réouverture des préférences utilisent le chemin existant, sous isolation.

La clé Permapeople configurée fonctionne : un contrôle en lecture seule renvoie
HTTP 200 pour trois recherches Sophora. Aucun résultat `Sophora prostrata`, même
avec l'orthographe correcte ; aucune autre espèce n'a été assignée à sa place.
Aucune clé n'est affichée ou copiée dans les sources. L'application native ouverte
n'a été ni fermée ni pilotée ; validation manuelle du correctif encore nécessaire.

La vérification visuelle a aussi révélé un défaut du nouveau pied de formulaire :
un transfert générique de QLayoutItem laissait Cancel/Save superposés au texte du
haut. Le transfert utilise maintenant addWidget/addLayout pour adopter les enfants.
Les tests vérifient parentage, position et présence dans le viewport ; les rendus
haut/bas corrigés ont été inspectés et relus indépendamment. Les 80 contrôles
responsive ont été relancés avec succès par le reviewer, sans P0/P1 restant.

La suite générale a ensuite révélé deux régressions d'affichage immédiat : les
recommandations d'un massif et le code de connexion IA restaient masqués sans
attendre la boucle d'événements. Les nouveaux conteneurs sont maintenant affichés
explicitement ; les sections volontairement cachées le restent. Les 143 tests
amendements/connexion/suivi/formulaires passent ensemble. L'ancienne assertion de
largeur client exactement 800 px vérifie désormais que le cadre entier tient dans
l'écran. Le contrôle natif après redémarrage reste à confirmer par le propriétaire.

Bilan final du correctif : **8736 tests réussis, 46 skips, 2 tests CLI explicitement
incompatibles Darwin désélectionnés, 1 échec**, en 853,23 s. Seul échec : le round-trip
DXF déjà reproduit sur la base amont. Les deux contrôles lock/version passent avec
uv 0.12.23 et un cache accessible. Ruff, traductions compilées EN/DE, parité, scan des
secrets, sources wiki et Bandit HIGH=0 passent. Mypy reste à 1928 erreurs héritées,
sans augmentation par fichier ; le nouveau module est sans erreur. Revue finale
sur un worktree frais favorable, 109 tests relancés dans cette copie ; les 143 tests
ciblés avaient aussi été relancés indépendamment. Aucun pilotage de fenêtre native.

## Catalogue massif privé — 19 octobre 2026

La clé déjà configurée a permis une lecture paginée complète de Permapeople :
9166 profils, dont 295 marqués Variety. Après dédoublonnage des espèces de même
nom botanique et conservation des identités existantes, 9040 fiches supplémentaires
ont été préparées puis installées dans la bibliothèque hors ligne : 8745 espèces
et 295 variétés. Les 247 fiches précédentes restent strictement identiques, avec
leurs noms français et leurs données personnelles. Les espèces supplémentaires
s'affichent sous leur nom botanique ; le nom anglais source reste un alias de
recherche. Les variétés gardent leur libellé source et leur filiation brute.
Les caractéristiques composites ou ambiguës restent non renseignées dans les
champs simples ; les valeurs originales, crédits, liens et licence CC BY-SA 4.0
sont conservés dans chaque fiche. Aucune image n'a été téléchargée en masse.

L'import est privé et explicite, dans les stockages existants ; aucune donnée de
ce catalogue ni clé n'est embarquée dans le dépôt. L'ajout concerne la recherche
hors ligne, pour éviter une collection graphique de 9000 vignettes. Le cache et
les outils ponctuels restent dans `.local/plant-catalog/` et `.local/`. Avant
installation : validation du modèle, contrôle de l'application fermée, relecture
indépendante, sauvegarde exacte et remplacement atomique du fichier. Le reçu
empêche un rejeu de rétablir des fiches supprimées ensuite. Vérification après
écriture : sauvegarde identique octet pour octet, anciennes fiches inchangées,
rejeu sans écriture. Le guard de processus vérifie l'exécutable et ses arguments,
car les arguments des outils peuvent eux-mêmes citer le nom de l'application.

Un contrôle sur copie mesure 9287 fiches disponibles : chargement à froid 0,47 s,
recherches ciblées Sauge/Tomate/Sophora 0,04 à 0,065 s, recherche vide limitée à
100 résultats 0,27 s. Ce sont des mesures locales, pas une garantie générale.

Sophora prostrata reste absent des réponses du fournisseur. Une fiche d'identité
séparée, sourcée par Flora of New Zealand, a donc été ajoutée via l'importeur
existant, avec la collection « Mes plantes — Sophora prostrata ». Aucun cultivar,
gabarit ou besoin cultural n'est déduit. Le catalogue réel atteint maintenant
**9288 fiches** ; la recherche exacte et la collection à une entrée sont vérifiées.
Les sauvegardes des bibliothèques et collections sont présentes. L'utilisateur a
confirmé la fermeture ; l'installation avait aussi vérifié l'absence du processus.

## Catalogue dans les menus et lot de visuels (19 octobre 2026, ADR-063)

Demande : retrouver les nouvelles fiches dans les catégories du haut, avec un
maximum de visuels 2D. Le propriétaire a validé la planche des 138 nouveaux dessins
avant intégration. Le registre expose désormais 246 illustrations et 15 formes
génériques, toutes régénérables sans ressource externe.

- Recherche/pagination des fiches dans les quatre catégories botaniques, 48 lignes
  maximum ; recherche globale limitée à 12 résultats avec priorité des noms/alias
  exacts. Les fiches complètes passent au placement en un clic ou au glisser-déposer.
- Copie isolée du catalogue privé : 9 288 fiches, dont 1 880 classables comme arbres,
  2 024 comme arbustes, 1 216 fleurs/vivaces, 215 potagères/aromatiques et 3 953 sans
  type connu. Ces dernières restent dans All plants sans inventer leur strate.
- Mesure hors écran sur cette copie : snapshot froid 0,539 s, ouverture de la palette
  d'arbustes 0,061 s avec 48 lignes, recherche Sophora 0,009 s avec une fiche exacte.
  Les 138 nouveaux dessins ne sont pas 138 nouvelles identifications botaniques.
- 1 621 contrôles ciblés initiaux réussis (conformité/rendu de tous les SVG, anciens
  menus, listes et dimensionnement). Revue indépendante : deux P1 reproduits puis
  corrigés (MIME non fini bloquant une sauvegarde ; résultat exact exclu par le top12).
  Les 32 contrôles métier dédiés passent après correction. Quatre cas typographiques supplémentaires
  mesurent tous les libellés des 11 catégories à 13 pt EN et 18 pt DE, clair/sombre ; les 67 contrôles combinés
  catalogue/catégories/popups passent avec la correction finale de lisibilité.
  Suite complète du snapshot métier terminée ; détail ci-dessous.
- Typer les deux nouveaux modules n'ajoute aucune erreur ; total strict global
  inchangé à 1 928 diagnostics hérités, sans hausse par fichier. Traductions EN/DE
  compilées avec 2 731 clés ; ruff, parité des contextes et sources wiki passent.

Aucune écriture du catalogue privé ni manipulation de l'application native ouverte.
La validation du style est acquise ; le nouveau parcours natif reste à essayer
après fermeture et relance. PR personnelle conservée en brouillon, sans merge/release.

Le contrôle des 9 288 fiches réelles par la nouvelle frontière de validation
réussit intégralement (1,003 s sur copie isolée). Les nouveaux minima de libellés
sont fondés sur les dimensions du QLabel, pas seulement du bouton parent.

### Bilan final des vérifications du catalogue

La suite complète du snapshot métier corrigé termine avec **9 733 réussites,
46 skips, 2 tests CLI incompatibles Darwin désélectionnés, 2 échecs bruts**, en
1 151,79 s. L'un reste le round-trip DXF à largeur nulle déjà reproduit sur
l'amont inchangé 603e303. L'autre est un **KeyboardInterrupt envoyé par l'agent**
et capturé par pytest-qt dans combo_wheel_guard.eventFilter pendant le contrôle
d'inactivité avec overlay : ce n'est pas une assertion de boucle au repos. La
relance propre des sept contrôles d'inactivité réussit en 27,20 s. Le résultat brut
et l'interruption restent dans le bilan ; aucun test n'est retiré pour les masquer.

Le seul fichier Python de production changé après ce snapshot complet est
CategoryDropdown : le dernier correctif de dimensions de vignettes est validé
séparément par **67 contrôles UI réussis**, puis par les **4 cas typographiques
exhaustifs sur les 11 catégories** (13 pt EN / 18 pt DE × clair / sombre).
La revue indépendante finale confirme la résolution des P1 de confiance, de
classement et de confinement des libellés, sans autre P0/P1. Ruff, Bandit HIGH,
traductions 2 731 clés, parité/citations des skills, secrets et sources wiki passent.
Mypy demeure à 1 928 diagnostics hérités, sans hausse par fichier.

Manuel restant : dans l'application relancée, rechercher Sophora prostrata depuis
Arbustes, placer d'un clic, annuler/rétablir puis enregistrer/rouvrir. Essayer
All plants, pagination et dernière silhouette avec la taille de fenêtre habituelle.
Aucun merge, tag, release ou publication amont.

### Dessins personnels et contrôles ciblés — 19 octobre 2026

À la demande du propriétaire, les changements courants utilisent des vérifications
ciblées ; la suite amont complète reste réservée aux changements de fond ou aux
audits explicites. Les tests hérités sont conservés. Aucun nouvel import massif
ni retrait du catalogue personnel n'est effectué.

Plant Details propose l’import PNG, le retour à la silhouette standard et la
mémorisation en bibliothèque. Le plan emporte l’image ; identité et géométrie
restent inchangées. Contrôles ciblés et revue indépendante validés. Le Rosa Ketchup
& Mustard utilise le PNG fourni, avec sauvegarde et conservation des 9288 fiches.
Les dessins restent privés. Voir ADR-064 et PLANTES_ET_DESSIN.

## Nettoyage Git, tests et consignes agents — 20 octobre 2026

Les 19 commits du fork étaient déjà poussés et le dossier principal propre.
L'historique publié est conservé ; ce nettoyage ajoute un commit cohérent et
actualise la PR personnelle brouillon existante, sans merge ni release.

Les sept anciens worktrees de revue/validation ont été retirés après vérification
de leur contenu et de l'absence de processus de test associé. Quatre contenaient
des copies modifiées des travaux précédents : leurs fichiers modifiés/non suivis,
les diffs binaires et les commits de base sont conservés dans des archives privées
sous `.local/worktree-backups/2026-10-20/`. Le manifeste SHA-256 et les fichiers
ont été vérifiés avant retrait. Les trois autres ne contenaient que des caches.
Il reste uniquement le worktree principal. Aucune bibliothèque personnelle modifiée.

`AGENTS.md` et `CLAUDE.md` passent de 53 947 à 5 793 octets chacun. Le guide place
le mandat macOS, les protections des données et la validation proportionnée au
premier plan. Les 23 descriptions de skills sont resserrées ; trois procédures
transversales sont réécrites, les références techniques conservées et les anciens
renvois vers le guide corrigés. Les deux reviewers utilisent une revue factuelle,
centrée sur le diff, sans posture hostile ni suite complète automatique. Les
métadonnées YAML héritées non reconnues par Codex sont retirées. Le budget de
contexte local revient à 32 Kio. Voir `AGENT_WORKFLOW.md` et ses sources OpenAI.

Le profil facultatif `pytest-macos.ini` réutilise cinq fichiers, soit **79 cas**.
La première exécution donne **78 réussites et un refus sandbox de socket locale**
en 96,24 s ; le seul test MCP réel est rejoué avec loopback autorisée et passe en
0,93 s (deux avertissements de dépréciation du SDK). Ces résultats ne constituent
pas une exécution exhaustive. Les **46 contrôles documentaires existants** passent,
ainsi que ruff, parité des contextes, citations et validation des 23 skills.
Revue indépendante du diff de nettoyage et relecture des derniers ajustements :
aucun P0/P1. Aucun nouveau test créé, aucune régression héritée supprimée.

La suite complète reste accessible pour les changements de fond et les audits.
Le profil court et la sélection par risque remplacent son lancement systématique.
Pas de nouvelle vérification native nécessaire pour ce changement documentaire ;
les essais des fonctionnalités précédentes restent ceux décrits plus haut.

## Codex autonome et preuves techniques — 20 octobre 2026

À la demande du propriétaire, remplacement de la politique de symétrie Claude/Codex
par un unique AGENTS.md orienté fork léger et GPT-6.1 Sol. Suppression des miroirs
Claude suivis, livraison limitée à la draft PR du fork, revue indépendante selon
le risque. Réécriture des workflows de livraison/revue/setup ; création du skill
`ogp-extension-design`. Cinq historiques volumineux deviennent des références
chargées à la demande. Documentation et sources : `AGENT_WORKFLOW.md`.

Les contrôles de contexte/citations et leurs tests suivent l’arbre Codex unique.
Les tests applicatifs restent inchangés ; les assertions de parité supprimées
concernaient exclusivement les miroirs retirés. Aucun modèle global ni permission
système reconfiguré, aucune nouvelle dépendance applicative.

Validation de cette refonte : 140 tests distincts passent, 2 cas de commandes
Windows documentaires sont sans objet (aucune commande restante à paramétrer).
Contexte, citations, liens déplacés, format des 24 skills et Ruff global passent.
Les 7 échecs initiaux des canaris de secrets provenaient du staging Git bloqué
par le sandbox ; les 13 tests de ce détecteur passent avec accès à l’index.
Revue indépendante du diff puis des corrections : aucun P0/P1 résiduel.
Pas de suite applicative ni de lancement GUI pour ce changement de consignes/outillage.
