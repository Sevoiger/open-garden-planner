# Audit technique du fork — 23 octobre 2026

## Périmètre et méthode

Comparaison de la base amont locale `603e303` (`upstream/master`) avec l’état
initial `e80a4ff` : 43 commits personnels et 113 fichiers Python applicatifs
modifiés, auxquels s’ajoutent tests, ressources et outils. Revue des parcours
modifiés : démarrage/récupération, persistance et saisons, import/catalogue,
géométrie et contraintes, matériaux, panneaux et dialogues macOS, météo,
orientation et frontières MCP. Les changements purement répétitifs de dialogues
et traductions sont également couverts par leurs contrôles transversaux.

Les vérifications utilisent des plans fictifs, des bibliothèques temporaires et
des réglages isolés. Aucun plan personnel ni bibliothèque utilisateur n’a servi
de jeu d’essai. Les changements déjà présents dans le dépôt au début de l’audit
sont exclus de la livraison. L’audit ne démontre pas l’absence de tout défaut.

## Défauts corrigés

| Risque | Déclencheur et conséquence constatée | Correction |
| --- | --- | --- |
| P1, liens de suivi rompus | Fusion/intersection/soustraction d’une forme utilisée par une intervention, une analyse de sol ou une fiche : remplacement de son UUID, données devenues orphelines. | Réutilisation de la protection de remplacement existante ; nouvelle vérification après la confirmation modale. |
| P1, appartenance de zone rompue | Découpe d’un massif désigné uniquement par `garden_tracking.zone_id` sur une autre plantation : le massif disparaissait. | Recherche des références entrantes de fiches et des liens de faîtage, même sans service de projet accessible depuis la vue. |
| P1, déplacement à la réouverture | Qt ajoute une translation compensatrice lorsqu’une surface tournée rejoint un groupe. L’enregistrement ignorait cette partie de la transformation. | Absorption de la translation effective dans les coordonnées sauvegardées des rectangles, cercles, ellipses et polygones. |
| P2, matériaux instables | Fusion d’une surface tournée : changement de repère du matériau et réapparition d’une étiquette masquée. Après déplacement, sauvegarder puis rouvrir décalait aussi la texture. | Conservation du repère source, du style et de la visibilité ; compensation de l’origine lors de la sérialisation. |
| P2, accumulation UI | Chaque ouverture des données jardin gardait le dialogue fermé et son abonnement aux changements du projet. Les menus vides du canevas s’accumulaient également. | Déconnexion explicite et destruction différée des objets Qt temporaires. |
| P2, latence du suivi | Une fiche de zone et un export CSV reparcouraient le plan pour chaque plantation : croissance quadratique du travail. | Projection des fiches en un parcours, partagée avec l’export. |
| P2/P3, interface nord | Le dialogue d’orientation échappait à la politique de confinement des formulaires ; l’action de menu n’avait pas d’icône. | Adoption de `ResponsiveDialog`, couverture du dialogue dans la matrice UI, icône existante. |

Les régressions initiales ont reproduit les ruptures de liens et les abonnements
résiduels avant correction. Les contrôles suivants ont également révélé les
défauts de matériau, de sauvegarde et de groupement ; ils passent après correction.

## Persistance des matériaux

Le format géométrique et sa version restent inchangés. Les champs internes
additifs `metadata.fill_transform.origin_x_cm` et `origin_y_cm` compensent
l’absorption de la translation Qt dans les coordonnées du fichier. Ils sont
appliqués après rotation, échelle et décalages du matériau. Les décalages saisis
dans l’interface restent inchangés ; les métadonnées vivantes ne sont pas modifiées
par la sauvegarde. Une seconde sauvegarde et un nouveau déplacement ne doivent
pas appliquer la compensation deux fois.

Les maisons possédant un faîtage réellement résolu gardent leur ancrage dérivé du
faîtage. Un lien de faîtage orphelin conserve le remplissage standard et reçoit la
compensation normale. Un test de rendu compare les pixels d’une toiture tournée
et déplacée avant et après réouverture.

Une ancienne application ignore ces nouvelles origines : elle peut décaler le
motif, mais les coordonnées du contour restent lisibles. La normalisation des
translations de groupe concerne les quatre surfaces ci-dessus ; elle n’étend pas
les sérialiseurs historiques des textes, polylignes et courbes aux transformations
affines générales.

## Mesures de performance

Mesure locale sur Apple Silicon, Python 3.11.17 / Qt 6.11.0, avec une zone et
des plantations fictives. Temps indicatifs d’un passage, hors démarrage et IO
personnelles ; il ne s’agit pas d’un budget garanti.

| Objets | Fiche de zone avant → après | Export CSV avant → après |
| --- | --- | --- |
| 2 000 | 351 ms → 2,1 ms | 362 ms → 6,6 ms |
| 5 000 | 2 133 ms → 5,3 ms | 2 157 ms → 15,6 ms |

Le nombre de membres est identique. Pour chaque taille, l’empreinte SHA-256 du
CSV avant/après est identique ; à 5 000 objets :
`b9f5de2ad791a69d26d1891e416ad1c74eba5f7765d891997c1876834cbacf80`.

## Vérifications et revue

- Campagne large lancée avant les corrections : **9 970 réussites, 48 skips,
  14 échecs**, sur 10 032 cas en 1 184,43 s. Son résultat brut reste distinct
  de la validation finale ; il ne constitue pas une suite complète verte du diff.
- Validation finale des zones affectées : **547 tests distincts réussis** :
  374 persistance/géométrie/matériaux/MCP, 139 UI/orientation, 33 menus/popups/
  fixtures, puis un contrôle de rendu de toiture ajouté après identification
  du risque particulier de son ancrage. Les 23 nouveaux cas comprennent les
  sauvegardes répétées, déplacements successifs, groupes, grandes coordonnées,
  PNG personnel, annulation/rétablissement et protection des références.
- Ruff global, cohérence des dépendances installées (`pip check`) et gate des
  traductions sources/catalogues compilés : réussis. Aucun nouveau texte UI.
- Revue indépendante `senior-reviewer` du diff de correction, puis de la
  correction de persistance : P1 initial du matériau résolu ; aucun autre
  P0/P1 identifié. Composition des transformations corroborée avec Qt.

Huit des quatorze échecs initiaux sont résolus et leurs contrôles relancés.
Trois tests historiques ont été adaptés à des comportements déjà introduits par
le fork : une sélection de plante copiée pour porter son option de taille,
l’émission réelle de `QAction.triggered` dans un menu, et l’entrée descriptive
désactivée du PNG personnel en plus des motifs standards. Les deux fixtures de
benchmark déclarent maintenant le nord par défaut. Aucune assertion métier
n’a été retirée pour masquer un défaut applicatif.

## Échecs résiduels et limites

| Contrôle | Constat et suite utile |
| --- | --- |
| `test_dxf_round_trip_preserves_position` | Défaut applicatif hérité déjà documenté dans `PROGRESSION.md` : les abscisses du rectangle réimporté sont toutes nulles au lieu d’une largeur de 300 cm. Non corrigé dans ce lot ; éviter de considérer l’aller-retour DXF comme fiable. C’est le point applicatif prioritaire restant dans cette campagne. |
| Deux tests CLI mypy de `test_ci_quality_workflows.py` | Le script de baseline exige explicitement Linux/Windows ; le lancement sous Darwin échoue avant le scénario attendu. Aucun élargissement de plateforme ou modification de baseline de CI dans cet audit. |
| Deux tests CLI de verrou/version | `uv` disponible est en version 0.5.30, alors que le projet impose 0.12.23. Ces contrôles d’outillage ne prouvent pas un défaut du logiciel lancé depuis les sources. |
| `test_real_corpus_citations_all_resolve` | Neuf références de lignes devenues obsolètes dans les skills et leurs historiques. Le script les énumère ; cette dette documentaire préexistait à l’audit. |

Les tests Qt utilisent le backend hors écran. Ils vérifient les coordonnées,
signaux, dimensions et pixels, sans établir l’acceptation visuelle native macOS.
Les alertes de dépréciation MCP constatées concernent le nom de son client HTTP.
La cohérence des paquets installés ne remplace pas un audit externe des avis de
vulnérabilité. Aucun merge, release ou changement des données personnelles.
