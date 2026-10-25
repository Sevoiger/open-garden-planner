# Couverture du logiciel par MCP

État vérifié dans les sources et tests du fork le 25 octobre 2026. L’objectif du
propriétaire est le pilotage de l’ensemble du logiciel. **Cet objectif n’est pas
encore atteint** : le MCP expose des opérations métier explicites, mais ne couvre
pas toutes les commandes du bureau. Le contrôle de l’interface ne remplace pas un
outil absent et ne doit pas être annoncé comme une garantie.

`get_capabilities()` donne la liste effectivement enregistrée dans le serveur en
cours, avec les parcours jardin et contraintes. Les outils d’écriture sont absents
si les écritures sont désactivées ou si aucun jeton n’est configuré. Lorsqu’ils sont
présents, chaque appel exige encore le jeton. Après mise à jour du code : relancer
OGP puis reconnecter le client MCP pour renouveler sa découverte d’outils.

## Matrice fonctionnelle

« Partiel » signifie que des fonctions du même domaine restent sans équivalent MCP.
L’inventaire se fonde sur les outils de `agent_api/server.py`, les commandes de
`app/application.py` et les opérations de `core/commands.py` / `ui/canvas/canvas_view.py`.

| Domaine | Accès MCP actuel | Manques identifiés |
| --- | --- | --- |
| Fichiers | `new_plan`, `open_plan`, `save_plan` | Redimensionner un plan existant, gestion des récents et récupération/autosave |
| Inspection | Résumé, objets, géométrie réelle, recherche spatiale, distances, diagnostics | Pas d’équivalent exhaustif de tous les inspecteurs du bureau |
| Création | `list_creatable_types`, `create_object` | Arcs/Bézier et symboles paramétriques exclus ; certaines variantes de forme non proposées |
| Géométrie simple | Déplacement relatif/absolu, redimensionnement, rotation, édition de sommets | Courbes, découpe/extension, arrondis/chanfreins et séparation de formes |
| Contraintes et jonctions | `get_geometry`, `list_constraints`, **`remove_constraint`** | Création/modification d’une contrainte ; édition avec solveur multi-objets |
| Catégories | `set_object_type` pour structures rectangle/polygone ; Pergola disponible | Reclassement général des autres familles avec migration de leurs liens métier |
| Calques | Lister/créer/renommer/supprimer, affecter un objet, verrouiller, visibilité/opacité | Réordonner les calques et réglages spécifiques de forêt-jardin |
| Superposition | `arrange_object` | Alignements et distributions de plusieurs objets |
| Copie et transformations multiples | Pas d’outil dédié | Copier/coller/dupliquer, miroirs, réseaux, groupement/dégroupement, opérations booléennes |
| Apparence | Création avec les valeurs prévues par le catalogue | Couleurs, contours, motifs, matières, décalages/rotation/échelle de remplissage, hauteur, libellés et texte existant |
| Botanique | Fiches de ressources, `set_species`, création avec espèce | Gestion du catalogue personnalisé, import personnel, traits et apparence individuelle complets |
| Plantation | **`set_planting_date`**, `set_garden_card`, `set_parent_bed` | Certaines propriétés de croissance et de pépinière restent accessibles seulement par les propriétés UI |
| Retour du jardin | `add_garden_intervention`, consultation/recherche, édition/suppression | Pas de transcription audio ni d’interprétation implicite du récit dans OGP : c’est le rôle du client conversationnel |
| Fiches de suivi | Nom, état, précision de date, notes, cultivar, zone, quantité et autres champs validés via `set_garden_card` | Cet outil remplace les champs de suivi fournis ; lire la fiche avant une réécriture complète. `set_planting_date` préserve les autres champs |
| Tâches | Lire calendrier/tâches, ajouter/modifier/supprimer une tâche manuelle | Mise à jour explicite de l’état de toutes les tâches automatiques |
| Sol | Lire état/incompatibilités, recommandations, `record_soil_test` | Édition/suppression d’un relevé existant et sol par défaut du projet |
| Associations et succession | Suggestions, ensembles compatibles, placement, plans et lacunes de succession | CRUD complet des guildes, rotation culturale et paramètres de propagation |
| Récoltes et ravageurs | Une observation générique peut être consignée dans le suivi ; export CSV des récoltes déjà saisies | CRUD des journaux spécialisés récoltes et ravageurs/maladies |
| Journal avec épingles | Aucun outil dédié | Créer/modifier/supprimer une note et son épingle liée ; distinct du suivi d’interventions |
| Saisons | Aucun outil dédié | Créer/gérer/changer/comparer les saisons |
| Fond et import | Aucun outil dédié | Import DXF, image/satellite, calibration, géoréférencement, verrouillage et visibilité du fond |
| Localisation et soleil | Lecture des métadonnées brutes du projet | Position, nord, date/heure de simulation, calcul/export d’ombre et carte solaire |
| Rendu et exports | `render_canvas_image`, `export_pdf`, `export_dxf`, CSV shopping/récoltes | Export SVG, CSV botanique, rapport PDF complet du jardin, impression et export PNG fichier dédié |
| Vue et 3D | Image du canevas 2D | Zoom/sélection/navigation de l’UI, caméra/marche 3D, éclairage et captures 3D |
| Préférences et application | Pas d’outil dédié | Thème/langue, grilles/accrochages/overlays, panneaux, préférences et mises à jour |
| Historique | `get_history`, `undo`, `redo`, pile commune à l’interface | Pas de transaction arbitraire regroupant plusieurs outils |

## Parcours immédiatement utilisables

Voir les [exemples d’usage](PLANTES_ET_DESSIN.md#piloter-le-jardin-par-mcp).
Un récit de plantation peut donner lieu à une date de plantation sur la fiche et à
un événement `planting` : ce sont deux faits distincts, deux commandes annulables.
Une recommandation, un projet et une intervention réalisée ne sont pas équivalents.
Les dates partielles gardent leur précision ; elles ne deviennent jamais un jour
inventé dans le champ botanique `planting_date`.

La suppression d’une contrainte s’adresse à son identifiant propre, jamais à un
indice de liste. Elle ne lance pas le solveur et ne déplace pas les objets. Une
répétition après suppression est refusée sans ajouter de commande à l’historique.
L’annulation restaure également la troisième ancre des contraintes d’angle,
point-sur-droite ou tangence, qui était perdue par l’ancienne commande partagée.

## Ordre conseillé pour atteindre la couverture complète

1. Parcours quotidiens : propriétés d’objet (nom, style, matière, hauteur, texte),
   journaux spécialisés et tâches, localisation/nord.
2. Édition du dessin : contraintes en création/édition avec solveur, alignements,
   groupes, copie, réseaux et opérations géométriques.
3. Entrées/sorties : fond calibré, imports/exports manquants, catalogue personnel,
   saisons et rotation/guildes.
4. Pilotage de la présentation : vue 2D/3D, simulation solaire, panneaux et réglages.

Chaque famille doit réutiliser la commande/service existant, valider les entrées
avant mutation et exposer une lecture de contrôle. Les commandes graphiques passent
sur le thread Qt ; les changements documentaires gardent l’undo et la sauvegarde
atomique. Éviter un outil « exécuter du Python » ou un dictionnaire de mutations
brutes : il contournerait les contrats que les outils actuels protègent.

## Preuves de ce lot

- `test_agent_api_writes.py` : consultation/suppression d’une jonction et d’un angle
  via HTTP MCP authentifié, refus sans jeton, rejeu refusé, géométrie inchangée,
  undo/redo et fichiers isolés restauré/supprimé puis réouverts ; verrou de la
  troisième ancre protégé.
- `test_garden_memory_mcp.py` : date exacte/partielle/inconnue, maintien de la zone
  et de l’état, refus d’une date impossible, absence de doublon sur rejeu, récit,
  correction annulée et sauvegarde-réouverture du suivi.
- `test_constraint_tool.py` : régression reproduite avant correction pour les
  troisièmes ancres ANGLE, POINT_ON_EDGE et TANGENT.
- `test_agent_api_auth.py` : inventaire exhaustif des écritures et gate commun.
