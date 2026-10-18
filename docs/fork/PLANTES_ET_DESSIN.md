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
précision et leurs inconnues. Les noms affichés dans les fiches sont ceux du
catalogue anglais. Cette sélection facilite la prise en main ; elle ne garantit
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
