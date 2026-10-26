# Pilotage de l’application par MCP

Le MCP fournit deux accès complémentaires dans le fork macOS : les outils métier
adressent directement les objets du plan ; les outils d’interface pilotent les
commandes, dialogues et surfaces Qt du logiciel. Une fonction sans outil métier
dédié peut être pilotée par son parcours d’interface existant. L’existence de ce
parcours ne démontre pas sa réussite pour toutes les fonctions.
Le client n’a plus besoin d’un connecteur séparé de contrôle de l’ordinateur.

La [matrice d’audit du 26 octobre 2026](MCP_AUDIT_2026-10-26.md) distingue les
parcours démontrés, partiels et non testés. Elle fait foi pour le niveau de preuve ;
le tableau ci-dessous est un inventaire des accès, sans certification exhaustive.

`get_capabilities()` décrit ces accès et la liste des outils réellement enregistrés.
Après mise à jour du code : relancer OGP puis reconnecter le client MCP.
Le contrat API `2` expose `identity` : version OGP, version du SDK MCP distincte,
PID, heure de construction du serveur et empreinte des sources à cet instant.
`initialize.serverInfo.version` annonce également OGP. L’empreinte ne certifie pas
du code remplacé à chaud : reconstruire le serveur après modification des sources.
Les workflows annoncés ne référencent que les outils effectivement enregistrés.

## Outils de pilotage Qt

Tous ces outils, y compris inspection et capture, exigent le jeton d’écriture :
les dialogues peuvent contenir des données personnelles et des valeurs saisies.
Ils sont absents lorsque les écritures MCP sont désactivées ou sans jeton.

| Outil | Fonction |
| --- | --- |
| `get_application_ui(root_id?, max_nodes?, include_hidden?)` | Actions de menus/barres, fenêtres, dialogues, champs, choix, valeurs, états, positions, sélection du dessin et surfaces 3D natives. Les mots de passe sont masqués. |
| `invoke_ui_action(action_id, request_key)` | Déclenche la commande Qt découverte ; ouvre aussi les menus et les dialogues. |
| `set_ui_value(widget_id, value, request_key)` | Saisit un texte Unicode, une valeur numérique, une date ISO, un choix, une case ou un onglet et termine l’édition normale. Les plages, champs non modifiables et validateurs sont respectés. |
| `interact_ui(widget_id, action, request_key, parameters?)` | Clic/double-clic, boutons de dialogue, clavier, souris, molette, sélection du dessin, édition des cellules par leur délégué, arbres, réordonnancement de lignes et dépôt depuis catalogue/liste de plantes. |
| `read_ui_model(widget_id, index_path?, start?, count?)` | Pagination, en-têtes, textes des lignes personnalisées, données JSON `UserRole` (souvent UUID), chemins de lignes, handles des widgets intégrés, états et possibilités d’édition. |
| `get_ui_operation(operation_id)` | État `queued`, `running`, `completed` ou `failed`, clé de requête, horodatages et type/message d’erreur éventuel. |
| `render_ui_image(widget_id?)` | Capture de la fenêtre, du dialogue ou d’une surface native 3D, avec dimensions et facteur d’échelle. |

L’inventaire montre par défaut les contrôles visibles, pour ne pas noyer le client
dans les champs des onglets inactifs. `include_hidden=true` complète cet inventaire
lorsqu’une inspection des contrôles masqués est utile.

Les identifiants proviennent du serveur en cours : ne pas les inventer ni les
réutiliser après destruction du contrôle ou redémarrage du serveur. Les actions
indiquent leurs menus et widgets associés ; les contrôles indiquent parent,
enfants, libellé, choix, visibilité et activation. Un inventaire tronqué se poursuit
avec `root_id` sur un sous-arbre, ou `max_nodes` jusqu’à 10000. Les listes se lisent
par pages et les arbres par chemins de lignes. Les champs masqués sous un panneau
replié ou un onglet doivent d’abord être révélés par leur en-tête/onglet.
Les champs donnent aussi leur `label` de formulaire et leurs `groups`. Les
contrôles Min/Opt/Max des semences, les étapes de propagation et le lien de lot
possèdent des `object_name` stables. Le calendrier peint expose `custom_state` :
identités botaniques, fenêtres **prévues**, année, sélection et rectangles de lignes
en pixels locaux pour `interact_ui(click)`. Il n’atteste aucune réalisation.
Les nombres/dates affichés comme inconnus utilisent `value=null`, avec
`display_text`, `special_value_text` et `value_is_special`. `raw_value` conserve
la sentinelle technique sans la présenter comme une mesure ; `set_ui_value(null)`
rétablit cet état uniquement sur un contrôle configuré avec une valeur spéciale.
La sélection d’un tableau respecte son mode lignes/colonnes ; relire après tri ou
filtre, car `index_path` est une adresse temporaire et non une identité métier.

### Dialogues et fin d’une opération

Une action renvoie immédiatement un reçu ; `queued` n’est pas un succès. Une
commande ouvrant un dialogue reste `running` pendant son `exec()` Qt. Le serveur
reste disponible : inspecter le dialogue indiqué par `modal_id`, saisir les valeurs,
cliquer le bouton normal de validation/annulation, puis consulter le reçu. Les
sélecteurs de fichiers natifs sont remplacés par leurs dialogues Qt pendant cette
session de pilotage afin de pouvoir saisir les chemins depuis MCP. Le choix
antérieur de l’application est rétabli à l’arrêt du serveur ; rien n’est écrit dans
les préférences du propriétaire pour ce mécanisme.

`completed` signifie que l’entrée a été délivrée et que le gestionnaire Qt est
revenu. Relire ensuite l’état métier, la fiche ou l’interface pour constater le
résultat : une validation applicative peut garder un dialogue ouvert ou afficher
un message. Un dialogue modal actif doit être traité avant la fenêtre parente.
La fermeture conserve les confirmations ordinaires de travail non enregistré.
Quitter l’application ou désactiver son serveur coupe naturellement la connexion.

Un même `request_key` rejoue le reçu de la même entrée sans la répéter ; un contenu
différent avec la même clé est refusé. Les reçus sont conservés pour la session,
y compris après destruction du widget. Après redémarrage, relire l’état avant de
reprendre : les reçus UI ne sont pas des événements persistants du jardin. Pour les
récits, utiliser l’idempotence persistante d’`add_garden_intervention`.

Un timeout du pont Qt annule une opération encore en attente et indique qu’elle
n’a pas été exécutée. Si elle avait déjà commencé, le résultat est explicitement
indéterminé : relire l’état avant de réessayer. La livraison d’une entrée UI n’attend
pas tous les traitements différés de l’application (par exemple les 600 ms de
regroupement des dates du calendrier) ; attendre ensuite l’état métier attendu.

Les arguments inconnus sont refusés pour tous les outils. Les schémas de suivi
détaillent les types/statuts d’événements, périodes, cibles et champs autorisés ;
les nombres négatifs, booléens et non finis y sont refusés. Les unités
d’intervention restent des libellés explicites sans conversion automatique.
`get_object` refuse désormais les UUID invalides ou absents avec un message ;
il ne renvoie plus un succès sans contenu pour ces cas.

### Dessin, groupes et glisser-déposer

- Sur le `CanvasView`, `select_scene` reçoit `item_ids` et prépare les commandes
  Edit, les raccourcis, les propriétés ou les opérations sur plusieurs objets.
- Les événements souris utilisent des pixels logiques locaux ; sur un
  `QGraphicsView`, `coordinate_space="scene"` accepte aussi les coordonnées CAD
  réelles en cm, avec conversion par la transformation courante (zoom et Y-up).
  Un déplacement avec bouton tenu précise `buttons=["left"]`, puis libère le bouton.
- `double_click` délivre la séquence complète Qt. Les menus contextuels sont
  actionnés de manière à terminer leur `exec()` avec l’action choisie.
- `move_rows` reçoit `row`, `count`, `destination` (indice d’insertion avant retrait)
  et, pour un arbre, les chemins des parents. Il utilise le déplacement natif du
  modèle `InternalMove` : la liste des calques émet son signal habituel et crée
  sa commande annulable de réordonnancement.
- `drop` reçoit `source_id`, `index_path`, `column`, `x`, `y` et éventuellement
  `coordinate_space="scene"`. Catalogue et listes de plantes fournissent exactement
  leur charge MIME native ; le canevas traite les événements enter/move/drop.
  Un déplacement souris seul n’est pas un transfert MIME entre contrôles.
- Dans une cellule, `edit_cell` ouvre l’éditeur du délégué existant ; ses bornes et
  validateurs restent actifs. Déplier les parents d’une cellule d’arbre avant édition.
- Pour la 3D, utiliser le nœud `Qt3DWindow` pour les entrées et captures, puis les
  actions de sa barre pour Walk/Refresh. `key_press` / `key_release` permettent la
  marche continue ; Escape ressort du mode marche. Le conteneur QWidget n’est pas
  la cible réelle des touches de la vue native.

## Couverture fonctionnelle

La colonne Qt décrit les parcours identifiés pour les outils ci-dessus,
même lorsqu’une API métier dédiée n’existe pas. Les commandes sont celles de
l’application, donc conservent sa validation, ses confirmations et son historique.
Les réglages et opérations qui n’avaient pas d’undo dans l’interface n’en gagnent
pas artificiellement ; les mutations documentaires utilisent leurs commandes existantes.

| Domaine | Outils métier directs | Parcours Qt identifié (preuve dans l’audit) |
| --- | --- | --- |
| Fichiers | `new_plan`, `open_plan`, `save_plan` | Taille du canevas, récents, autosave et récupération ; dialogues Fichier/Edit |
| Inspection | Résumé, objets, géométrie, recherche spatiale, distances, diagnostics | Tous les inspecteurs, panneaux, tableaux et captures de l’UI |
| Création | `list_creatable_types`, `create_object` | Tous les outils de dessin, arcs/Bézier, symboles, catalogue et dépôt de plantes |
| Géométrie | Déplacement, dimensions, rotation, sommets | Courbes, découpe/extension, arrondis/chanfreins, séparation et opérations booléennes |
| Contraintes/jonctions | `get_geometry`, `list_constraints`, `remove_constraint` ; translations compatibles sans suppression des règles | Outils de création/édition avec le solveur existant et panneau Contraintes |
| Catégories | `set_object_type` pour structures | Tous les types proposés par les propriétés et les menus contextuels |
| Calques | Liste, création, renommage, suppression, affectation, verrou, visibilité/opacité | Réordonnancement `move_rows`, paramètres forêt-jardin et commandes du panneau |
| Superposition | `arrange_object` | Alignements/distributions, menus Edit et sélection multiple |
| Copie et transformations multiples | Historique commun | Copier/coller/dupliquer, groupes, miroirs et réseaux par clavier/menus/dialogues |
| Apparence | Valeurs initiales du catalogue | Couleurs, contours, matières, motifs, remplissages, hauteur, texte et libellés dans les propriétés |
| Botanique | Ressources, `set_species`, création avec espèce | Catalogue personnalisé, imports personnels, traits et apparence par panneaux/dialogues |
| Plantation | `set_planting_date`, `set_garden_card`, `set_parent_bed` | Toutes les propriétés de croissance et de pépinière présentes dans l’UI |
| Retours du jardin | CRUD/recherche d’interventions | Fiches et dialogues de suivi ; le récit conversationnel utilise les outils métier |
| Tâches | Calendrier, CRUD manuel | Cases d’état des tâches automatiques et dialogues du calendrier |
| Sol | État/incompatibilités, recommandations, `record_soil_test` | Édition/suppression des relevés et sol par défaut du projet |
| Associations/succession | Suggestions, ensembles, placement, plans et lacunes | Guildes, rotation culturale, propagation via leurs panneaux/dialogues |
| Récoltes/ravageurs | Export des récoltes et observations génériques | CRUD des journaux spécialisés, tableaux et formulaires |
| Journal avec épingles | Suivi d’interventions distinct | Création, modification, suppression de notes et de leurs épingles par l’outil/panneau Journal |
| Saisons | Lecture du projet | Gestion, création, changement et comparaison des saisons |
| Fond/import | Inspection du canevas | Import DXF, images/satellite, calibration, géoréférencement, visibilité/verrouillage |
| Localisation/soleil | Métadonnées du projet | Localisation, nord, date/heure, calculs d’ombre et carte solaire |
| Exports | PNG en conversation, PDF/DXF, CSV shopping/récoltes | SVG, CSV botanique, rapport jardin, impression et export PNG fichier |
| Vue 2D/3D | Rendu du canevas | Zoom, sélection, caméra/marche, soleil et captures natives |
| Préférences/application | Inventaire des capacités | Thème/langue, grilles/accrochages, overlays, panneaux, préférences et mises à jour |
| Historique | `get_history`, `undo`, `redo` | Même pile que les commandes d’interface, sans pile parallèle |

Une plante créée par MCP conserve une date de plantation **inconnue** ; donner une
date connue avec `set_planting_date`. Les anciens projets et la commodité de dessin
de l’interface ne sont pas réécrits. Un stock de graines nul s’affiche explicitement
comme `0 seeds`/`0 grams`. Le produit ne représente pas actuellement une quantité
inconnue de stock ou de récolte ; ne pas la convertir implicitement en zéro.

Les récoltes, tâches, observations et dates sont dans le projet `.ogp`. Les stocks
de semences, plantes personnelles, listes et préférences ont des stockages globaux
distincts. Leur sauvegarde n’est pas assurée par `save_plan`. L’undo est celui du
produit : les états de tâches, dates de propagation et stocks n’ont pas d’undo
commun artificiellement ajouté par le MCP.

## Compatibilité des contraintes et déplacements

Les translations métier ne lancent pas le solveur et n’effacent aucune contrainte.
La règle pure `agent_api/constraint_policy.py` vérifie l’invariance avant la commande,
après limitation au canevas, sur toutes les entités effectivement déplacées : objet,
plantes transportées, descendants Qt et faîtage traduit par le crochet de la maison.

| Contrainte | Translation autorisée |
| --- | --- |
| EQUAL, PARALLEL, PERPENDICULAR | Libre : taille et direction restent identiques |
| DISTANCE, EDGE_LENGTH, COINCIDENT, POINT_ON_CIRCLE | Même vecteur pour les deux ancres ; donc libre pour une dimension interne à un objet |
| ANGLE, POINT_ON_EDGE, TANGENT | Même vecteur pour les trois ancres, y compris la troisième |
| HORIZONTAL, VERTICAL_DISTANCE | Même déplacement Y des deux ancres ; X reste libre |
| VERTICAL, HORIZONTAL_DISTANCE | Même déplacement X des deux ancres ; Y reste libre |
| SYMMETRY_HORIZONTAL | Déplacements X égaux et déplacements Y opposés : l’axe du monde est conservé |
| SYMMETRY_VERTICAL | Déplacements Y égaux et déplacements X opposés |
| FIXED | Aucun déplacement de l’objet fixé (première ancre) |
| Type inconnu ou relation incomplète | Refus conservatoire |

Les composantes de déplacement sont comparées exactement, sans marge réutilisable
par de petites translations répétées. Il ne s’agit pas de déclarer une relation
précédemment invalide satisfaite : une translation autorisée préserve sa géométrie.
Les changements de forme et rotations gardent leur garde stricte ; leur compatibilité
ne peut pas être déduite de la seule translation des ancres.

Les régressions `test_agent_constraint_policy.py` et
`test_agent_constraint_compatibility.py` couvrent les types réels, le rectangle à
longueurs imposées, les axes, le déplacement groupé et les refus atomiques d’un
point fixe porté, le faîtage lié et le contrôle après clamp. Le test HTTP MCP
vérifie les deux outils de position, leurs réponses, l’authentification, le maintien
des dimensions/règles, l’undo/redo et la sauvegarde-réouverture isolée.

## Vérification

`tests/integration/test_agent_ui_control.py` vérifie la frontière entière : refus
sans jeton de chacun des sept outils, transport HTTP MCP réel, dialogue modal
pilotable pendant son ouverture, validation/saisie Unicode, menus contextuels,
rejeu sans double exécution, champs désactivés et handles détruits, pagination et
édition par délégué, propriétés réelles avec undo/redo et sauvegarde-réouverture,
gestes viewport en coordonnées réelles, groupement, réordonnancement de calques,
dépôt de plante avec undo et captures UI. Le parcours Cocoa natif vérifie aussi la
vraie fenêtre Qt3D : mode marche, touche tenue, déplacement mesuré de la caméra,
relâchement, capture et sortie par Escape. Les données et préférences des tests
sont isolées de celles du propriétaire.

Ces preuves couvrent les mécanismes partagés ; elles ne prétendent pas avoir
exercé chaque combinaison de données et chaque commande du logiciel. Les erreurs
métier, dépendances absentes ou erreurs réseau restent celles de la fonction appelée
et doivent être relues dans son dialogue/état, comme lors d’une utilisation manuelle.
