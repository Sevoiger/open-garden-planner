# Fiabilité des listes personnelles et du stock de graines

Les listes de plantes et le stock global de graines sont des fichiers JSON
séparés du projet `.ogp`. Leur lecture, leur écriture Qt et leurs lectures MCP
partagent maintenant un état de stockage explicite.

## Défauts corrigés et frontières

| Gravité | Reproduction avant correction | Frontière corrigée |
| --- | --- | --- |
| P1 | Charger un JSON corrompu, puis créer une liste ou sauvegarder le stock : l’état vide de substitution pouvait remplacer le fichier existant. | `PlantListStore._load/save`, `SeedInventoryStore._load/save` et getters refusent l’état `failed` ; surfaces Qt de reprise et erreurs MCP. |
| P1 | Deux stores chargent le même fichier, le premier enregistre, puis le second enregistre son ancien état. | `core/personal_store.py::write_json`, verrou coopératif et empreinte exacte, publication par `plan_io.write_bytes_atomically`. |
| P2 | Réécrire une liste ou son snapshot botanique effaçait des champs inconnus ; une date d’ajout inconnue devenait aujourd’hui. | `PlantListEntry`/`PlantList` conservent les extensions et la présence de la date historique. |
| P2 | Éditer seulement une note de paquet arrondissait sa quantité, convertissait zéro en inconnu ou perdait des champs hors formulaire. | `SeedPacketEditDialog.get_packet` garde les valeurs originales des champs non modifiés ; test sauvegarde–réouverture. |
| P2 | Une durée représentable en JSON et en timedelta débordait lors de son addition à la date réelle du projet, puis faisait échouer le callback Qt. | `task_generator.CalendarDataError` encadre les fenêtres, la propagation et les ancres ; les vues Tâches/Calendrier affichent une erreur explicite et MCP propage l’erreur. |

Sources : [stores listes](../../src/open_garden_planner/models/plant_lists.py),
[graines](../../src/open_garden_planner/models/seed_inventory.py),
[I/O commune](../../src/open_garden_planner/core/personal_store.py),
[éditeur](../../src/open_garden_planner/ui/dialogs/seed_inventory_dialog.py),
[bandeau de reprise](../../src/open_garden_planner/ui/widgets/personal_store_status.py).

## États de lecture

| État | Signification |
| --- | --- |
| `absent` | Le fichier n’existe pas encore. Le stockage est utilisable et vide ; les favoris restent accessibles en mémoire pour les listes. |
| `empty` | Le fichier est valide et contient zéro liste personnelle ou zéro paquet. |
| `loaded` | Le fichier valide contient des listes/entrées ou des paquets. |
| `failed` | Lecture, décodage ou validation impossible. Le contenu n’est pas présenté comme vide et aucune mutation ni sauvegarde n’est permise avant un rechargement réussi. |

Les listes acceptent leur ancien format JSON racine en tableau ainsi que
l’enveloppe `{ "version": "1.0", "lists": [...] }`. Les stocks de graines
doivent être des tableaux JSON. Les deux lecteurs refusent les structures
connues mal formées, les identifiants manquants ou dupliqués, les clés JSON
dupliquées, `NaN` et les infinis. Une erreur laisse les octets d’origine
intacts. Les champs d’extension inconnus sont conservés à la racine du fichier,
des listes, des entrées, des snapshots botaniques et des paquets pendant les
modifications ordinaires.

Pour une entrée de liste, `note` doit être une chaîne lorsqu’il est présent.
`added_at` peut être absent, `null` ou vide : ces trois formes représentent une
date inconnue et restent inconnues lors du chargement. Une entrée créée par
l’application reçoit son horodatage normal. Pour un paquet de graines,
`purchase_year` et `cold_stratification` acceptent leurs valeurs par défaut
historiques lorsqu’ils sont absents, mais une valeur `null` est invalide.
Les champs entiers édités par `QSpinBox` doivent tenir dans l’intervalle signé
32 bits pris en charge par Qt ; le lecteur refuse un paquet hors intervalle au
lieu de tronquer sa valeur lors de l’édition. Dans cet intervalle, l’éditeur
élargit le champ si nécessaire pour afficher la valeur stockée, sans ajouter de
limite agronomique supplémentaire. Une modification de note ne normalise pas
les autres valeurs du paquet : les champs non modifiés restent ceux du modèle,
y compris une quantité arrondie à l’affichage, un zéro ou une unité historique.
Les champs hors formulaire (identité botanique, photo, date de création) restent
conservés. Les optionnels connus gardent leur convention de sérialisation
historique ; ce travail n’introduit pas de migration générale de leur format.

Les décalages calendaires des snapshots de plantes sont bornés à ±521 722
semaines, soit le plus grand nombre de semaines entières dans l’intervalle entre
`date.min` et `date.max`. Les durées en jours des snapshots et les durées de
germination des paquets sont bornées de 0 à 3 652 058 jours, l’intervalle
représentable par `date`. Ces limites préviennent les dépassements dus à des
valeurs d’entrée démesurées ; une ancre elle-même proche des extrêmes de `date`
peut encore rendre une somme impossible et relève de la validation du calcul
calendaire.

## Sauvegarde, concurrence et reprise

Les changements Qt sont enregistrés dans le même dossier au moyen de l’écriture
atomique partagée avec les plans. Sous le verrou consultatif
`<nom-du-fichier>.writer.lock`, la sauvegarde compare le SHA-256 des octets
chargés avec la version actuelle avant le remplacement. Un conflit ou une
erreur d’écriture refuse la publication. Le verrou coordonne les processus OGP
qui l’utilisent ; la comparaison détecte également les modifications externes
déjà présentes à l’acquisition du verrou.

Après un échec d’écriture, l’édition est conservée en mémoire et signalée comme
non sauvegardée. La commande « Réessayer » tente une nouvelle sauvegarde. La
commande « Recharger depuis le disque… » demande confirmation si elle va
abandonner ces changements en mémoire. Pour un échec de lecture, le panneau
propose « Réessayer le chargement » après réparation du fichier. Les mutations
restent bloquées tant que la lecture n’a pas réussi.

Les panneaux de listes et d’inventaire, la recherche de plantes, le menu des
favoris, les détails botaniques et le calendrier affichent l’indisponibilité
du stockage concerné. Au démarrage, les deux fichiers peuvent être illisibles
en même temps : l’application s’ouvre et chaque surface concernée montre son
état d’erreur. Les getters MCP des listes globales et du stock global propagent
l’échec comme erreur de lecture au lieu de renvoyer une collection vide. Les
outils MCP restent en lecture seule pour ces stockages ; aucune écriture MCP
n’est introduite. Après un échec de sauvegarde, ces lectures exposent l’état en
mémoire encore visible dans Qt ; elles ne constituent pas une preuve de
persistance. Une édition externe n’est pas rechargée automatiquement : elle est
détectée avant publication et la reprise passe par un rechargement explicite.
Le champ `added_at` exposé dans une entrée MCP peut désormais être nul pour
une date historique inconnue ; aucune date n’est fabriquée pour ce cas.

Le stock de graines attaché au projet `.ogp` reste distinct. Lorsqu’une tâche
générée demande un paquet du stock global, l’échec de lecture se propage au
service appelant ; le calendrier Qt l’intercepte pour conserver les tâches
indépendantes des graines et affiche l’état indisponible.

## Vérifications isolées

Les tests utilisent des fichiers temporaires. Les modèles et l’intégration
couvrent les quatre états, les fichiers corrompus ou structurellement invalides,
les dossiers illisibles, les nombres non finis et les JSON excessivement
imbriqués, les champs inconnus, les erreurs de publication, la conservation
des modifications en mémoire, la reprise explicite et les conflits entre
instances. Les tests des surfaces Qt couvrent les états d’erreur, la reprise
par les commandes visibles et la conservation des valeurs entières en dehors
des plages initiales des champs. Les tests MCP couvrent les erreurs de lecture
des listes et du stock global. Pour une modification, exécuter les fichiers de
tests concernés et les contrôles de catalogue prévus par
`docs/TRANSLATING.md` si des traductions changent.

## Limites

Le verrou est consultatif : un programme qui l’ignore peut écrire entre la
comparaison et le remplacement. Le CAS protège les éditeurs coopératifs et les
modifications déjà visibles, pas toute écriture arbitraire du système. Le
remplacement reste atomique ; aucune garantie supplémentaire de survie à une
coupure électrique après rename n’est ajoutée. Les fichiers invalides sont
préservés pour réparation, sans récupération automatique ni écrasement forcé.
Les mutations des stores globaux restent distinctes de l’undo du projet.

## Validation livrée le 28 octobre 2026

**433 tests passent ensemble** dans le groupe de 23 modules : modèles de listes
et graines, dialogue et liaison plante–lot, lectures personnelles MCP, refus de
lecture pour permission, générateur et outils de tâches, transactions et
concurrence bibliothèque, bridge, projet et sérialisation, surfaces listes,
éditorial botanique, opérations partagées, filtre de molette, calendrier et
convergence entre vues. Deux avertissements préexistants concernent le nom
obsolète du client HTTP SDK. Trois anciennes fixtures construisaient le store
avec `__new__` ; elles utilisent désormais un vrai fichier temporaire et le
constructeur, sans affaiblir les assertions.

Le groupe final est reproductible avec :

```sh
.venv/bin/python -X faulthandler -m pytest tests/unit/test_plant_lists_model.py tests/unit/test_seed_inventory.py tests/unit/test_seed_inventory_dialog.py tests/unit/test_seed_to_plant_linking.py tests/unit/test_personal_catalog_tools.py tests/unit/test_personal_store_io.py tests/unit/test_task_generator.py tests/unit/test_agent_task_tools.py tests/unit/test_library_operations.py tests/unit/test_library_port_concurrency.py tests/unit/test_agent_api_bridge.py tests/unit/test_project_data_pure.py tests/unit/test_project.py tests/integration/test_personal_store_reliability.py tests/integration/test_live_library_worker.py tests/integration/test_library_editor_conflict.py tests/integration/test_plant_lists.py tests/integration/test_classic_plant_lists.py tests/integration/test_shared_document_operations.py tests/integration/test_combo_wheel_guard.py tests/ui/test_planting_calendar_view.py tests/integration/test_tasks.py tests/integration/test_calendar_task_convergence.py -q
```

Huit cas de reprise et d’erreur calendaire passent également avec
`QT_QPA_PLATFORM=cocoa` : les six cas de `test_personal_store_reliability` et les
nouveaux cas d’erreur de `TestTasksView` et `TestCalendarTasksTabConvergence`.
Les panneaux de reprise ont été affichés et capturés sur Cocoa en français,
avec les thèmes clair et sombre ; leurs boutons de rechargement récupèrent les
fichiers temporaires réparés. Cela valide ces panneaux, pas toute l’application.

Ruff global, contrôle des traductions compilées EN/DE/FR et `git diff --check`
passent. La revue indépendante `senior-reviewer` Luna Max a relu le diff exact,
les appels et les corrections de dates ; aucun défaut actionnable restant
signalé. Le reviewer n’a pas répété les tests Qt ni les mesures du responsable.
Les invariants de concurrence bibliothèque et les limites de mesure figurent
dans le [profil complémentaire](MCP_REACTIVITE_QT.md). Le
[crash ComboWheelGuard](COMBO_WHEEL_GUARD_INVESTIGATION.md) reste non résolu.
