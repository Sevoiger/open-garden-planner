# Revue transversale métier, MCP et stockage — 27 octobre 2026

Base examinée : `3791260`. Cette revue porte sur les frontières métier partagées,
leurs adaptateurs et leurs appels réels. Elle ne certifie pas chaque dialogue,
chaque outil de dessin ni chaque combinaison de données. Les constats ci-dessous
séparent les invariants vérifiés des limites. Tous les essais de mutation utilisent
les fixtures isolées de `tests/conftest.py` ou les jeux synthétiques des benchmarks.
Aucun plan, bibliothèque ou fichier personnel n'a servi aux essais.

Références : [ADR-065 et index d'architecture](../09-architecture-decisions/README.md),
[contrat des hôtes](METIER_PARTAGE.md), [profil existant](MCP_PERFORMANCE.md).
Les mesures de performance ont guidé l'examen des captures et des révisions ;
les défauts ont été établis par lecture des appels et reproductions séparées.

## Cartographie des responsabilités

Conventions : **D** = outils `prepare_document_operations` / `apply_document_operations`
appelant `services/document_operations.py::prepare_operations` ; ils sont disponibles
en direct et dans l'hôte autonome pour les opérations explicitement listées.
**Q** = document vivant sur le thread Qt, publication par commandes, undo partagé,
persistance lors de la sauvegarde. **A** = `DocumentSession.dispatch`, sous `RLock`,
historique de session ; données et reçu écrits avant succès. **B** = bibliothèque
botanique indépendante, `LibraryPort` → `LibraryOperations`, verrou et transaction
fichier, historique distinct. Aucun lot ne traverse simultanément Q/A et B.

| Domaine | Entrées Qt / MCP direct / D / autonome | Règles et publication | Vérité, stockage, garanties |
| --- | --- | --- | --- |
| Plantes placées | Galerie, listes, propriétés ; `create_object`, `set_species`, `set_parent_bed`, `set_planting_date` ; D pour date/fiche seulement | `ui/plant_creation.py`, `ui/plant_species_assignment.py`, `GardenMemoryService`, `garden_operations` ; géométrie par commandes Qt | Espèce embarquée et instance dans metadata du `.ogp` ; Q. L'autonome conserve la géométrie brute, ne construit pas de plante graphique. |
| Catalogue | Panneau/éditeur/import ; lectures espèces et outils bibliothèque préparés ; hors D | `PlantLibrary`, `LibraryOperations`, validations botaniques | Catalogue fourni + `custom_plants.json` ; B. Import n'actualise pas les instantanés déjà placés. Suivi du 28 octobre : lectures et préparation sur worker ; application/undo et rafraîchissement sur Qt, voir [profil](MCP_REACTIVITE_QT.md). |
| Listes | `PlantListsPanel` / `PlantListStore` ; `get_plant_lists`, `get_plant_list`, ressource protégée ; pas D ni autonome | Stockage Qt historique ; MCP lit la même source via bridge | `plant_lists.json`, global ; pas l'historique du projet. Mutations non extraites en transactions documentaires. |
| Graines | Vue et dialogue inventaire ; `get_seed_inventory(scope=...)` ; pas D de mutation | `SeedInventoryStore`, `ProjectManager.set_seed_inventory` pour le stock projet | Deux sources explicites : global `seed_inventory.json`, projet `.ogp`. Autonome : lecture du stock projet seulement. Viabilité calculée distincte du stock observé. |
| Tâches | `TasksView`, calendrier ; outils directs tâches/statut ; D tâches manuelles + statut | `task_operations`, `task_status`, `task_generator` ; commandes et `ProjectManager` | `manual_tasks`, `task_states`, miroir historique `task_completions` ; Q/A. Tâches générées dérivées, identifiants canoniques, pas d'ajout manuel de leur contenu. |
| Fiches de suivi | Propriétés et suivi ; outils fiche/date ; D | `GardenMemoryService` → `garden_operations`, `models/garden_memory` | `metadata.garden_tracking` et mémoire du jardin ; Q/A. Date partielle séparée de l'ancienne date exacte. |
| Interventions | Suivi Qt ; outils directs ajout/édition ; D ajout/édition/suppression | `garden_operations`, `GardenMemoryService` et commande mémoire | `garden_memory.events` et clés directes `requests` ; Q/A. Prévu/réalisé explicites, reçu documentaire séparé du journal métier. |
| Sol | Dialogues relevés, commandes sol ; `record_soil_test`, lectures/conseils ; D ajout de relevé | `soil_assessment.prepare_soil_test_change`, `SoilService` pour lectures et conseils | Historiques `soil_tests` globaux/par zone ; Q/A. Kit qualitatif et ppm distincts ; conseil sans écriture d'une intervention. |
| Récoltes | Vue/dialogue et commandes récolte ; pas de CRUD MCP direct dédié ; D CRUD | `record_operations.prepare_record_operation` et `prepare_command_record_change` | `harvest_logs` ; Q/A. Quantité inconnue distincte de zéro ; effets sur liens du journal préparés avec l'enregistrement. |
| Ravageurs | Dialogue/panneau, commandes ; pas de CRUD MCP direct dédié ; D CRUD | `record_operations`, modèles ravageurs | `pest_disease_logs` ; Q/A. Observation et traitement conseillé ne constituent pas une action réalisée. |
| Journal | Panneau/dialogue et épingles ; pas de CRUD MCP direct dédié ; D CRUD borné | `record_operations`, commandes journal ; épingles dans l'adaptateur Qt | `garden_journal_notes`, liens récolte/ravageur et objets épingles ; Q/A pour corps, Q pour position/épingle. Suppression préparée d'une note épinglée refusée. |
| Successions | Dialogue/panneau rotation ; `set_succession_plan`, suggestions ; D remplacement | `succession_operations` et commande succession ; analyse rotation séparée | `succession_plans` prévu, `crop_rotation` historique ; Q/A. Un plan futur ne devient pas une culture réalisée. |
| Géométrie | Outils/propriétés ; outils directs création/mouvement/sommets/arrangement ; hors D | `geometry_apply`, `arrange.build_arrange_command`, préflight dans `application.py`, commandes | Scène Qt, sérialisation `.ogp` ; Q seulement. Coordonnées scène en cm, pivot des rectangles au centre géométrique. Pas d'exécution graphique autonome. |
| Sauvegarde | Menu et `save_plan` ; autonome via chaque mutation | `ProjectManager.save` / `DocumentSession._write` → `plan_io.write_plan`, `DocumentOwnership` | Bail coopératif + empreinte disque ; publication atomique. Q persiste explicitement, A avant réponse ; backup autonome avant première mutation. |
| Exports | Menus, `export_pdf/dxf/csv`, rendu MCP ; hors D/autonome | `ExportService`, `PdfReportService`, `DxfExportService`, `scene_rendering` | Fichiers dérivés ; scène et rendu sur Qt. Récoltes/achats réutilisent leur agrégation/service. CSV plantes conserve l'origine historique, exprimée dans la scène même en groupe. |

Les services purs partagent effectivement la validation des tâches, statuts,
fiches/interventions, sol, enregistrements et successions. Les adaptateurs de
publication restent distincts : commandes Qt spécialisées, patchs de lot, session
fichier. Le stockage des listes/graines et les interactions graphiques ne sont
pas devenus des services métier universels. `application.py` reste un gros
adaptateur et contient encore de la validation graphique ; aucun déplacement
cosmétique ni nouveau framework n'a été ajouté.

## Défauts reproduits et corrigés

| Gravité | Scénario avant correction | Correction et preuve |
| --- | --- | --- |
| P1 | Sauvegarder via le menu, modifier le fichier depuis un autre hôte après libération du bail, sauvegarder encore : le contenu externe était écrasé. | Bail conservé jusqu'à fermeture acceptée ; `ProjectManager.save` garde sa précondition disque même après réacquisition. `test_manual_save_keeps_document_lease_and_refuses_external_overwrite` et `test_close_cancellation_keeps_document_lease_and_accepted_close_releases_it`. |
| P1 | Ancienne lecture MCP capturée puis modification imbriquée Qt pendant son traitement worker : certaines tâches/relevés/metadata du snapshot suivaient encore le document vivant. | Copie profonde dans le callable Qt de `_agent_snapshot`, avant franchissement du bridge. `test_agent_snapshot_detaches_nested_project_state_before_worker_returns` synchronise capture et édition sans délai arbitraire. |
| P1 | Lire `garden://plant-lists` sans jeton exposait les listes globales alors que les outils équivalents exigeaient le jeton. | Même activation et authentification que les outils personnels ; tests de refus et lecture autorisée. Changement de contrat explicite dans `METIER_PARTAGE.md`. |
| P1 | Une ouverture remplaçait les objets, puis échouait pendant la publication des métadonnées : les anciennes commandes restaient dans les piles et pouvaient rejouer des objets de l'ancien document. | `_apply_to_scene` vide l'historique à la frontière destructive après préparation. `test_load_history_follows_destructive_boundary` couvre échec de prévalidation (historique conservé), publication et succès (piles vidées). |
| P1 | Le stock global de graines était ouvert en écriture avant sérialisation : une erreur après le premier fragment tronquait l'ancien fichier. Les extensions de paquet inconnues étaient aussi perdues à la réécriture. | `SeedInventoryStore.save` publie via temporaire dans le même dossier et remplacement atomique. Tests d'échec partiel, échec de remplacement, réouverture et conservation d'extension après édition ; suppression puis réutilisation d'ID sans résurrection de l'extension. |
| P2 | Éditer sans changement une tâche historique sans date produisait un aperçu « unchanged », mais ajoutait `date=""` et une étape undo. | Le préparateur ne publie pas `after` si `is_noop`. `test_unchanged_legacy_manual_task_is_not_normalized_or_undoable` vérifie aperçu, reçu, historique et fichier rouvert. |
| P2 | Arrêter le bridge après démarrage d'une mutation renvoyait le même message que l'abandon d'une requête encore en file. | `abort_pending` distingue absence d'exécution et résultat indéterminé ; tests par événements prouvent absence d'écriture tardive en file et écriture possible après démarrage. |
| P2 | CSV : `current_spread_cm=0` ou `null` remplacé par l'ancien diamètre ; plante dans un groupe déplacé/tourné exportée en coordonnées locales. | Fallback uniquement si champ absent, `scenePos()` pour le repère plan. Tests CSV avec zéro/inconnu/valeur et transformation réelle de groupe. |
| Consolidation | Les préparations de l'ancien document restaient en cache jusqu'à expiration, bien que la révision empêchât leur application. | Invalidation explicite à chaque changement d'epoch, sauvegarde comprise comme auparavant pour la révision. Tests service et adaptateur : ancien token refusé, aucune mutation ni commande. |

Références d'implémentation :
[`ProjectManager`](../../src/open_garden_planner/core/project.py),
[`application`](../../src/open_garden_planner/app/application.py),
[`bridge`](../../src/open_garden_planner/agent_api/bridge.py),
[`serveur`](../../src/open_garden_planner/agent_api/server.py),
[`adaptateur vivant`](../../src/open_garden_planner/app/domain_adapter.py),
[`opérations`](../../src/open_garden_planner/services/document_operations.py),
[`stock de graines`](../../src/open_garden_planner/models/seed_inventory.py),
[`exports`](../../src/open_garden_planner/services/export_service.py).

## Invariants et preuves examinés

| Invariant | Examen et preuve adaptée |
| --- | --- |
| Un lot refusé ne publie rien | Préparation sur copie, validations successives sur candidat, patches contrôlant tous les avant-valeurs ; échec milieu de lot et échec disque dans `test_document_operations`, conflits bibliothèque et écriture atomique dans `test_library_operations`. |
| Préparer ne réserve pas l'état ; appliquer revalide | Révision complète et epoch relus sur Qt ou sous `DocumentSession._mutex`, contrôle des pièces jointes puis application. Séquence édition Qt après préparation refusée dans `test_live_preview_apply_undo_and_gui_conflict`. |
| Snapshot et révision représentent une même capture | `LiveDocumentOperations.capture_read` capture données + génération sur Qt, hash et projection sur worker ; `test_live_read_captures_on_qt_and_projects_detached_data_on_worker`. Anciennes lectures renforcées séparément. |
| Pas d'objet Qt dans les calculs autonomes | `DocumentSession`, opérations et projections sans Qt ; tests de frontière d'import et `test_autonomous_without_qt` dans un interpréteur bloquant Qt. Construction/rendu restent sur Qt. |
| Réponse perdue ne justifie pas un nouvel ajout | Reçu stocké avec l'effet, intention vérifiée au rejeu ; lookup après réponse perdue/redémarrage, replay après undo sans réapplication. Tests HTTP réel `test_autonomous_http_auth_batch_retry_and_reopen` et suites document/bibliothèque. |
| Reçu historique distinct d'état courant | `_receipt_result` compare les valeurs touchées aux empreintes avant/après : `present`, `reverted`, `changed`. Cela ne prouve pas qu'un undo a causé le retour. Le reçu reste hors du patch inverse. |
| Erreur disque ≠ succès | `write_plan` écrit temporaire puis remplace ; session ne publie mémoire/révision qu'après succès. Tests d'injection d'échec gardent fichier, reçu et piles ; bibliothèque teste aussi le CAS externe. |
| Historiques séparés | Qt et autonome emploient `CommandHistory`, avec portées de session distinctes. `LibraryOperations.undo` utilise versions et transaction botanique, sans toucher la pile document. |
| Inconnu et valeurs explicites conservés | Dates/quantités/champs historiques dans `test_garden_operations`, `test_record_operations`, `test_task_operations`, `test_library_operations`, `test_project_data_pure` ; régression CSV pour faux fallback. |
| Identité stable et liens | UUID des objets gardé dans les patches indexés ; reorder/refus undo testé. Suppression récolte et lien journal préparés ensemble, commande ravageur undo exact, notes épinglées refusées dans `test_record_operations`. |
| Pas de conseil enregistré comme observation | `assess_document_soil` lit et calcule sans mutation ; intervention explicite distincte. `test_garden_soil_batch_and_advice_are_distinct_from_completed_work`. |
| Pas de tâche fantôme lors d'une opération inchangée | Le service définit le no-op sémantique, les adaptateurs n'enregistrent pas de commande ; la représentation historique du document est désormais conservée par D aussi. |

## Concurrence et annulation

Parcours vivant : coroutine MCP → worker AnyIO → `MainThreadBridge` → capture
ou préparation/application Qt → réponse Python. Pour les lectures documentaires,
le hash et la projection se font après retour sur worker. Les autres lectures
historiques capturent désormais une copie profonde avant de quitter Qt.
Depuis le suivi du 28 octobre, les lectures et préparations bibliothèque sont
sur worker ; les écritures restent sur Qt. Les verrous de lecture et écriture
sont séparés, avec revalidation disque à l’application ([preuves](MCP_REACTIVITE_QT.md)).

Parcours autonome : coroutine → worker → `DocumentSession._mutex` → snapshot/
préparation/application → vérification version disque → sauvegarde atomique →
publication mémoire → réponse. Le port bibliothèque prend son mutex avant son
verrou fichier non bloquant. Le bridge tient son verrou pour les transitions de
Future, le relâche avant d'appeler Qt. Aucun ordre inverse vers ces verrous n'a
été trouvé dans les appels examinés.

Avant démarrage Qt, timeout/arrêt termine la Future : le slot en file ne lance
pas le callable. Après démarrage, il ne peut pas annuler la mutation ; le client
doit lire le reçu ou l'état avant tout retry. Une déconnexion HTTP ne constitue
pas une annulation métier. Les outils sans clé persistante ne deviennent pas
idempotents : préférer une opération absolue ou un lot à reçu, selon l'intention.

Les écritures vivantes publient l'ensemble des stores avant les signaux de
rafraîchissement, reçu compris. Les slots métier examinés rafraîchissent ou
planifient les vues ; aucun callback de mutation réentrant n'a été trouvé.
Un futur slot réentrant devrait préserver l'ordre de l'historique : cela ne
constitue pas une garantie générale contre des callbacks arbitraires.

## Compatibilités et limites maintenues

- Une date partielle reste une période de suivi ; seule une date exacte est
  projetée dans l'ancien champ `planting_date`. Une date inconnue reste inconnue.
- Création graphique Qt depuis catalogue peut proposer aujourd'hui ; MCP crée
  une date inconnue. Ce défaut historique de saisie est documenté, pas migré.
- Le relevé sol MCP direct conserve aujourd'hui si date omise ; D exige une date
  explicite. Kit qualitatif, ppm et dosage selon surface ne sont pas confondus.
- Le dialogue succession peut accepter un chevauchement avec avertissement ;
  les mutations agent le refusent. Historique de rotation et plan prévu restent
  séparés ; aucune culture historique n'est créée par une recommandation.
- L'édition historique d'une tâche est un remplacement de ses champs connus,
  pas un PATCH universel. Les extensions inconnues sont conservées.
- L'ouverture Qt peut importer des espèces custom manquantes dans la bibliothèque
  (`ProjectManager._sync_custom_plants`) ; l'autonome n'effectue pas cet effet.
  L'échec de cette synchronisation est journalisé sans refuser l'ouverture.
- Les épingles, positions de notes, contraintes et transformations restent Qt.
  Un test offscreen de coordonnées ne démontre pas l'acceptation visuelle native.
- Suivi du 28 octobre : les stores globaux listes/graines distinguent désormais
  erreur et collection vide, bloquent les mutations après échec de lecture et
  publient sous verrou avec précondition disque. Les modifications en mémoire
  sont conservées après échec de sauvegarde ; reprise explicite dans l’UI.
  Ils ne partagent ni historique ni reçu avec le projet et restent sans écriture
  MCP. Voir [contrat et preuves](STORES_PERSONNELS.md).
- Les outils historiques de sauvegarde/exports suivent le modèle de confiance
  loopback et permettent des chemins explicites ; cette revue ne change pas
  leur politique d'autorisation ou d'écrasement Save As.
- Le verrou est consultatif. Un programme qui l'ignore peut écrire entre contrôle
  disque et remplacement ; aucune exclusion universelle du système de fichiers
  n'est annoncée. La détection CAS couvre les modifications déjà observables.

## Étendue effective et suites justifiées

Ont été relus les préparateurs cités, les frontières des hôtes et du stockage,
les appels directs MCP correspondants, les commandes Qt qui les réutilisent,
les appels de sauvegarde/ouverture et les primitives communes de géométrie/export.
Les listes et graines étaient seulement cartographiées dans cette revue ;
le [suivi ciblé](STORES_PERSONNELS.md) examine leurs formats et surfaces UI.
La géométrie a été examinée aux points d'entrée et aux primitives partagées ;
ce document ne revendique pas la revue de tous les solveurs, outils ou symboles.
Pas de revue exhaustive du fournisseur botanique distant, de la météo, de la 3D,
de l'impression native ni des calculs agronomiques de toutes les recommandations.

Les risques restants justifient des travaux ciblés seulement si ces parcours
évoluent : transactions des listes/graines avant exposition MCP en écriture,
étude de saturation au-delà de la campagne bornée à 32 requêtes
([suivi](MCP_REACTIVITE_QT.md)), et revue des nouveaux callbacks qui
muteraient pendant les signaux. Aucun défaut supplémentaire n'est inféré du seul
fait qu'un service est large ou qu'un stockage historique reste propre à Qt.

## Validation technique et mesures

Sur le diff de cette revue :

- 124 tests des services purs, frontières d'import sans Qt, historique,
  bibliothèque et projections réussis ;
- 203 tests des frontières authentification/bridge, export, validations
  géométriques et projections réussis ;
- 38 tests chargement/historique, sérialisation et compatibilité objets/overlay
  réussis ;
- 144 tests d'intégration réussis ensemble : providers Qt réels, HTTP MCP,
  exports, lifecycle/arrêt, domaines jardin/tâches/sol/successions/géométrie,
  conflits d'éditeur botanique. Cinq avertissements de dépréciation du nom du
  client HTTP SDK, sans échec ;
- le dernier ajustement issu de revue (capacité de ressource selon hôte/provider)
  est vérifié séparément par les suites auth et serveur HTTP : 39 réussis ;
- 84 tests du stock de graines, dialogue et lien plante–lot réussis après le
  complément de publication atomique et de conservation des extensions ;
- Ruff global `src/ tests/ scripts/` et `git diff --check` propres.

Ces groupes se recouvrent ; leurs nombres ne sont pas additionnés. Les refus
d'ouverture d'un port loopback dans le sandbox ont été levés en relançant les
tests concernés avec l'autorisation d'exécution locale. Un crash Qt intermittent
`ComboWheelGuard` avait été signalé dans un run parallèle d'agent ; il n'est pas
apparu dans le run consolidé. Aucune assertion n'a été assouplie pour le masquer.
Le [ticket de diagnostic](COMBO_WHEEL_GUARD_INVESTIGATION.md) conserve la trace
et les essais répétés, sans annoncer de résolution. Pas de test d’acceptation
visuelle native pour ces corrections de frontières.

Les benchmarks existants sont conservés. Rapports :
[`mcp-audit-reads.json`](mcp-audit-reads.json) et
[`mcp-audit-qt.json`](mcp-audit-qt.json), 12 répétitions, jeux synthétiques
décrits dans `MCP_PERFORMANCE.md`. Commandes reproductibles :

```sh
.venv/bin/python scripts/benchmark_agent_reads.py --output /tmp/audit-reads.json --repeat 12
.venv/bin/python scripts/benchmark_agent_qt.py --output /tmp/audit-qt.json --repeat 12
```

| Surveillance existante | p95 observé | Budget de surveillance |
| --- | ---: | ---: |
| Tâches, 2 000 objets | 0,231 ms | < 1 ms |
| Tâches, 10 000 reçus | 0,628 ms | < 2 ms |
| Tâches, 8 Mo | 3,172 ms | < 5 ms |
| Page bibliothèque, 2 000 fiches | 12,251 ms | < 25 ms |
| Recherche bibliothèque, 2 000 fiches | 26,151 ms | < 30 ms |
| Section Qt, 8 Mo, concurrence 1 / 4 | 0,468 / 0,483 ms | < 1 ms |
| Section Qt, 2 000 objets, concurrence 1 / 4 | 38,580 / 42,092 ms | < 45 ms |

Le premier relevé historique, pris pendant des essais parallèles, dépassait
ponctuellement 2 ms (2,911 ms). Le relevé de lectures archivé a été repris seul
pour lever ce doute ; aucune modification de performance n'a été ajoutée.
Le relevé Qt a été pris sous charge de travail concurrente et reste indicatif.
Le benchmark Qt mesure les lectures documentaires préparées, déjà détachées ;
il ne mesure pas le coût ajouté de la copie profonde des anciennes lectures
`providers.snapshot`. Ce coût demeure une limite explicite, plutôt qu'un gain
annoncé sans mesure. Les budgets observés ne prouvent ni la correction métier,
ni une latence connecteur, ni la fluidité d'une fenêtre native.

Revue indépendante `senior-reviewer` effectuée avec Luna Max sur le diff de
production et ses régressions. Le défaut de capacité autonome relevé pendant
la revue a été corrigé, puis les changements d'authentification finaux revus.
Les résultats de tests et mesures ne remplacent pas les invariants et limites
de couverture détaillés ci-dessus.
