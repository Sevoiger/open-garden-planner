# Plantes classiques, dessin et cercles

## Piloter le jardin par MCP

Après une mise à jour, relancer OGP et reconnecter le client MCP : sa liste d’outils
peut être mise en cache. `get_capabilities()` indique les outils réellement enregistrés,
les parcours utiles et les outils de pilotage. Pour toute fonction sans outil métier
dédié, utiliser `get_application_ui`, `invoke_ui_action`, `set_ui_value` et `interact_ui`
puis consulter `get_ui_operation` et relire le résultat. La [matrice de couverture](MCP_COUVERTURE.md)
détaille ce pilotage des commandes, dialogues, panneaux, imports/exports et de la 3D.
Un dialogue reste pilotable pendant son ouverture ; les fichiers se choisissent
dans un dialogue Qt accessible au MCP. Les sept outils UI exigent aussi le jeton.

Pour raconter un retour du jardin, l’agent doit d’abord retrouver les identifiants des
plantes ou zones avec `list_objects` et lire leurs fiches avec `get_garden_record`.
Il peut ensuite :

- Appeler `set_planting_date(item_id="…", planting_date="2026-10-24")` pour une date
  connue, ou passer `"2026-10"`, `"Automne 2023"`, `"2023"` selon ce qui est connu.
  `null` signifie explicitement inconnu. Les autres champs de la fiche sont conservés.
  La date seule ne déclare pas une intervention réalisée.
- Appeler `add_garden_intervention(target_ids=["…"], event_type="observation",
  description="Feuilles jaunies observées sur le plant", request_key="retour-2026-10-25-obs-1",
  occurrence="2026-10-25")`. Les autres types incluent `planting`, `watering`, `pruning`,
  `mulching`, `amendment` et `sowing`. Une même clé doit être réutilisée pour rejouer
  le même enregistrement ; un nouvel événement reçoit une nouvelle clé.
- Relire `get_garden_record` / `search_garden_interventions`, puis appeler `save_plan`
  pour écrire dans le fichier. Les actions précédentes restent en mémoire jusque-là.

L’agent conserve les mots de l’utilisateur et la précision de ses observations.
Une date absente reste inconnue ; une suggestion n’est pas enregistrée comme un
travail effectué. Une observation peut concerner plusieurs plantes ou une zone.
`edit_garden_intervention` corrige un récit, `delete_garden_intervention` le retire ;
`undo` / `redo` permettent de revenir sur les modifications.

Un déplacement MCP ne nécessite plus de retirer les contraintes de taille.
`move_object` et `set_object_position` conservent les longueurs de côtés,
l’égalité de taille et les directions parallèles/perpendiculaires. Une relation
horizontale permet encore un déplacement horizontal, une relation verticale un
déplacement vertical ; leurs autres composantes restent protégées. Les relations
internes à un groupe ou à un massif et ses plantes peuvent suivre un même
vecteur. Les positions **fixes**, les liens modifiés vers un objet laissé en place
et les symétries dont l’axe serait déplacé restent bloquants. Le contrôle porte
sur le déplacement réel après limitation au canevas, avant toute commande.

Les contraintes ne sont jamais effacées automatiquement. Un échec indique la
relation incompatible ; choisir une direction compatible ou passer par le
solveur de l’interface pour déplacer les objets liés. Les autres outils qui
modifient les formes (dimensions, sommets, rotation, espèce redimensionnée)
conservent leur protection actuelle des géométries contraintes.

Pour détacher une jonction : lire `list_constraints(item_id="…")` (ou toutes les
contraintes sans filtre), identifier la bonne paire ou les trois points, puis appeler
`remove_constraint(constraint_id="…")`. Cela conserve les contours et positions.
La suppression est annulable, y compris pour les contraintes à trois points ; les
objets verrouillés ou membres d’un groupe restent protégés. Les déplacements ou
modifications de sommets peuvent ensuite être effectués indépendamment.

## Suivre les dimensions d’une plantation

Dans la fiche d’une plante, **Mesures et historique…** permet d’ajouter un relevé
avec une date exacte, une hauteur et/ou une envergure en cm, et une note facultative.
Les champs « actuels » affichent la dernière mesure connue de chaque dimension ;
leur infobulle donne sa date. Une hauteur récente et une envergure plus ancienne
conservent donc chacune leur date. Le suivi reste accessible sans espèce renseignée.

Sélectionner un relevé pour le corriger ou le supprimer. **Nouveau relevé** prépare
une nouvelle saisie ; **Enregistrer le relevé** la valide. Une dimension vide reste
inconnue pour ce relevé. Un doublon du même jour et de la même dimension est refusé :
corriger le relevé existant. Chaque validation ou suppression s’annule en une étape.
Les valeurs antérieures sans date sont conservées séparément, sans inventer une date ;
elles redeviennent les valeurs actuelles si tous les relevés de la dimension sont retirés.

Le diamètre du cercle dessiné et les distances de plantation restent indépendants
des mesures : redimensionner le cercle ne crée pas d’observation. L’icône et les
vues qui projettent la croissance utilisent les relevés : valeur observée à sa date,
interpolation entre relevés (une réduction après taille est possible), puis estimation
linéaire vers la dimension adulte depuis le dernier relevé. Le délai de maturité
existant est compté à partir de ce relevé. Avant le premier relevé, sa dimension est
maintenue comme estimation d’affichage, sans prétendre connaître une ancienne mesure.
Les plans sans relevés datés gardent leur modèle antérieur.

Après un redimensionnement par poignée, le centre enregistré et celui utilisé
en 3D restent identiques au centre visible en 2D, y compris après annulation,
rétablissement et réouverture du plan. Ce correctif ne permet pas de reconstituer
automatiquement une position déjà perdue dans une ancienne sauvegarde.

Pour le MCP :

- `get_garden_record(item_id="…")` renvoie `measurements` (identifiant, `date`,
  `height_cm`/`spread_cm`, `note`), `current_size` (valeur, date et identifiant par
  dimension) et `undated_size` (anciennes valeurs conservées).
- `add_plant_measurement(item_id="…", date="2026-10-28", height_cm=125,
  spread_cm=80, note="Relevé au jardin", request_key="mesure-2026-10-28-1")`
  ajoute une observation. La date doit être explicite ; au moins une dimension
  strictement positive et finie est requise. Réutiliser la même clé au rejeu.
- `edit_plant_measurement(item_id="…", measurement_id="…", date="2026-10-28",
  height_cm=120, spread_cm=80)` remplace le contenu du relevé ; les dimensions
  omises deviennent inconnues dans ce relevé.
- `delete_plant_measurement(item_id="…", measurement_id="…")` le supprime.
  Le rejeu d’un ajout annulé ou supprimé ne recrée jamais le relevé.

Ces écritures exigent le jeton, passent sur le thread Qt et restent en mémoire
jusqu’à `save_plan`. Les opérations de document préparées exposent les mêmes trois
noms avec `target_id` pour la plante et `fields` pour le contenu du relevé ;
`measurement_id` appartient à `fields` pour corriger/supprimer. Leur clé de lot
assure le rejeu sûr, y compris dans l’hôte autonome. Le `.ogp` conserve les relevés
dans les métadonnées de la plantation ; aucun changement de version n’est nécessaire.

## Reclasser une pergola

Le type **Pergola** est disponible pour les rectangles et les polygones dans le
sélecteur de type des propriétés et le menu contextuel **Changer le type**.
Pour une structure déjà dessinée comme **Maison**, l’agent MCP peut appeler
`set_object_type(item_id="…", object_type="PERGOLA")`. Cette opération conserve
le contour, la position, le nom, le calque, les métadonnées et le style personnalisé ;
elle s’annule et se rétablit en une étape. Le nouveau type est conservé dans le `.ogp`.

L’outil MCP accepte les structures `HOUSE`, `GARAGE_SHED`, `GREENHOUSE`, `TOOL_SHED`,
`PERGOLA` et les formes `GENERIC_RECTANGLE` / `GENERIC_POLYGON`, selon les types
compatibles avec le contour. Il refuse les plantes, les massifs, les objets groupés,
les calques verrouillés et les structures avec un faîtage lié. Un appel répété sur
le type déjà appliqué est refusé sans ajouter d’étape d’annulation.
Pour une nouvelle pergola rectangulaire, utiliser `create_object` avec
`object_type="PERGOLA"`. Après mise à jour du code, relancer OGP puis reconnecter
le client MCP pour qu’il découvre le nouvel outil.

## Catalogue dans les catégories du haut

La barre présente trois entrées libellées tout à droite, sur la même ligne que les outils de dessin :

- **Formes** : massifs et surfaces, formes géométriques.
- **Plantes** : arbres, arbustes et haies, fleurs et vivaces, légumes et aromatiques.
- **Aménagements** : constructions, mobilier, clôtures et murs, équipements,
  contenants et supports verticaux. C’est le groupe des éléments non organiques.

Un clic ouvre directement la palette : les contenus des anciennes catégories
sont réunis, sans menu intermédiaire. Le champ de recherche du haut a été retiré.
Une disposition enregistrée avec deux lignes revient à une seule ligne au démarrage.
Les contraintes CAD précèdent les palettes et utilisent le débordement Qt si nécessaire.

La palette **Plantes** propose toutes les fiches du catalogue local,
avec recherche, filtres et pages de 48 plantes. **Previous plants / Next plants**
changent de page. Les fiches dont le type n’est pas renseigné sont incluses
dès l’ouverture ; les filtres restent disponibles à l’intérieur de la palette.
Les filtres actifs prennent un fond vert contrasté dans les thèmes clair et
sombre ; les valeurs choisies ne dépendent pas de la langue affichée.

Par défaut, choisir une fiche, cliquer sur le plan pour fixer son centre, puis
cliquer sur le bord pour choisir sa taille. La case **Choose size on plan
(center, then edge)** permet de revenir au placement en un clic avec la taille
du catalogue. Le glisser-déposer utilise aussi la taille connue ou générique.
La fiche botanique complète, sa provenance et
son identité sont conservées. L'emprise initiale utilise l'envergure connue
(plafonnée à 50 m pour le dessin) ou la taille générique habituelle. Annuler puis
rétablir conserve le même sujet. **Search plant databases…** ouvre la recherche
existante avec les fournisseurs configurés ; cliquer **Search online** dans cette
fenêtre pour interroger les API. La confirmation prépare un nouveau placement.

La section **Illustrations and generic shapes** propose séparément les dessins :
246 illustrations de plantes et 15 formes génériques, dont 138 nouveaux dessins
validés le 19 octobre. Ces raccourcis gardent le dessin centre puis bord du cercle.
Les dessins sont visibles par défaut ; décocher l'intitulé permet de les masquer
temporairement. Ils réapparaissent à la prochaine ouverture du menu. Les fiches
botaniques restent visibles en premier. Choisir une fiche
prépare un placement ; **Plant Details** décrit la sélection du plan. Une plante
sans fiche affiche le message d'absence de données, sans conserver le profil de
la plante précédente.
Les dessins sont conventionnels : ils ne prouvent ni une espèce ni une variété,
et leurs couleurs ne décrivent pas nécessairement votre cultivar. Une fiche sans
dessin spécifique utilise une silhouette générique. La recherche globale de la
barre trouve aussi les fiches locales et privilégie les résultats botaniques.

Les variétés utilisent désormais le dessin de leur espèce ou genre lorsque
l'association existe. Un dessin approchant peut aussi être associé explicitement
à une fiche personnelle. Ces associations ne changent ni son identité, ni ses
dimensions, ni ses traits botaniques. Un PNG personnel reste prioritaire.

### Dessiner une plante type puis l'identifier

Dans les illustrations, **Round Deciduous**, **Spreading Shrub** ou **Flowering
Perennial** sont des silhouettes sans espèce supposée. Choisir une silhouette,
dessiner centre puis bord, revenir à la sélection avec Échap, puis sélectionner
la plante. **Plant Details > Search** associe une fiche ; **Create Custom** permet
de la définir soi-même. Le diamètre dessiné reste distinct de l'envergure à
maturité de l'espèce : choisir un rayon ne crée pas une mesure botanique supposée.

### Régler les panneaux de droite

Chaque panneau se replie avec son titre. Une fois ouvert, faire glisser sa
bordure inférieure pour régler sa hauteur ; un double-clic sur cette bordure
rétablit sa hauteur initiale. La hauteur choisie est conservée pendant la session,
y compris après fermeture/réouverture du panneau ou changement de sélection.
**Properties** démarre plus haut pour laisser lire ses champs.

Faire glisser le séparateur entre le plan et la colonne pour régler sa largeur.
Les boutons des détails de plante deviennent **Rechercher / Charger / Créer** ;
ils s'empilent quand la colonne est étroite. Si un autre contenu reste plus large,
un défilement horizontal permet de l'atteindre sans imposer sa largeur à la colonne.

Le contenu de chaque panneau peut défiler indépendamment. La colonne complète
défile également lorsque plusieurs panneaux ouverts dépassent la fenêtre ; leur
ordre reste fixe. Les hauteurs reviennent à leurs valeurs initiales au prochain
lancement de l'application.

Le fond des en-têtes reste identique au survol ; la bordure et le dépliage
continuent d'indiquer le panneau actif. La molette fait défiler les panneaux et
les formulaires, mais ne modifie plus les nombres, dates, listes de choix et
curseurs. Les menus et listes déroulantes ouverts ignorent aussi la molette ;
leurs choix restent accessibles au clic et au clavier.

Dans **Properties > Calque**, déplacer un objet vers un calque masqué le rend
invisible ; vers un calque verrouillé, il perd sa sélection. Le panneau se vide
alors sans interrompre l'application. Le déplacement reste annulable et rétablissable.

### Associations de plantes et verrouillage

L'interface française utilise **association** à la place du terme spécialisé
« guilde » : un ensemble de plantes aux rôles complémentaires, souvent autour
d'un arbre. **Jardin > Insérer une association…** crée l'ensemble depuis un
modèle ; **clic droit sur une plante > Ajouter à une association…** rattache une
plante à un ensemble existant. Les rôles indiquent l'intention du jardinier et
ne garantissent pas une compatibilité biologique. Les noms et descriptions des
modèles conservent les traductions disponibles dans leurs fichiers.

Les dialogues de choix, d'insertion et d'édition s'ouvrent avec davantage de
place et restent bornés à l'écran. Les colonnes de rôles et les boutons
Modifier, Supprimer et Dissocier s'adaptent au texte français.

Le clic droit sur un objet propose **Verrouiller la position** puis
**Déverrouiller la position**. Ce sont les contraintes de position existantes :
le verrou est sauvegardé dans le plan, visible par un cadenas orienté vers le
haut lorsque les contraintes sont affichées, et annulable/rétablissable. Les
coordonnées X/Y dans Propriétés respectent aussi ce verrou. Le verrou porte sur
la position ; les autres propriétés restent modifiables. Un calque verrouillé
doit d'abord être déverrouillé dans Calques.

À la réouverture, les verrous portent sur les positions effectivement sauvegardées.
Les anciens fichiers dont les coordonnées de verrou ont été perdues sont pris
en charge sans déplacer les objets : le verrou est rétabli dans le repère chargé.
La prochaine sauvegarde normale conserve les coordonnées complètes. Le chargement
ne réécrit pas le fichier personnel et ne rapproche pas automatiquement des formes
verrouillées dont une autre liaison était déjà violée.

Pendant un glissement, une position verrouillée reste prioritaire sur les
contraintes d’alignement et les accrochages. Le déplacement Qt est bloqué avant
application : aucun déplacement temporaire suivi d’un retour à la position fixe.
Un objet verrouillé sélectionné
avec un objet mobile reste en place ; le partenaire mobile peut glisser le long
d’un bord auquel il est contraint. Déplacer un objet ne remet pas à leur ancienne
position cible d’autres objets verrouillés hors du glissement. Pour déplacer
volontairement un objet fixe, utiliser d’abord **Déverrouiller la position**.

Les contraintes **Coïncidence** et **Sur le côté** peuvent se combiner : un même
point contraint sur deux côtés sécants rejoint leur intersection. Deux milieux
de côtés différents d’un rectangle peuvent aussi le placer dans un coin sans
changer ses dimensions. Le panneau vérifie la distance au côté, et non la distance
à son premier sommet. Les traits des rectangles visent le côté choisi ; un bord
polygonal conserve son identité par son numéro même s’il change de direction.

La création et son contrôle de compatibilité utilisent les mêmes références
immobiles : A se place par rapport à B ; si A est verrouillé, B peut être ajusté.
Les lignes et cercles de construction restent fixes. Une contrainte déjà violée
dans un autre réseau indépendant ne bloque pas une nouvelle liaison et n’est pas
réparée par cet ajout. Les contrôles utilisent les positions après résolution,
y compris après translation d’un polygone. La résolution interactive vise une
précision de 0,5 mm, sous le seuil de 1 mm de l’indicateur de coïncidence.

La sélection d’une **serre** n’ouvre pas le panneau **Rotation des cultures**.
Ce panneau reste disponible à la sélection des parcelles de culture.

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

## Dossier des plans et réouverture

Les boîtes de dialogue réutilisent le dossier du plan courant, puis celui du
dernier plan sauvegardé. Un plan enregistré dans **projects/** garde donc ce
dossier comme point de départ, y compris après redémarrage. Aucun dossier
**Open Garden Planner** supplémentaire n’est créé dans Documents. Sans dossier
mémorisé utilisable, le choix initial est Documents, puis le dossier utilisateur
si Documents n’existe pas. Les dossiers déjà présents ne sont pas supprimés.

Au démarrage, le dernier plan sauvegardé est rouvert automatiquement ; l’écran
d’accueil n’est affiché que si aucun plan ne peut être repris. Ouvrir un autre
fichier ne change pas ce choix : seule une sauvegarde réussie mémorise le chemin
absolu dans le réglage `startup/last_saved_project`. Pour la première ouverture
après cette mise à jour, la liste des fichiers récents sert de reprise. Effacer
les fichiers récents efface aussi le choix de réouverture.

Un fichier fourni explicitement au lancement ou un travail commencé pendant le
démarrage reste prioritaire. Les propositions de récupération passent avant la
réouverture automatique ; une seule récupération est chargée par démarrage. Si
la récupération du dernier plan est refusée mais conservée, l’accueil reste
accessible et sa copie n’est pas effacée par une ouverture automatique. Un plan
introuvable revient à l’accueil ; un plan illisible signale l’erreur puis revient
à l’accueil. Les sauvegardes automatiques ne remplacent pas le dernier plan
sauvegardé comme choix de démarrage.

## Premier jet depuis un dessin

Un croquis, une photo de plan ou un scan permet de préparer un `.ogp` éditable :
image de fond, limites, massifs et objets sur des calques séparés. Il faut fournir
le dessin et au moins une longueur réelle connue pour l'échelle. Une deuxième
longueur permet de contrôler la calibration ; une photo en perspective peut
nécessiter une rectification. Le nord est utile si des études d'ensoleillement
sont souhaitées.

L'import d'une image de fond conserve sa résolution, y compris pour les scans
HD de permis de construire. Le décodage bénéficie d'au moins 512 Mio par image
(la taille décompressée compte, pas le poids du PNG sur disque), sauf si la limite
Qt est déjà désactivée. Cela vaut aussi pour la réouverture du fond intégré au
`.ogp` ; la limite Qt précédente est rétablie après chaque chargement. Calibrer
ensuite le fond sur une cote connue.

Même à **0 % d’opacité**, le fond reste accessible : clic droit dans une zone
vide du canevas → **Images de fond** → choisir le fichier et son action
**Définir l’opacité…**. Le menu indique le pourcentage courant et distingue les
images lorsqu’il y en a plusieurs. Ce réglage reste accessible si l’image est
verrouillée ; il ne la réimporte pas et ne la déplace pas. Valider un changement
d’opacité crée une étape annulable et marque le plan comme modifié. Annuler la
boîte de dialogue ou conserver le même pourcentage ne crée aucune étape.

### Orienter le nord sans tourner le dessin

Après l’étalonnage, ouvrir **Fichier > Définir l’orientation du nord…**.
Garder le terrain dans l’alignement le plus pratique pour tracer. Dans la boîte
de dialogue, cliquer sur la boussole ou saisir l’angle du nord géographique
indiqué sur le plan importé : **0° vers le haut, 90° vers la droite, 135° vers
le bas à droite, 180° vers le bas**. L’angle augmente dans le sens horaire.
Le terme « bas à droite » décrit l’écran : c’est la direction du nord du terrain,
pas son sud-est géographique.

La flèche **N** en haut à droite du canevas suit ce réglage. Le fond, les objets,
les coordonnées et les axes de tracé restent en place. Valider crée une seule
étape annulable ; annuler la boîte de dialogue ne change rien. L’orientation est
propre au projet, sauvegardée dans le `.ogp`, conservée lors de la création d’une
saison et rétablie à la réouverture. Un ancien plan sans orientation reste à 0°.
Le champ additionnel `north_angle_deg` ne change pas la version du format ; les
anciennes versions de l’application ne l’utilisent pas pour leurs calculs.

Les ombres 2D, la carte d’ensoleillement, l’exposition des plantes et le soleil
3D utilisent cette orientation ; le rapport PDF oriente aussi sa flèche nord.
Après un changement d’angle, relancer la carte d’ensoleillement : l’ancien résultat
est effacé, y compris si son calcul était en cours. Les coordonnées GPS restent
à renseigner séparément pour la simulation solaire. La boussole du canevas est
un repère d’affichage et n’est pas ajoutée aux exports simples PNG/SVG.

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
.venv/bin/python scripts/import_local_plants.py plants/import01
```

Le dossier import01 fourni contient 37 fiches JSON et leurs PDF. Les fiches sont
ajoutées à la recherche et à **Mes plantes — import01**. Avec les 118 fiches
d'origine, cela représente 155 fiches. L'option facultative `--french-catalog`
installe aussi les anciennes collections françaises et les 80 fiches génériques ;
elle n'a pas été utilisée pour cette réinstallation ciblée. Les fiches personnelles déjà présentes
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
d'origine ne casse pas le plan. Formats acceptés : PNG, 16 Mio maximum et
16 mégapixels maximum. Un PNG endommagé dans un plan utilise la silhouette
standard. Les images personnelles dans **visuals/** sont privées et ignorées
par Git. Une image personnelle ne devient pas un dessin fourni avec l'application.

Le rendu conserve en cache 64 images de 1024 × 1024 pixels (256 Mio au maximum
pour les rasters), soit les deux orientations plan/catalogue de 32 illustrations
personnelles. Cela évite de redécoder les PNG à chaque déplacement ou zoom dans
un jardin comportant une vingtaine d'illustrations différentes.

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

À la demande suivante, les **37 fiches d'import01** ont été réinstallées seules
dans **Plant Lists > Mes plantes — import01** : le catalogue local contient donc
118 fiches d'origine et 37 fiches personnelles. Le gros import Permapeople reste
retiré. Chaque fiche dispose d'une illustration : espèce/genre pour 26, symbole
approchant explicite pour 10 et un PNG personnel. Le rosier Ketchup & Mustard retrouve son PNG
personnel. Les noms de cultivars sont visibles dans les intitulés.

La planche privée `output/import01/illustrations.html` montre les 37 associations,
leurs limites et les générations utiles pour une apparence plus fidèle. Aucun
dessin nouveau n'a été généré automatiquement. Les données source et leurs
ambiguïtés sont conservées ; le dessin ne constitue pas une identification.

Pour restaurer l'ancienne bibliothèque, fermer OGP, sauvegarder les deux fichiers
actifs s'ils ont été recréés, puis recopier les fichiers archivés à leur emplacement
d'origine. Ne pas relancer les scripts d'import si l'on souhaite garder le catalogue
d'origine. **Plant Lists** et **Create Custom** permettent de reconstruire une petite
bibliothèque personnelle au fil des besoins.

## Fusionner des formes : rectangle à bouts arrondis

Dessiner le rectangle puis deux cercles, dont les centres sont sur les petits
côtés et le diamètre correspond à la largeur du rectangle. Sélectionner les trois
objets avec Maj, puis **clic droit > Combiner les formes > Fusionner** et confirmer
l'aperçu. On obtient un seul polygone éditable et sauvegardable, avec une seule
étape d'annulation qui restitue tous les objets d'origine.

La fusion accepte plusieurs rectangles, cercles, ellipses ou polygones reliés
par contact ou chevauchement. Les arrondis deviennent une approximation polygonale ;
ombres et épaisseur du trait ne modifient pas le contour. Le résultat reprend
l'apparence d'un objet source. Les plantes, objets liés ou contraints et formes
portant des données incompatibles sont refusés pour préserver leurs informations.
Cette protection inclut les interventions, analyses de sol et plantations qui
désignent une zone explicitement, même sans lien de conteneur sur le dessin.
La texture conserve son orientation et son alignement ; une étiquette masquée
reste masquée sur le résultat.
Intersection et soustraction restent limitées à deux formes.

Un double-clic ouvre l'édition des sommets. Pour les contours denses (plus de
24 points), les poignées sont espacées à l'écran et renouvelées au zoom ou au
déplacement de la vue ; les coordonnées et longueurs des grandes arêtes sont
consultables au survol. Zoomer pour atteindre un point plus précis. Les petits
segments des arrondis n'affichent plus chacun une cote et une poignée d'insertion.
La géométrie enregistrée reste complète ; Échap ou désélection ferme l'édition.

Pour un contour libre, les outils polygone et courbe de Bézier permettent aussi
de dessiner directement une forme personnalisée.

## Découper une forme selon un axe

Sélectionner une forme, puis **Propriétés > Découper selon un axe…**, ou utiliser
l’entrée directe **Découper selon un axe…** du clic droit (ce n’est pas un sous-menu).
Deux clics définissent
une droite de coupe prolongée de part et d’autre de la forme. Les deux points et
la prévisualisation utilisent le magnétisme activé (sommets, milieux, bords).
**Maj** contraint l’angle par pas de 45° ; **Échap** abandonne. Si la droite ne
traverse pas la surface, choisir un autre second point.

Les morceaux deviennent des polygones indépendants, avec le calque et l’apparence
d’origine. Un seul **Cmd+Z** restaure exactement l’objet initial. Une forme concave
peut donner plus de deux morceaux ; les arrondis sont approximés en polygones.
La découpe accepte les surfaces fermées indépendantes : déverrouiller la position
et retirer les contraintes avant de couper. Les plantes, objets liés et formes
portant des données de suivi (sol, cultures, récoltes, tâches ou notes liées) sont
protégés contre cette transformation.
Une zone utilisée par une fiche de plantation reste également protégée, même
si cette zone n’a pas encore sa propre fiche ou ses propres interventions.

## Importer un remplissage personnel

Sélectionner une surface (rectangle, polygone, cercle géométrique ou ellipse),
puis **Properties > Import fill PNG…**. Le PNG doit faire au plus 16 Mio et
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

**Angle du motif** fait tourner le remplissage indépendamment du contour.
**Échelle du motif** règle la répétition : 100 % conserve la taille de référence,
200 % donne des motifs deux fois plus grands, 50 % deux fois plus petits.
**Décalage X du motif** et **Décalage Y du motif** déplacent le remplissage en
centimètres sans déplacer le contour : X positif vers la droite, Y positif vers
le haut dans le repère local de la surface. L’angle et l’échelle du motif ne
multiplient pas ces distances. Pour les tuiles de toiture, ce repère suit le
faîtage et se reflète sur l’autre pan. Remettre les deux valeurs à zéro rétablit
l’alignement initial. Les anciens plans commencent à zéro.
Ces réglages s’appliquent aux motifs standards comme aux PNG et sont enregistrés
avec la forme ; ils sont annulables. Pour un PNG, l’échelle multiplie la largeur
réelle indiquée dans **Tile width**.
Après déplacement, fusion ou découpe, l’alignement du motif est conservé à la
sauvegarde et à la réouverture, sans changer les décalages X et Y affichés.

Le sélecteur **Matériaux** propose les PNG présents dans `visuals/materials/`
(par exemple ardoise, bois ou paillage). Choisir un matériau l’intègre au plan.
Les imports de dessins de plantes s’ouvrent dans `visuals/plants/` ; les imports
de remplissages dans `visuals/materials/`. En l’absence du sous-dossier,
l’ancien dossier `visuals/` reste utilisé. Les images déjà intégrées aux plans
et à la bibliothèque restent valables après déplacement du PNG source : aucun
chemin de fichier externe n’est nécessaire à leur affichage.

Cette fonction vise les surfaces 2D. Les plantes et objets illustrés conservent
leurs dessins, et les chemins linéaires leurs styles dédiés. Les anciennes versions
de l'application peuvent afficher le motif standard à la place de l'image personnelle.
La mini-vue doit rester dans le coin inférieur droit de la zone de dessin, y compris
lorsque la disposition ou les barres de défilement changent. On peut la masquer via
**View > Overlays > Show Minimap**.

## Interface française

Choisir **View > Language > Français**, puis redémarrer l'application. Le choix
est mémorisé ; le menu devient **Affichage > Langue** et permet aussi de revenir
à English ou Deutsch. Le catalogue Qt français couvre les textes actifs de
l'inventaire : menus, panneaux, dialogues, outils de dessin, messages et
noms usuels intégrés. Les noms scientifiques, données personnelles et textes
libres provenant de services en ligne conservent leur contenu d'origine.

Les fichiers source `.ts` et compilé `.qm` sont livrés ensemble. Pour maintenir
la traduction lors d'un ajout de texte, suivre `docs/TRANSLATING.md`.

### Recherche préalable d'une traduction existante

Vérification du 20 octobre 2026 : aucun catalogue français trouvé dans les 24
branches publiques du dépôt amont, ni dans les branches par défaut des cinq autres
forks publics recensés. Les recherches d'issues/PR amont sur « French » et
« français » n'ont pas trouvé de demande correspondante. La recherche web n'a pas
identifié d'extension française associée. Cela ne couvre pas les dépôts privés ni
les travaux non publiés.

Le [dossier des traductions amont](https://github.com/cofade/open-garden-planner/tree/master/src/open_garden_planner/resources/translations)
contient anglais, allemand et espagnol au moment de cette vérification ; ce fork
enregistrait alors anglais et allemand. Aucun changement amont sans rapport n'a
été importé. Les [forks publics](https://github.com/cofade/open-garden-planner/forks)
et les [branches amont](https://github.com/cofade/open-garden-planner/branches)
permettent de reprendre la recherche ultérieurement. Les traductions de noms de
plantes ne constituent pas une traduction de l'interface. Un catalogue Qt complet
a ensuite été ajouté à ce fork selon `docs/TRANSLATING.md`.

## Extensions et bibliothèques d'illustrations

Recherche du 20 octobre 2026 : aucun pack d'illustrations installable ni marketplace
d'extensions utilisable n'a été trouvé pour ce dépôt dans les sources publiques
examinées. Le [dépôt amont](https://github.com/cofade/open-garden-planner) fournit
ses ressources et générateurs SVG ; l'[issue de périmètre historique](https://github.com/cofade/open-garden-planner/issues/188)
place un système de plugins et une marketplace dans une phase ultérieure.
Les autres projets nommés « Garden Planner » ne sont pas pour autant des extensions
compatibles avec OGP.

Dans ce fork, les 246 illustrations et 15 silhouettes génériques sont intégrées
aux sources. Le retour usine de la bibliothèque de fiches ne supprime pas ces
dessins. Les points d'extension utiles sont les collections JSON et fiches locales,
les PNG personnels, les remplissages importés et l'API agent. Les dessins intégrés
utilisent un registre partagé pour les catégories, le rendu du plan et les
associations par défaut ; aucune dépendance extérieure n'est nécessaire pour les
retrouver.


## Fluidité du dessin et des données

Le déplacement avec le bouton central suit les pixels de la souris, y compris
pour les petits mouvements et à différents zooms. Les arrondis du recentrage
ne doivent pas créer de dérive ou annuler les mouvements en diagonale.

La consultation et la recherche dans **Données du jardin** lisent uniquement les
identités et les champs de suivi. Elles ne réencodent pas l’image de fond en PNG.
Les exports JSON, CSV et sauvegardes portables utilisent toujours le plan complet.

Une liaison **Sur le côté** reste prioritaire sur le magnétisme visuel et le
recalage au bord du canevas. Les longueurs internes d’un rectangle ne désactivent
pas son glissement le long d’une référence fixe ; ses dimensions restent identiques.


## Points de référence sur le plan

L’icône en forme de cible avec une croix, près de l’outil de mesure dans la barre
principale, active **Point de référence**. Cliquer sur le plan ou saisir une
coordonnée, puis donner un nom (par exemple « Poteau nord » ou « Borne OGE »).
Un clic sur ou près de la frontière d’un polygone propose ce polygone comme
parent et coche **Rester sur le contour du parent**. La position est projetée sur
le trait exact. Une ancienne sélection (par exemple une pergola) n’intervient pas
dans ce choix. Hors frontière, le dialogue propose **Point indépendant**.

Un point lié au contour peut coulisser sur tous les côtés et passer un sommet,
y compris le côté qui ferme le polygone ; le tirer vers l’intérieur ou l’extérieur
ne le fait pas quitter le trait. Le mode est sauvegardé avec le point. Pour un
repère existant, choisir le parent et cocher **Rester sur le contour du parent**
dans son dialogue. Décocher cette option conserve un simple rattachement qui suit
les déplacements du parent ; choisir **Point indépendant** supprime le lien.

La croix propose deux modes : **Fixe dans le plan (suit le zoom)**, par défaut
pour les nouveaux points, mesure sa taille en centimètres et grossit ou rétrécit
avec le zoom ; **Variable dans le plan (constante à l’écran)** conserve une taille
en pixels à l’écran, comme auparavant. Les anciens points gardent ce second mode.
Son centre est la position exacte utilisée
par les mesures et l’accrochage. Le point ne représente aucune surface. Il se
déplace en tirant la croix, sa zone de prise élargie ou son nom ; un halo rend la
sélection visible. Un double-clic, le menu contextuel ou le bouton du panneau
**Propriétés** ouvre ses réglages : nom, rattachement, taille et mode de taille du symbole,
couleur et épaisseur du trait. La taille ne change pas la coordonnée du repère.
Les opérations, y compris l’apparence, sont annulables et sauvegardées.
Un clic droit sur la croix ou son nom donne accès au verrouillage de position,
à la suppression, à la copie, à la duplication et aux menus de calque et
d’organisation. **Verrouiller la position** empêche le déplacement ; la même
action permet ensuite de déverrouiller le repère. Annuler une suppression
restaure en une fois le point, son rattachement et ses contraintes.
La mini-carte conserve la sélection et la visibilité des repères pendant ses
rafraîchissements ; elle les représente par de petits points dans sa vignette.
Le maintien de la prise souris couvre le symbole et son nom : une perte de prise
Qt entre deux événements ne termine pas le glissement ni ne ramène le point en arrière.
L’accrochage pendant le glissement utilise le centre exact et les ancrages réels
des autres objets, sans attirer le contour du symbole ou son halo. Seul le centre
doit rester dans le canevas : un repère peut donc atteindre exactement son bord.

Le dialogue montre l’objet parent proposé par un contour pointillé ;
**Point indépendant** ne crée aucun lien. Après placement, un badge en maillons
signale le rattachement. Sélectionner le point montre le contour de son parent ;
le nom du parent apparaît dans le panneau Propriétés et l’infobulle.
Les aperçus sont transitoires : ils ne font pas partie des objets du plan.

Un point rattaché accompagne les
translations de son parent, par glissement, clavier ou API, et sa copie ; il ne
recalcule pas sa position lors d’une modification des sommets ou d’une rotation.
Supprimer le parent d’un repère le laisse indépendant à sa dernière position.

Le format `.ogp` conserve le point comme cercle avec l’indicateur additif
`metadata.reference_point`, son nom, son calque et les liens d’objets existants
(`parent_bed_id` / `child_item_ids`, également utilisés pour ces repères). Une
ancienne version peut donc le lire comme un petit cercle. La bibliothèque
personnelle n’intervient pas dans ce stockage. L’indicateur additif
`metadata.reference_point_on_contour` mémorise le mode de coulissement. Les anciens
repères gardent leur rattachement simple tant que cette option n’est pas activée.
`metadata.reference_point_fixed_size` conserve le choix de taille ; absent ou faux,
il garde le symbole de taille constante à l’écran.


### Masquer les cotes de contraintes

L’outil de longueur de côté accepte un clic sur toute la longueur du segment,
y compris sur un côté tourné ou celui qui ferme un polygone. Le côté visé est
mis en évidence au survol. Les objets masqués ou sur un calque verrouillé ne
sont pas proposés.

Le bouton **Afficher/masquer les cotes de contraintes** de la barre principale
(icône de cotation, après les outils d’annotation) bascule l’affichage des marqueurs
et des longueurs. Le même réglage existe dans **Affichage → Superpositions →
Afficher les contraintes**. Les longueurs fixées et les autres contraintes restent
actives lorsque leurs marqueurs sont masqués. L’état est conservé entre les lancements
et ce bouton ne change pas l’outil de dessin actif.

## Données du jardin et météo

L’onglet **Données du jardin**, à côté du calendrier, du stock et des tâches,
affiche les fiches des plantes et zones, les interventions, les filtres par calque
et les exports déjà accessibles via **Données du jardin…** dans Propriétés
ou le menu Jardin. Ces accès ouvrent maintenant la page, sans modale, et le bouton
de Propriétés sélectionne la fiche de l’objet. **Enregistrer la fiche**,
**Annuler** et **Rétablir** utilisent les commandes habituelles du plan.

La météo reste dans l’onglet **Calendrier de plantation**, pour le lieu configuré
via **Fichier › Définir la position du jardin**.
Les cartes indiquent les températures, les précipitations prévues en mm (y compris
l’équivalent en eau de la neige) et l’**ET₀ estimée**. Déplier le tableau pour les
16 jours disponibles ; cliquer **Heure par heure (3 jours)** pour afficher dans la
page les températures, précipitations et probabilités de pluie horaires. Les heures
sont celles du fuseau du jardin, indiqué au-dessus du tableau. Choisir la journée
dans la liste pour parcourir ses heures. Les dates utilisent la langue de
l’application, les colonnes numériques sont alignées et de petites barres
facilitent la comparaison des précipitations, de leur probabilité et de l’ET₀.
Le détail journalier et le détail horaire s’ouvrent chacun à leur tour.

L’ET₀ est l’évapotranspiration de référence FAO-56 fournie par
[Open-Meteo](https://open-meteo.com/en/docs), en mm/j : perte d’eau estimée d’un
gazon bien arrosé. Elle ne constitue pas une dose d’arrosage pour les plantes du
plan. 1 mm représente 1 L/m². Une valeur indisponible apparaît comme **—**.
Les données sont mises en cache pendant trois heures. Un ancien cache sans
détail horaire est renouvelé dès le prochain chargement ; en cas d’échec réseau,
son contenu journalier reste disponible, avec l’ET₀ indiquée comme inconnue. Les alertes de gel
du calendrier et des tâches utilisent cette même prévision.
