# Plantes classiques, dessin et cercles

## Catalogue dans les catégories du haut

Les menus **Trees**, **Shrubs & Hedges**, **Flowers & Perennials** et
**Vegetables & Herbs** proposent maintenant les fiches du catalogue local,
avec recherche, filtres et pages de 48 plantes. **Previous plants / Next plants**
changent de page. **All plants (including unclassified)** donne accès à toutes
les fiches, y compris celles dont le type n'est pas renseigné.

Choisir une fiche, puis cliquer une fois sur le plan pour la placer ; le
glisser-déposer fonctionne aussi. La fiche botanique complète, sa provenance et
son identité sont conservées. L'emprise initiale utilise l'envergure connue
(plafonnée à 50 m pour le dessin) ou la taille générique habituelle. Annuler puis
rétablir conserve le même sujet. **Search plant databases…** ouvre la recherche
existante avec les fournisseurs configurés ; cliquer **Search online** dans cette
fenêtre pour interroger les API. La confirmation prépare un nouveau placement.

La section **Illustrations and generic shapes** propose séparément les dessins :
246 illustrations de plantes et 15 formes génériques, dont 138 nouveaux dessins
validés le 19 octobre. Ces raccourcis gardent le dessin centre puis bord du cercle.
Cette section est repliée à l'ouverture : cocher son intitulé pour afficher les
dessins. Les fiches botaniques restent visibles en premier. Choisir une fiche
prépare un placement ; **Plant Details** décrit la sélection du plan. Une plante
sans fiche affiche le message d'absence de données, sans conserver le profil de
la plante précédente.
Les dessins sont conventionnels : ils ne prouvent ni une espèce ni une variété,
et leurs couleurs ne décrivent pas nécessairement votre cultivar. Une fiche sans
dessin spécifique utilise une silhouette générique. La recherche globale de la
barre trouve aussi les fiches locales et privilégie les résultats botaniques.

## Bibliothèques disponibles

OGP contient déjà 118 fiches botaniques hors ligne et 11 catégories d'objets dans
la barre du haut. Une silhouette générique (ou un cercle) n'identifie pas une espèce.
Le message « No species data » signifie que cet objet n'a pas encore de fiche
botanique associée. **Search** permet de chercher dans le catalogue local puis,
si nécessaire, en ligne ; **Create Custom** définit une fiche personnelle.

Le pack ajoute quatre listes réutilisables, avec neuf plantes chacune :

- **Classiques — arbres et fruitiers** : pommier, cerisier, poirier, prunier,
  figuier, olivier, chêne, bouleau, magnolia.
- **Classiques — arbustes et grimpantes** : rosier, hortensia, lilas, forsythia,
  buis, framboisier, myrtillier, clématite, glycine.
- **Classiques — fleurs et bulbes** : lavande, pivoine, iris, tulipe, narcisse,
  crocus, échinacée, dahlia, lis.
- **Classiques — potager et aromatiques** : tomate, laitue, carotte, radis,
  fraisier, thym, romarin, basilic, ciboulette.

Les noms scientifiques et données restent ceux du catalogue embarqué, avec leur
précision et leurs inconnues. Le pack français remplace les noms usuels affichés par leurs équivalents français ;
les noms botaniques restent inchangés. Cette sélection facilite la prise en main ; elle ne garantit
pas l'adaptation des plantes à votre climat, sol ou exposition.

Ouvrir **Plant Lists** dans la colonne de droite, choisir une collection dans son
sélecteur puis glisser une plante vers le plan. Les listes sont communes aux
projets. L'import ne place aucune plante dans un jardin et ne transforme pas les
cercles déjà dessinés en espèces supposées.

## Import automatisé et fichiers portables

Fermer l'application avant l'import pour éviter qu'une bibliothèque en mémoire
écrase les ajouts. Depuis le projet :

```bash
.venv/bin/python scripts/import_classic_plants.py
```

Le stockage habituel sur ce Mac est
`~/Library/Application Support/OpenGardenPlanner/plant_lists.json`. Avant le
premier changement d'un fichier existant, ses octets sont sauvegardés dans un
fichier voisin `plant_lists.before-classics-<date>.json`. Le service réutilise
l'importeur atomique existant ; un fichier illisible ou d'une version non prise
en charge est refusé sans écrasement. Une interruption peut laisser certaines
collections déjà installées ; une relance termine celles qui manquent.

La relance de la commande reconnaît les descriptions de ces collections : elle
ne crée pas de doublons et conserve renommages, notes et retraits d'entrées.
L'import n'est pas exécuté automatiquement à chaque démarrage. Une suppression
de collection reste donc effective jusqu'à une nouvelle demande d'import.

Les quatre fichiers `examples/plant-lists/classiques-*.json` utilisent le format
standard de **Plant Lists > menu > Import**. Cet import manuel attribue de nouveaux
identifiants et peut créer une autre copie ; la commande automatisée évite cela.

## Premier jet depuis un dessin

Un croquis, une photo de plan ou un scan permet de préparer un `.ogp` éditable :
image de fond, limites, massifs et objets sur des calques séparés. Il faut fournir
le dessin et au moins une longueur réelle connue pour l'échelle. Une deuxième
longueur permet de contrôler la calibration ; une photo en perspective peut
nécessiter une rectification. Le nord est utile si des études d'ensoleillement
sont souhaitées.

Les contours approximatifs et positions non vérifiées doivent rester signalés.
Les noms illisibles restent « à identifier », les dates inconnues restent inconnues,
et un projet de plantation reste prévu. Le premier jet ne constitue pas un relevé
géomètre. Placer les sources privées dans `sources-jardin/` et les résultats dans
`jardin-personnel/`, exclus de Git. Aucun dessin réel n'a encore été fourni ici.

## Comprendre un cercle

Avec la souris : premier clic = centre, deuxième clic = point sur le bord.
La distance entre ces deux points est le rayon. Il n'est pas nécessaire de saisir
un angle pour dessiner un cercle de cette manière.

Après avoir fixé le centre, `150<45` ou `@150<45` signifie **rayon de 150 cm,
angle de 45 degrés**. Le signe `<` sépare la distance de l'angle ; ce n'est pas
une comparaison « inférieur à ». Le cercle a donc un diamètre de 300 cm, soit
3 mètres. Pour un diamètre de 2 mètres, utiliser un rayon de 100 cm.

Dans les propriétés, **Diameter** règle directement le diamètre en centimètres.
Les champs **Position X / Y** règlent la position, pas les rayons. Pour une plante,
l'emprise dessinée et son cercle d'envergure future sont des informations distinctes.


## Catalogue français et plantes sans variété

Le pack français expose les 118 fiches embarquées dans six collections **Catalogue FR**,
et ajoute 80 fiches de départ dans quatre collections **À préciser** : vivaces/fleurs,
arbustes/grimpantes, graminées/couvre-sols, arbres. Il s’agit de noms de groupes
usuels, pas de 80 nouvelles espèces identifiées. Par exemple **Sauge — variété à
préciser** ne suppose ni Salvia officinalis ni une variété de sauge arbustive.
Les dimensions, le cycle et l’exposition restent inconnus. Les données des espèces
identifiées restent celles du catalogue embarqué, sans ajout horticole inventé.

Dans **Plant Lists**, choisir une collection, puis glisser la fiche sur le plan.
Sélectionner la plante et compléter **Plant Details > Variety** quand le cultivar
est connu. Régler manuellement l’emprise dessinée si la fiche générique ne donne
pas de dimension. Ce champ appartient à cette plantation ; il ne renomme pas toutes les
sauges de la bibliothèque. Pour identifier précisément une autre espèce, utiliser
**Search** ou compléter son nom botanique. Les recherches françaises et les anciens
noms anglais des fiches embarquées restent disponibles. Les 36 noms du précédent
pack Classiques passent aussi en français, à l’exception des noms déjà personnalisés.

## Importer un dossier de fiches JSON

Fermer l’application après avoir enregistré, puis lancer :

```bash
.venv/bin/python scripts/import_local_plants.py plants/import01 --french-catalog
```

Le dossier import01 fourni contient 37 fiches JSON et leurs PDF. Les fiches sont
ajoutées à la recherche hors ligne et à **Mes plantes — import01**. Avec les 118
fiches françaises et les 80 fiches génériques, cela représente 235 fiches dans le
pack, dont des groupes non identifiés. Les fiches personnelles déjà présentes
s’ajoutent à ce nombre. Les collections peuvent présenter une
même fiche à plusieurs endroits ; ce ne sont pas autant de plantes sur le plan.

Les noms et mesures explicites sont repris : mètres vers centimètres, nom botanique
et nom usuel français. Tous les champs source, l’URL et l’empreinte du fichier restent
conservés dans les données de provenance. Les JSON sont la source de l’import ; les
PDF d’origine restent à côté. Les périodes textuelles et les listes de mois peuvent
se contredire : elles sont conservées sans créer de dates ni de tâches supposées.
Une rusticité en degrés Celsius n’est pas convertie arbitrairement en zone USDA.
Une exposition multiple reste dans les données source plutôt que d’être réduite
à une seule préférence présentée comme certaine.

Les deux stockages, custom_plants.json et plant_lists.json, sont validés et sauvegardés
avant modification. Chaque remplacement de fichier est atomique ; si l’import est
interrompu entre les deux, le relancer termine l’installation. La relance n’écrase
pas les fiches, notes, renommages ou retraits déjà personnalisés. Un nouveau lot doit
porter un autre nom de dossier ou un **--collection-key** distinct. Les données
personnelles dans plants/ et les documents de sortie dans output/ restent hors Git.

## Une plante absente du fournisseur

Une clé API donne accès au catalogue du fournisseur, sans garantir toutes les
espèces ou variétés. Vérification du 19 octobre 2026 : la recherche Permapeople
répond correctement pour `Sophora`, `Sophora prostrata` et la coquille
`Sophora protrata`, mais ses réponses ne contiennent pas `Sophora prostrata`.
Le nom botanique correct est confirmé par
[Flora of New Zealand](https://www.nzflora.info/factsheet/taxon/Sophora-prostrata.html).
Ne pas remplacer cette plante par une autre espèce de Sophora renvoyée par la
recherche. Une fiche personnalisée reste possible ; une variété horticole ne doit
être ajoutée que si elle est connue. Les autres fournisseurs peuvent compléter
la recherche, sans garantie d'exhaustivité.

L'installation personnelle du 19 octobre 2026 dispose désormais de **9288 fiches
hors ligne**, dont 295 variétés issues de Permapeople. Les noms français existants
sont conservés ; les espèces supplémentaires utilisent leur nom botanique, avec
le nom anglais source disponible à la recherche. Les conditions ambiguës restent
à préciser. Les crédits, liens d'origine et licence CC BY-SA 4.0 accompagnent les
fiches Permapeople. Le téléchargement est local et privé, avec sauvegardes ; il
ne devient pas un catalogue embarqué pour les autres installations.

Une fiche **Sophora prostrata** est aussi disponible dans la recherche et dans
**Plant Lists > Mes plantes — Sophora prostrata**. Elle confirme l'identité
botanique ; préciser soi-même la variété et les dimensions connues.

## Mes propres dessins de plantes

Sélectionner une plante déjà associée à une fiche, puis ouvrir **Plant Details**.
Dans la partie défilante, **Import personal PNG…** permet de choisir un dessin vu
du dessus, idéalement sur fond transparent. L'image conserve ses proportions et
ses couleurs ; elle remplace la silhouette du plan et la miniature de la fiche.
Les dimensions, espacements et informations botaniques restent identiques.

**Remember visual in library** mémorise ce dessin pour les prochains placements
de cette même fiche, y compris depuis les catégories du haut. C'est une préférence
de bibliothèque : les plantes déjà posées conservent leur propre version.
**Use default illustration** rétablit la silhouette standard pour la plante
sélectionnée ; mémoriser ensuite si ce choix doit aussi devenir le défaut futur.
L'import et le retour à la silhouette standard s'annulent avec Cmd+Z dans le plan.

Le PNG est intégré au fichier .ogp : déplacer ou supprimer le fichier image
d'origine ne casse pas le plan. Formats acceptés : PNG, 4 Mio maximum et
16 mégapixels maximum. Un PNG endommagé dans un plan utilise la silhouette
standard. Les images personnelles dans **visuals/** sont privées et ignorées
par Git. Une image personnelle ne devient pas un dessin fourni avec l'application.

Le gros catalogue local est utile hors ligne, mais la recherche en ligne suffit
pour découvrir des plantes au fil du projet. Pour un usage simple, partir de ses
propres fiches et favoris ; il n'est pas nécessaire de parcourir les milliers de
fiches importées. Les fiches existantes restent conservées.

## Retour au catalogue d'origine

À la demande du propriétaire, le 20 octobre 2026, les fiches ajoutées et les
collections personnelles ont été retirées de la bibliothèque active, application
fermée. Les deux fichiers `custom_plants.json` et `plant_lists.json` ont d'abord
été copiés et vérifiés octet par octet dans un dossier
`catalog-before-factory-<date>` du stockage utilisateur. Un manifeste contient
leurs tailles et empreintes SHA-256. Les sauvegardes antérieures sont conservées.

Le catalogue actif revient aux **118 fiches embarquées** ; les chiffres d'import
plus haut décrivent donc l'historique, pas l'état actif. Les collections ajoutées
ne sont pas réinstallées au démarrage. Les plans `.ogp`, leurs fiches et leurs
images intégrées, les clés API et les réglages de l'application restent conservés.
Ce retour concerne la bibliothèque de plantes, pas tous les réglages du logiciel.

Pour restaurer l'ancienne bibliothèque, fermer OGP, sauvegarder les deux fichiers
actifs s'ils ont été recréés, puis recopier les fichiers archivés à leur emplacement
d'origine. Ne pas relancer les scripts d'import si l'on souhaite garder le catalogue
d'origine. **Plant Lists** et **Create Custom** permettent de reconstruire une petite
bibliothèque personnelle au fil des besoins.

## Fusionner des formes : rectangle à bouts arrondis

La fonction existe déjà pour deux formes fermées : rectangle, cercle ou polygone.
Dessiner un rectangle puis un cercle de diamètre égal à sa largeur à chaque
extrémité, avec les centres sur les petits côtés. Les formes doivent se chevaucher.
Passer à l'outil de sélection, sélectionner le rectangle et un cercle avec Maj,
puis **clic droit > Boolean > Union** et confirmer l'aperçu. Sélectionner ensuite
le résultat et le second cercle et répéter l'union. On obtient un seul polygone
éditable, sauvegardable, avec deux étapes d'annulation. Les arrondis deviennent une
approximation polygonale ; les ombres et l'épaisseur du trait n'agrandissent pas
la géométrie fusionnée. L'opération reste limitée à deux objets à la fois.

## Importer un remplissage personnel

Sélectionner une surface (rectangle, polygone, cercle géométrique ou ellipse),
puis **Properties > Import fill PNG…**. Le PNG doit faire au plus 4 Mio et
16 mégapixels. **Tile width** règle la largeur réelle de chaque répétition en
centimètres ; la hauteur suit les proportions de l'image. La valeur initiale est
256 cm. Les couleurs et la transparence d'origine sont conservées : la couleur
du remplissage standard ne recolore pas l'image personnelle.

Choisir une image déjà répétable sans raccord (*seamless/tileable*) : l'import
répète le PNG mais ne répare pas ses bords. Le PNG est intégré aux métadonnées du
`.ogp` ; le fichier source peut ensuite être déplacé. L'import, la taille et le
retour au remplissage standard sont annulables. **Use built-in fill**, ou choisir
un motif standard dans **Fill Pattern**, retire le remplacement personnel ; annuler
le restaure. Un PNG intégré invalide utilise le remplissage standard.

Cette fonction vise les surfaces 2D. Les plantes et objets illustrés conservent
leurs dessins, et les chemins linéaires leurs styles dédiés. Les anciennes versions
de l'application peuvent afficher le motif standard à la place de l'image personnelle.
La mini-vue doit rester dans le coin inférieur droit de la zone de dessin, y compris
lorsque la disposition ou les barres de défilement changent. On peut la masquer via
**View > Overlays > Show Minimap**.

## Recherche d'une interface française existante

Vérification du 20 octobre 2026 : aucun catalogue français trouvé dans les 24
branches publiques du dépôt amont, ni dans les branches par défaut des cinq autres
forks publics recensés. Les recherches d'issues/PR amont sur « French » et
« français » n'ont pas trouvé de demande correspondante. La recherche web n'a pas
identifié d'extension française associée. Cela ne couvre pas les dépôts privés ni
les travaux non publiés.

Le [dossier des traductions amont](https://github.com/cofade/open-garden-planner/tree/master/src/open_garden_planner/resources/translations)
contient anglais, allemand et espagnol au moment de cette vérification ; ce fork
enregistre toujours anglais et allemand. Aucun changement amont sans rapport n'a
été importé. Les [forks publics](https://github.com/cofade/open-garden-planner/forks)
et les [branches amont](https://github.com/cofade/open-garden-planner/branches)
permettent de reprendre la recherche ultérieurement. Les traductions de noms de
plantes ne constituent pas une traduction de l'interface. Ajouter le français
nécessite un catalogue Qt complet selon `docs/TRANSLATING.md`.
