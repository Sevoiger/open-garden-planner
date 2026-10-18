# Plantes classiques, dessin et cercles

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
est connu. Ce champ appartient à cette plantation ; il ne renomme pas toutes les
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
fiches françaises et les 80 fiches génériques, cela représente 235 fiches dans la
recherche, dont des groupes non identifiés. Les collections peuvent présenter une
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
