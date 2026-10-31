# Revue du parcours jardin — 31 octobre 2026

**Suivi :** le propriétaire a demandé la suppression de la date automatique et
la correction des dettes ci-dessous. Voir le guide [Plantes et dessin](PLANTES_ET_DESSIN.md)
pour le comportement actuel ; les constats et chiffres de cette revue restent
ceux de sa première livraison.

Base examinée : `b3f347d3`, puis corrections locales de cette mission. Cette
revue confronte les rapports précédents aux points d’entrée et à des essais
isolés ; elle ne certifie ni chaque outil de dessin, ni chaque formule métier.
Aucun plan ou catalogue personnel n’a servi aux essais.

## Parcours et frontières examinés

| Parcours | État vérifié dans le code et preuves |
| --- | --- |
| Ouvrir, enregistrer, fermer | `app/application.py::_load_project_file`, `_save_to_file`, `_confirm_discard_changes` et `closeEvent` ; `core/project.py::save/load` et `core/plan_io.py`. Les tests couvrent échec d’ouverture, contrôle de version disque, refus d’écrasement, annulation de fermeture et libération du bail. Le verrou est coopératif : une écriture externe qui l’ignore peut encore intervenir entre contrôle et remplacement. |
| Chercher, consulter et placer | Panneaux `plant_search_panel.py`, `plant_database_panel.py`, construction `ui/plant_creation.py`, dépôt `canvas_view.py` et listes. Les essais existants exercent recherche/association, dépôt, rattachement et undo ; les nouveaux ciblent la date de la fiche. Le choix historique de proposer le jour du placement demeure, bien qu’il ne prouve pas une plantation réelle. |
| Sélection, déplacement, propriétés | `core/tools/select_tool.py`, `ui/canvas/geometry_apply.py`, `ui/panels/properties_panel.py`, commandes. Les tests de sélection et de redimensionnement passent ; ce n’est pas une revue exhaustive des contraintes ni de chaque outil. |
| Catalogue, listes, graines | `services/plant_library.py`, `library_operations.py`, `models/plant_lists.py`, `models/seed_inventory.py`, panneaux correspondants. Les tests vérifient notamment les erreurs de lecture et de sauvegarde, la conservation en mémoire et la reprise explicite. Ces stores globaux ne partagent pas l’undo du plan. |
| Tâches, interventions, observations | `services/task_operations.py`, `garden_operations.py`, `record_operations.py`, `ui/views/tasks_view.py`, `garden_memory_panel.py`. Les tâches Qt et calendrier convergent ; les opérations documentaires et le suivi utilisent des préparateurs et commandes partagés. Les dates partielles et observations ne deviennent pas des dates exactes ou des interventions réalisées. |
| Préparer / appliquer MCP | `agent_api/document_tools.py`, `app/domain_adapter.py`, `services/document_operations.py`, `agent_api/bridge.py`, `core/document_session.py`. Tests HTTP réels : authentification, conflit, rejeu, annulation et réouverture. L’application vivante publie sur le thread Qt ; une requête déjà commencée à l’arrêt peut avoir un résultat indéterminé. |

## Diagnostic priorisé

Les coûts et risques ci-dessous sont des ordres de grandeur qualitatifs, pas des
estimations chronométrées. Les éléments reproduits sont distingués des risques
déduits du code.

| Priorité et nature | Scénario, impact et preuve | Intervention, effort et vérification |
| --- | --- | --- |
| P1 — défaut corrigé | Sauvegarder après retrait d’une plante supprimait son prix avant l’écriture ; si l’écriture échouait, le prix avait déjà disparu en mémoire. Échec reproduit sur fenêtre Qt avec erreur disque injectée ; même appel d’élagage dans `agent_api/exports.py`. Une sauvegarde réussie suivie d’undo perdait également ce prix. | Les deux entrées de sauvegarde conservent les prix. Effort faible, risque de persistance : `test_save_preserves_prices.py` couvre les deux entrées, l’échec, la fermeture refusée, undo et réouverture. Compromis et risque résiduel ci-dessous. |
| P1 — défaut corrigé | Modifier la date dans les détails écrivait directement `plant_instance`, sans commande : `undo_depth` restait à zéro dans une fenêtre réelle. L’affichage après undo n’était pas rafraîchi de manière fiable. | La fiche passe par `GardenMemoryService.set_planting_date` et la commande partage l’historique. L’UI propose « Date inconnue », conserve les périodes partielles et affiche une erreur si la validation échoue. Effort moyen ; conflit avec une préparation MCP, undo/redo, notes ultérieures et sauvegarde/réouverture sont testés. |
| P1 — compatibilité corrigée | Des fiches historiques peuvent stocker notes et cultivar dans `garden_tracking`. La revue indépendante a reproduit leur disparition avec une première version de la commande ; la projection antérieure masquait aussi ces champs lorsque `plant_instance` ne les contenait pas. | Le préparateur garde les valeurs historiques lors d’un changement de date ou lorsqu’un éditeur ne les modifie pas ; la projection les utilise en repli. Tests sur candidat partagé, application, undo, réédition et champs absents/nuls. Effort moyen ; risque de divergence Qt/MCP couvert par tests du service et HTTP. |
| P2 — dette démontrée, non corrigée | `PlantDatabasePanel._update_instance_metadata` modifie encore directement notes, variété et champs personnalisés ; ces éditions ne bénéficient pas toutes de l’historique. `GardenEditCommand` restaure un instantané complet de métadonnées : une autre commande de fiche, suivie d’une édition directe, mérite un examen ciblé pour éviter d’écraser celle-ci lors d’undo. Le correctif de date protège spécifiquement sa propre commande. | Consolider ces éditions dans des commandes à granularité adaptée, avec regroupement de texte et tests de changements croisés. Effort moyen/élevé ; dépendances : focus, debounce, rafraîchissements et compatibilité des fiches. Le risque d’interaction est déduit du code, pas présenté comme incident utilisateur reproduit ici. |
| P2 — risque résiduel du correctif | Les prix d’articles absents restent dans le `.ogp`, y compris après fermeture et partage. Les espèces identifiées partagent volontairement leur clé ; toutes les plantes sans espèce utilisent `plant:_unknown`. La revue a reproduit la réutilisation du prix générique pour une autre plante non identifiée. | Concevoir une purge explicite et une identité des prix qui distinguent les intentions, sans casser les anciennes clés ni l’undo. Effort moyen ; tester partage, réouverture, ajout d’une autre plante et suppression/undo. En attendant, vérifier le prix des plantes non identifiées. |
| P2 — dette de test | `test_editing_current_spread_shrinks_icon_without_shrinking_footprint` échoue : il écrit dans un champ devenu lecture seule. Le champ et le test sont déjà identiques à la base `b3f347d3`. | Adapter séparément cette régression au parcours réel « Mesures et historique », en conservant la preuve sur l’icône et l’empreinte. Effort faible ; ne pas masquer l’échec actuel. |

La conservation de prix est un compromis explicite : elle protège l’annulation
et l’échec de sauvegarde mais ne constitue pas une solution définitive au
cycle de vie de ces données. Aucune purge automatique des fichiers personnels
existants n’a été effectuée.

## Observations visuelles et limites

Les seules observations visuelles nouvelles concernent le panneau de détails
d’une plante fictive, en français et thème sombre, sous Cocoa à 320 et 420 px.
Le premier emplacement du bouton était coupé à 320 px ; il a été déplacé sur
une ligne visible. Captures locales dans `output/plant-date-review/` ; « avant »
utilise le panneau de la base Git, « après » le panneau modifié. Les autres
constats UI proviennent du code ou des tests, pas d’une inspection native de
tous les panneaux, thèmes, lecteurs d’écran ou tailles de fenêtre. Le bouton a
un nom accessible ; le parcours complet au lecteur d’écran n’a pas été évalué.

Pas de modification de performance intentionnelle : aucune mesure avant/après
n’est revendiquée. Le service de date prépare un instantané documentaire, comme
le chemin MCP existant ; sa latence sur un très grand plan n’a pas été mesurée
pendant cette mission. Les précédents profils MCP restent bornés aux conditions
documentées dans `MCP_REACTIVITE_QT.md`.

## Vérifications de la mission

- 119 tests ciblés passent ensemble : nouvelles régressions, fiches historiques,
  opérations métier, listes d’achats et résolution des chemins d’export.
- 14 tests MCP supplémentaires passent sur HTTP loopback réel, puis 8 tests
  d’export MCP ; les refus de connexion initialement obtenus dans le sandbox
  disparaissent hors sandbox. Les fixtures redirigent réglages et fichiers.
- Huit tests des nouveaux parcours date et sauvegarde passent aussi sous Cocoa.
  Deux campagnes de revue indépendante ont couvert les commandes puis les
  sauvegardes ; les défauts signalés ont été corrigés ou sont documentés comme
  risques résiduels ci-dessus.
- Ruff global, `git diff --check` et le contrôle des traductions EN/DE/FR
  compilées passent. Une campagne élargie a en outre donné 143 réussites et six
  échecs : cinq interdictions réseau du sandbox, ensuite levées par la campagne
  loopback ci-dessus, et le test hérité du champ de mesure en lecture seule.
  La suite complète n’a pas été exécutée.

## Pistes produit, par intérêt

1. **Dates et certitude au placement.** Besoin : représenter un arbre existant
   sans inventer son jour de plantation. Exemple : placer un pommier déjà
   présent et choisir « inconnue » ou « automne 2023 ». Valeur : suivi et
   simulation plus fiables. Première version : choix facultatif au placement,
   expliquant le compromis sur la croissance. Dépendances : modèle de périodes
   existant et choix historique du jour courant ; risque de friction à chaque
   placement. Critère : essais utilisateur montrant des corrections fréquentes
   de date ou une préférence nette pour un choix explicite.
2. **Prochaines actions contextualisées.** Besoin : savoir quoi faire dans son
   jardin cette semaine. Exemple : filtrer les tâches ouvertes par massif et
   période, puis marquer une taille réalisée. Valeur : passer du plan au suivi.
   Première version : filtre des tâches existantes avec lien vers la cible et
   état vide explicite. Dépendances : identifiants et statuts existants ; risque
   de présenter une suggestion comme une intervention. Critère : l’utilisateur
   retrouve et clôt une action plus vite qu’avec les vues actuelles.
3. **Historique de plantation lisible sur le plan.** Besoin : comprendre ce qui
   a changé autour d’une plante. Exemple : sélectionner un arbre et voir dates,
   mesures et dernières observations liées. Valeur : retrouver les faits sans
   parcourir plusieurs panneaux. Première version : chronologie en lecture
   seule limitée à une cible, avec dates inconnues explicites. Dépendances :
   mémoire, mesures et journal ; risque de doublons et de confusion entre prévu
   et réalisé. Critère : retrouver une observation connue en quelques actions.
4. **Liste d’achats réconciliable.** Besoin : contrôler les prix conservés et
   les articles disparus. Exemple : afficher les anciennes lignes et supprimer
   volontairement un prix obsolète. Valeur : éviter prix trompeurs et données
   résiduelles dans un plan partagé. Première version : aperçu des prix non liés
   et suppression explicite avec confirmation et undo. Dépendances : clés
   historiques et historique du projet ; risque de supprimer des prix utiles à
   une annulation. Critère : reproduction des scénarios de suppression/undo et
   ajout d’une plante inconnue sans attribuer silencieusement un ancien prix.
