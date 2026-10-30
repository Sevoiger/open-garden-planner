# Évolutions amont examinées le 29 octobre 2026

Base du fork : `v1.30.10` (`603e3035`), puis adaptations personnelles jusqu’à
`29801908`. Références amont actualisées le 29 octobre : tags jusqu’à `v1.34.5`
(`56b82f3f`), master `93c9d500`. La version affichée du fork reste inchangée :
il s’agit de ports sélectifs, pas d’une migration complète vers 1.34.

## Choix du propriétaire — 30 octobre 2026

Conserver l’interface actuelle et le moteur Qt3D du fork. Le nouveau workspace
amont (modes Plan, 3D et Split côte à côte), la migration Qt Quick 3D et la barre
combinant timeline et simulation du soleil ne répondent pas au besoin personnel
actuel. Ils ne constituent donc pas une migration en attente. Ne pas les proposer
à nouveau à chaque contrôle ; réexaminer ce choix seulement sur demande du
propriétaire ou si une incompatibilité concrète affecte le fonctionnement conservé.

Continuer les ports sélectifs de correctifs utiles au dessin, aux données du jardin
et à l’exécution macOS. Une dépendance au nouveau workspace n’est pas, à elle seule,
une raison de migrer : chercher d’abord si le défaut existe dans le fork et si sa
correction peut être adaptée à nos points d’entrée.

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
| 1.30.14 puis 1.31/1.32/1.34 : Qt Quick 3D, analyse, Plant Forge et monde bâti (#484/#584/#593/#613) | Migration non retenue, choix confirmé le 30 octobre : conserver l’interface et Qt3D actuels. Les correctifs propres au nouveau moteur ne sont pas à récupérer. |
| 1.30.16 : timeline et soleil combinés (#533), puis navigation chronologique (#565) | Nouvelle barre non souhaitée, choix confirmé le 30 octobre. N’adapter un correctif de navigation que si le même défaut est reproduit dans nos contrôles actuels. |
| Correctifs de style des lignes (#545), hauteur estimée et verrouillage (#566) | Candidats à évaluer séparément : ils s’appuient sur les extractions et contrats introduits par le nouveau moteur. Reproduire le défaut dans le fork avant de décider d’un port ; ne pas recopier des fichiers entiers sur nos adaptations. |
| 1.31.1 : booléens et préférences (#585) | La géométrie cercle/ellipse, l’union multiple et les dialogues défilants sont déjà présents dans le fork. Les extensions amont supplémentaires (contacts ponctuels, limites des polygones denses, plafonnement des styles) ne sont pas revendiquées ici. |
| 1.32.1 : durcissement non 3D (#596) | Les modifications de géométrie, mise en page, lancement et indexation des verrous sont à reprendre séparément : le code amont cible aussi le nouveau workspace. |
| 1.33 : exemples hors ligne et modèles portables (#610) | Différé : touche chargement, chemins et stockage des modèles ; à confronter à nos parcours personnels de reprise du plan et dossier projet. |
| 1.34.1/2 : durcissement 3D (#630/#632) | Dépend du moteur Quick 3D absent du fork. |
| 1.34.3/4 : soleil 2D, exposition sur supports, atomicité du déplacement agent (#627/#639) | À examiner lors d’un lot soleil/MCP : changements couplés au nouveau modèle de scène et à des frontières réécrites dans notre fork. |
| 1.34.5 : refonte des paquets d’audit (#640) | Pas de reprise globale d’une refonte transversale ; conserver nos frontières métier/Qt et protections de persistance. |
| Installateur Windows, statistiques de téléchargements, CI, synchronisations de version et consignes agents | Hors du besoin macOS ; nos consignes locales restent la référence. |

Ce tableau distingue les apports récupérés des évolutions encore disponibles. Il
ne certifie pas que tous les défauts corrigés en amont ont disparu du fork.

## Point de reprise pour le prochain contrôle

La borne examinée est **`v1.34.5`**, commit `56b82f3f` ; le master amont observé
était `93c9d500` (synchronisation de version). Ces références datent du 29 octobre ;
elles ne prétendent pas désigner la dernière version disponible après cette date.
Le port livré dans le fork est `391f28e5`,
[draft PR #40](https://github.com/Sevoiger/open-garden-planner/pull/40).

Au prochain passage :

1. Actualiser les références et les releases amont, puis examiner les changements
   après cette borne (`v1.34.5..upstream/master`), en distinguant les releases des
   commits non encore publiés. Ne pas considérer les tags amont comme intégrés.
2. Appliquer les choix ci-dessus sans rouvrir la migration d’interface. Comparer
   les correctifs candidats au code réel du fork et reproduire les défauts avant
   portage ; préserver les adaptations personnelles.
3. Garder comme candidats antérieurs non intégrés les corrections de lignes et
   hauteurs (#545/#566), les compléments booléens/polygones denses (#585), le
   durcissement non 3D (#596) et les correctifs soleil/MCP (#627/#639). Leur
   pertinence reste à établir : ce relevé n’est pas une validation de ces lots ni
   une obligation de tous les reprendre. Les exemples/modèles (#610) restent une
   option à évaluer selon un besoin concret.
4. Mettre à jour cette borne, les décisions et les ports effectivement livrés dans
   ce document ; résumer le résultat dans `PROGRESSION.md`.

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
