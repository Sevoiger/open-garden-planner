# Évolutions amont examinées le 29 octobre 2026

Base du fork : `v1.30.10` (`603e3035`), puis adaptations personnelles jusqu’à
`29801908`. Références amont actualisées le 29 octobre : tags jusqu’à `v1.34.5`
(`56b82f3f`), master `93c9d500`. La version affichée du fork reste inchangée :
il s’agit de ports sélectifs, pas d’une migration complète vers 1.34.

## Changements récupérés

Le lot 2D [#556](https://github.com/cofade/open-garden-planner/pull/556),
commit `3714842e`, fournit les trois corrections suivantes, adaptées aux points
d’entrée existants du fork :

- **Propriétés des bacs et pots** : grille, espacement, profondeur de terre,
  matériau, drainage, hauteur et volume de terre deviennent des commandes
  annulables. La pile partagée signale le plan modifié. Undo restaure une clé
  initialement absente comme absente ; redo réapplique la valeur. Les listes et
  cases affichent le résultat même lorsqu’elles ont le focus. Les champs de
  saisie gardent leur protection pendant une édition.
- **Sélection avec Maj** : recherche du premier objet sélectionnable sous un
  objet verrouillé ; le clic ajoute ou retire cet objet de la sélection sans
  effacer les autres. Les poignées conservent leurs événements Qt.
- **Illustrations végétales** : correspondance par nom normalisé et mots entiers,
  puis repli sur la catégorie. « melon » ne correspond plus à « melongena », et
  un nom vide ne correspond plus à la rose. Le catalogue enrichi personnel reste
  en place. Ses alias explicites restent prioritaires. La règle amont conserve
  des ambiguïtés sémantiques (par exemple un nom contenant un autre nom complet) ;
  aucune nouvelle identité botanique n’est inférée.

Les libellés des sept commandes sont traduits en français et allemand ; les trois
catalogues enregistrés (EN/DE/FR) sont compilés et contrôlés. Pas de nouveau format
`.ogp`, de remplacement des réglages ni de modification des données personnelles.

## Comparaison et décisions

| Évolution amont | Décision pour ce lot |
| --- | --- |
| 1.30.11 : dépendances et accélération des tests (#508/#510) | Pas de remplacement global des verrous ni des fixtures ; cela inclut des choix CI Windows et des changements à recroiser avec notre isolation MCP. |
| 1.30.12 : langue espagnole (#518) | Non ajoutée : notre contrat actuel enregistre EN/DE/FR et nos extensions ont leurs propres textes. |
| 1.30.14 puis 1.31/1.32/1.34 : Qt Quick 3D, analyse, Plant Forge et monde bâti (#484/#584/#593/#613) | Migration différée : remplacement du moteur, du workspace et de nombreuses interfaces ; ce n’est pas un correctif isolé compatible avec notre vue Qt3D actuelle. |
| 1.30.16 : timeline et soleil combinés (#533), puis navigation chronologique (#565) | Différé avec le workspace : les correctifs de navigation dépendent de cette nouvelle barre. |
| Correctifs de style des lignes (#545), hauteur estimée et verrouillage (#566) | Restent à porter dans un lot dédié : ils s’appuient sur les extractions et contrats introduits par le nouveau moteur. Ne pas recopier des fichiers entiers sur nos adaptations. |
| 1.31.1 : booléens et préférences (#585) | La géométrie cercle/ellipse, l’union multiple et les dialogues défilants sont déjà présents dans le fork. Les extensions amont supplémentaires (contacts ponctuels, limites des polygones denses, plafonnement des styles) ne sont pas revendiquées ici. |
| 1.32.1 : durcissement non 3D (#596) | Les modifications de géométrie, mise en page, lancement et indexation des verrous sont à reprendre séparément : le code amont cible aussi le nouveau workspace. |
| 1.33 : exemples hors ligne et modèles portables (#610) | Différé : touche chargement, chemins et stockage des modèles ; à confronter à nos parcours personnels de reprise du plan et dossier projet. |
| 1.34.1/2 : durcissement 3D (#630/#632) | Dépend du moteur Quick 3D absent du fork. |
| 1.34.3/4 : soleil 2D, exposition sur supports, atomicité du déplacement agent (#627/#639) | À examiner lors d’un lot soleil/MCP : changements couplés au nouveau modèle de scène et à des frontières réécrites dans notre fork. |
| 1.34.5 : refonte des paquets d’audit (#640) | Pas de reprise globale d’une refonte transversale ; conserver nos frontières métier/Qt et protections de persistance. |
| Installateur Windows, statistiques de téléchargements, CI, synchronisations de version et consignes agents | Hors du besoin macOS ; nos consignes locales restent la référence. |

Ce tableau distingue les apports récupérés des évolutions encore disponibles. Il
ne certifie pas que tous les défauts corrigés en amont ont disparu du fork.

## Vérification

Les régressions ajoutées échouent avant le portage sur les trois comportements
concernés. Les tests pilotent les contrôles du panneau et les événements du
viewport, avec rotations et undo/redo. Données et réglages sont isolés par les
fixtures ; aucun plan personnel n’est ouvert. La suite étendue donne 494 réussites et quatre échecs identiques sur la base
initiale : trois fixtures de `PlantMeasurementsDialog` sans arguments et un
constructeur `QDialog` de `reference_points.py` signalé par le garde des dialogues.
Le roundtrip ajouté et les six cas supplémentaires de repères passent. Sous Cocoa
natif, les 25 premiers cas panneau/sélection passent, puis les 23 cas de sélection
passent après correction des raccords de repères (ces campagnes se recouvrent).
Le premier essai Cocoa dans le sandbox a avorté dans le menu natif ; le nouvel
essai hors sandbox passe. Ruff sur le diff et le contrôle des traductions passent.
La revue senior indépendante a détecté deux raccords de sélection des repères ;
après correction et relecture, aucun P0/P1/P2 restant dans le périmètre revu.
