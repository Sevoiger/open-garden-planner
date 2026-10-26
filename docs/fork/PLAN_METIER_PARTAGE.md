# Plan d’implémentation — métier partagé Qt / MCP

Date : 26 octobre 2026. Référence analysée : `6d8dbdc`.
**Statut : proposition d’implémentation ; aucun comportement applicatif modifié.**
Ce document prépare la migration demandée. Les contrats et noms de modules
proposés ci-dessous ne sont pas des capacités déjà disponibles.

## 1. Résultat attendu et périmètre

Permettre à un agent de lire et modifier les données du jardin par des opérations
métier explicites, avec les mêmes règles que l’interface, sans remplir de
dialogues. Pour les parcours migrés, permettre ensuite leur exécution sans
`QApplication`, scène ou fenêtre OGP, dans un processus qui n’importe pas PyQt6.

Deux modes emploieront les mêmes opérations :

- **Application ouverte** : Qt reste propriétaire du document vivant ; MCP et
  formulaires appellent le service commun. La frontière Qt reste sur le thread
  principal pour lire/appliquer les données encore portées par la scène.
- **Session autonome** : un hôte Python/MCP charge explicitement un projet
  isolé, exécute les opérations migrées et sauvegarde par le même contrat de
  persistance. Les capacités graphiques indisponibles sont annoncées comme telles.

Les premières migrations concernent tâches manuelles, fiches de suivi,
interventions, bibliothèque botanique personnelle, analyses de sol et calculs
d’amendements. Journal, récoltes, maladies, lots de semences et successions
suivent les mêmes frontières, chacun dans un lot vérifiable.

Le rendu, les gestes de dessin, les transformations Qt, les contraintes et les
exports graphiques restent initialement dans leurs adaptateurs actuels. Une
opération d’affectation d’espèce pouvant redimensionner une plante doit annoncer
ces effets et conserver la frontière géométrique ; elle ne sera pas assimilée
à une simple modification de fiche botanique.

## 2. État réel vérifié

| Frontière actuelle | Source | Conséquence pour la migration |
| --- | --- | --- |
| Fournisseurs MCP construits par la fenêtre | `app/application.py::_build_agent_providers`, `agent_api/providers.py` | Conserver l’injection ; séparer les fournisseurs métier des capacités Qt avant d’assembler un hôte autonome. |
| Lectures, validations et mutations orchestrées dans la fenêtre | `application.py::_do_agent_manual_task`, `_do_agent_record_soil_test`, `_do_garden_write` | Extraire le cas d’usage, puis faire appeler le même service par UI et MCP. |
| Suivi déjà commun à UI et MCP, mais lié à la scène | `core/garden_memory.py::GardenMemoryService`, `GardenEditCommand` ; `ui/dialogs/garden_memory_dialog.py` | Séparer règles et adaptateur ; ne pas créer un second suivi parallèle. |
| Commandes et historique dépendants de Qt | `core/commands.py::Command`, `CommandManager` | Extraire progressivement le contrat Python et le moteur d’historique ; conserver l’adaptateur de signaux et les commandes géométriques. |
| Statuts de tâches centralisés mais certains écrits directement depuis la vue | `core/project.py::set_task_status`, `ui/views/tasks_view.py` | Préserver la source unique et intégrer les changements de statut à l’opération commune et à son undo. |
| État projet et sérialisation des items mêlés à Qt | `core/project.py::ProjectData`, `ProjectManager` | Extraire les données sans déplacer aveuglément le gestionnaire de scène. |
| Lecture/écriture JSON atomique déjà présente | `core/plan_io.py::read_plan`, `write_plan` | Réutiliser validation, encodage avant écriture, fichier temporaire, fsync et remplacement ; ajouter la coordination, pas un second format. |
| Préservation explicite de champs supplémentaires | `ProjectData.extra_fields`, `source_metadata` | Conserver les champs inconnus acceptés à chaque niveau traversé ; ne pas réencoder les objets non modifiés avec un modèle incomplet. |
| Bibliothèque botanique globale distincte du projet | `services/plant_library.py::PlantLibrary` | `_save` écrit directement et absorbe `OSError` ; durcir cette frontière avant de promettre des lots fiables. |
| Rejeu limité et déjà documenté | `GardenMemoryService.add_event`, `agent_api/server.py::get_capabilities`, `ui/agent_control.py` | Réutiliser les reçus existants ; distinguer session, sauvegarde et redémarrage. |
| Timeout Qt protégé avant démarrage | `agent_api/bridge.py::MainThreadBridge.run_on_main` | Garder l’annulation des appels en attente ; un appel déjà démarré peut avoir abouti malgré le timeout. |

Les modèles `models/garden_memory.py` et `models/task.py` s’importent sans PyQt6
dans des processus Python séparés. En revanche, les imports normaux de
`core.plan_io`, `services.plant_library` et `agent_api.domain` chargent actuellement
PyQt6 : les `__init__.py` des paquets importent commandes, autosave ou bridge.
`core/object_types.py` importe aussi Qt directement. Une recherche d’imports
dans le seul fichier ciblé ou un test sans `qtbot` ne prouve donc pas l’autonomie.

Autre précondition de migration : les conversions de `ManualTask`,
`SoilTestRecord` / `SoilTestHistory` et `PlantSpeciesData` ne conservent pas
automatiquement les clés futures. Leur mutation doit préserver le dictionnaire
source ou porter explicitement les extensions avant d’être réutilisée hors Qt.
`ProjectManager._sync_custom_plants` peut aussi écrire dans la bibliothèque à
l’ouverture d’un projet : cet effet doit être inventorié et maîtrisé dans
l’hôte autonome ; une simple lecture ne doit pas importer silencieusement des fiches.

Sources de contexte : [couverture MCP](MCP_COUVERTURE.md),
[audit et preuves existantes](MCP_AUDIT_2026-10-26.md),
[décisions d’architecture](../09-architecture-decisions/README.md), notamment
ADR-004, ADR-029, ADR-033/034/036, ADR-054/055 et ADR-059.
L’audit ancien constitue une base de scénarios, pas une validation de cette future migration.

## 3. Architecture cible minimale

```text
Formulaires Qt ──┐
                ├── opération métier commune ── validation / préparation pure
Outils MCP ──────┘                                  │
                                      changements + inverse + préconditions
                                                   │
                                        hôte du document / historique
                                         ├── adaptateur Qt vivant
                                         └── dépôt fichier autonome
```

Les noms suivants sont indicatifs ; conserver les modules existants quand ils
offrent déjà la bonne frontière :

- Données projet pures : extraire `ProjectData` et ses conversions dans un module
  dédié sous `core/`, sans importer commandes Qt, paramètres globaux ou scène.
  Garder un export compatible depuis `core.project` pour les anciens appelants.
- Opérations ciblées sous `services/` : validation et préparation des tâches,
  fiches, interventions et imports ; pas de règles agronomiques dans `server.py`.
- Contrats Python limités aux besoins réels : état lisible, changements
  préparés, historique, accès aux médias et dépôt. Pas de registre de plugins,
  bus générique ou infrastructure de synchronisation distribuée.
- Composer les fournisseurs métier et les fournisseurs scène/UI au lieu de
  forcer l’hôte autonome à remplir tous les champs du graphe live avec des
  simulacres. L’UI ne dépend pas du paquet de transport `agent_api` pour ses règles.
- Un changement préparé porte l’avant/après des champs touchés, son inverse,
  les identités et dépendances lues, les erreurs et effets secondaires. Ce n’est
  pas un pointeur mutable vers un item Qt, ni une commande arbitraire fournie
  par le client.
- En mode vivant, l’adaptateur collecte les données sur le thread Qt et traduit
  le changement en une commande de la pile existante. Le modèle pur préparé
  n’est pas une seconde copie autoritaire du projet ouvert. Après migration
  d’une donnée, désigner explicitement son propriétaire unique.
- Le moteur d’historique Python sera partagé ; le `CommandManager` Qt conserve
  ses signaux, libellés traduits et points d’entrée `execute` / `register_applied`.
  Les commandes de dessin peuvent rester Qt. Aucune pile parallèle dans MCP.
  Cette extraction constitue un sous-lot à risque distinct ; le premier
  branchement d’une opération pure peut garder le gestionnaire Qt intact et
  vérifier l’inverse directement sans livrer un deuxième historique autonome.

Le domaine retourne des codes d’erreur et paramètres structurés. Les interfaces
les traduisent ; les dates, UUID et horodatages proviennent de dépendances
injectées et explicites. Les calculs ne dépendent pas des préférences d’une
fenêtre ou de la date de simulation sans l’annoncer.

## 4. Contrats à construire avant de généraliser les écritures

### Préparer, prévisualiser, appliquer

La préparation est sans écriture : aucun fichier créé, média copié, reçu réservé,
signal de modification ou commande empilée. Elle retourne le périmètre de
stockage, les objets concernés, valeurs avant/après, refus, avertissements,
effets secondaires et conditions d’application.

L’aperçu et l’application emploient exactement le même préparateur. Une
application ultérieure revalide sous exclusion mutuelle la version de toutes
les dépendances lues, pas seulement celle de l’objet principal. Une prévisualisation
périmée est refusée ; l’agent la recalcule. Des IDs et dates générés dans un plan
préparé restent stables lors de son application ; ils ne sont pas recalculés à
partir d’un nouveau « maintenant ». Un aperçu abandonné ne réserve rien durablement.

Pour le premier contrat, conserver le plan côté hôte et retourner un
`preparation_id` opaque, lié à l’époque de session et aux versions lues.
L’application fournit ce jeton et sa clé de requête ; elle ne fournit pas un
patch libre à exécuter. Fixer une durée de validité annoncée et une capacité
bornée du cache. Expiration, éviction ou redémarrage : refus explicite et nouvel
aperçu requis. Les préparations ne sont pas des reçus durables ; si un commit a
déjà eu lieu, sa clé de requête permet de relire son résultat avant de demander
un nouveau plan. Le lot initial n’implémente pas de reprise durable d’un aperçu.

Exemple de réponse cible, **non encore implémentée** :

```json
{
  "status": "prepared",
  "preparation_id": "opaque-session-token",
  "scope": "project",
  "operation": "update_garden_card",
  "expected_revision": "opaque-token",
  "changes": [{"target_id": "plant-uuid", "field": "cultivar", "before": null, "after": "Exemple"}],
  "warnings": [],
  "conflicts": [],
  "side_effects": [],
  "persisted": false
}
```

Les noms d’outils finaux seront arrêtés dans chaque lot. Les outils existants
resteront compatibles et appelleront les mêmes opérations, avec déploiement
additif des paramètres de précondition et de rejeu. Les clients anciens sans
précondition n’obtiennent pas artificiellement une garantie de lecture protégée.

### Commandes et opérations par lot

Une mutation effective représente une étape d’undo ; refus, no-op et rejeu
n’en ajoutent aucune. Un lot est validé intégralement sur un état candidat,
y compris doublons internes, dépendances et références entre lignes.
L’application publie une seule mutation et un seul inverse.

Un lot de 30 fiches retourne un résultat par ligne : création, modification,
identique, conflit ou refus. Une ligne invalide empêche tout le lot par défaut.
Il n’y a pas de mode « partiel silencieux ». Une série de commandes Qt exécutées
puis annulées au mieux ne constitue pas une transaction atomique : la frontière
doit publier un candidat validé ou restaurer intégralement l’état et l’historique
avant toute notification. Les effets de fichiers externes sont exclus du lot
initial ou préparés dans une zone temporaire avec une procédure d’échec prouvée.

Les lots mixtes projet + bibliothèque globale sont initialement refusés : deux
fichiers n’ont pas une transaction commune. Les lots contenant des opérations
CAD non migrées sont également refusés. Pour la bibliothèque globale, l’annulation
porte sur une transaction de bibliothèque identifiée, avec contrôle des versions
touchées ; elle ne prétend pas appartenir à l’undo du canevas. Cette distinction
doit être visible dans le résultat et dans l’interface.

### Concurrence

Chaque lecture métier migrée fournit une version opaque liée au document et à
son époque d’ouverture. L’implémentation initiale peut utiliser une révision de
document conservatrice ; un suivi par objet/champ n’est ajouté que si les refus
excessifs le justifient. Changement de document, undo/redo et modifications par
une ancienne UI doivent invalider les préconditions concernées, sans problème ABA
(modifier puis rétablir une valeur ne rétablit pas une ancienne autorisation).

Vérification et mutation sont indivisibles dans l’hôte : thread Qt en mode
vivant, verrou de session en mode autonome. Entre processus, vérifier l’identité
du fichier et sa version de base sous un verrou coopératif tenu pendant le
commit ; une simple comparaison de hash suivie d’un `replace` laisse une course.
Les chemins équivalents et liens symboliques doivent désigner la même ressource.

Le mode autonome d’écriture n’est livré qu’une fois les ouvertures/sauvegardes
vivantes coordonnées avec lui. Première politique : un seul propriétaire
d’écriture par projet ou stockage global ; si l’application le détient, l’agent
passe par son MCP ou reçoit un conflit. Tous les chemins internes d’écriture
concernés sont inventoriés, y compris autosave et transitions de saisons.
Un éditeur externe ignorant ce protocole reste une limite explicite ; une
modification externe détectée provoque un refus, jamais un écrasement volontaire.

### Rejeu, coupures et durabilité

La clé de requête est liée au stockage/document, à l’opération, à une version
de contrat et à l’empreinte canonique de son contenu. Même clé/même intention :
retourner le reçu ; même clé/contenu différent : conflit. Deux appels simultanés
avec la même clé ne produisent qu’un effet. Vérifier les reçus avant les
préconditions devenues caduques du fait de la première application.

Les reçus existants d’interventions survivent à undo et suppression ; conserver
ce comportement pour empêcher une résurrection après reprise. Redo rétablit
l’action explicitement ; le rejeu réseau n’est pas un redo. L’historique
d’annulation reste propre à la session, même lorsque le reçu est enregistré.

En application ouverte, `applied` et `persisted=false` signifient une mutation
en mémoire ; la protection ne survit à un crash que si état et reçu ont été
sauvegardés ensemble. En mode autonome, le reçu et la donnée sont publiés dans
la même écriture atomique avant de répondre `persisted=true`. Ne pas promettre
« exactement une fois » pour une réponse en mémoire perdue avant sauvegarde.

Un outil de lecture du résultat par clé distingue appliqué, sauvegardé, annulé,
supprimé et résultat encore indéterminé lorsque cette information est disponible.
Un timeout après début d’exécution n’autorise aucune nouvelle mutation aveugle.
Les formats de reçus seront additifs, bornés en taille et validés ; aucune purge
automatique susceptible de rendre un vieux rejeu créateur de doublon ne sera
introduite sans un contrat d’expiration explicite.
Le conteneur actuel `garden_memory` ayant un validateur fermé, ne pas y ajouter
des reçus généraux sans évolution explicite et compatible du contrat. Le lot
concerné choisit un emplacement additif au document et vérifie sa conservation
à travers les anciennes voies de sauvegarde. Les reçus ne contiennent ni jeton
d’accès ni copie inutile de données personnelles.

### Authentification et disponibilité

Conserver le transport MCP local, les protections Host/Origin et le double
contrôle activation des écritures + jeton, avant accès à tout fournisseur de
mutation. L’hôte autonome ne contourne pas ces contrôles et ne génère ni ne lit
de configuration personnelle par simple import. L’inspection UI reste protégée
comme aujourd’hui. Une préparation qui révèle des valeurs avant/après suit
au moins la politique de lecture de son stockage ; elle n’active pas une écriture.

Les calculs purs peuvent quitter le thread Qt après capture d’un snapshot
détaché. Aucun objet Qt mutable n’est envoyé à un worker. La revalidation et
l’application vivantes reviennent ensemble sur le thread principal. Borner
taille des entrées, nombre d’opérations, médias et coût de préparation ; prévoir
timeout et annulation avant commit, sans annoncer l’annulation d’un commit
déjà commencé. Les capacités précisent famille migrée, mode, périmètre d’undo,
rejeu et niveau de durabilité, plutôt qu’un booléen global « headless ».

### Données, calculs et connaissances inconnues

Une fiche botanique, une instance plantée, une observation, un conseil et une
intervention sont des objets sémantiques distincts. Modifier la bibliothèque ne
réécrit pas les snapshots botaniques déjà embarqués dans les projets. Une
propagation éventuelle est une opération explicite avec son aperçu.

Une recommandation d’amendement reste un résultat de calcul : entrées, unités,
date de référence, provenance et limites. L’enregistrement en intervention
planifiée ou réalisée est une autre opération, avec intention explicite et
preuves connues ; aucun calcul ne coche une tâche ou ne décrémente un stock.
Conserver les distinctions inconnu/absent/zéro, date partielle/exacte et prévu/réalisé.

## 5. Lots d’implémentation et critères de sortie

Chaque lot est une branche/PR vérifiable, sans nécessité de fusion préalable
dans l’amont. Les dépendances ci-dessous évitent une réécriture en bloc.

| Lot | Modifications prévues | Preuve et critère de sortie |
| --- | --- | --- |
| L0a — frontière d’import | Alléger ou rendre paresseux les exports des paquets `core`, `services`, `agent_api` ; isoler traductions Qt et catégories métier nécessaires. Conserver les imports publics compatibles. | Sous-processus bloquant PyQt6/PySide pour les modules migrés ; imports publics historiques et démarrage isolé Qt toujours valides. Aucun accès automatique au stockage personnel. |
| L0b — données documentaires pures | Extraire les données projet et leurs conversions, conserver les façades publiques et isoler les effets d’ouverture sur les bibliothèques globales. | Lecture/écriture sans Qt de documents synthétiques ; champs inconnus, métadonnées et anciennes versions acceptées conservés ; aucune modification implicite d’un stockage global. |
| L1a — pilote : CRUD des tâches manuelles | Extraire création/édition/suppression et validation ; préserver les extensions des enregistrements touchés. Préparateur et inverse purs, adaptateur vers le gestionnaire Qt intact. UI et MCP appellent le même service. | Ajouter/éditer/refuser et inverser une tâche sans Qt, sauvegarder–réouvrir ; parcours MCP/UI réels, même commande, pile et dirty cohérents. Les statuts/report ne font pas partie de ce pilote. |
| L1b — historique commun | Extraire le contrat de commande et le moteur de pile Python derrière la façade Qt, sans déplacer les commandes géométriques sur des workers. | Exécution/undo/redo mixtes commandes métier et dessin, `register_applied`, refus d’exécution et erreurs d’undo/redo sans perte de pile ; signaux et dirty inchangés ; historique autonome de session utilisant ce même moteur. Revue dédiée avant extension. |
| L1c — garanties sur le pilote | Prévisualisation avec jeton de préparation, préconditions, lot de tâches atomique, reçus et lecture de résultat. | Aperçu sans effet, conflit après édition UI/undo, expiration/redémarrage, échec à mi-lot sans effet, appels simultanés et rejeu sans deuxième commande ; distinction appliqué/sauvegardé prouvée. |
| L2 — fiches de suivi et interventions | Scinder `GardenMemoryService` en opérations pures et adaptateur scène. Préserver `GardenEditCommand`, verrous, règles de cibles/médias, précision des dates et reçus historiques. | Même opération depuis dialogue et MCP ; deux instances de même espèce restent distinctes ; no-op, champ absent, suppression de cible, undo/rejeu et réouverture prouvés. Aucun changement de géométrie implicite. |
| L3 — stockage de bibliothèque fiable | Injecter un chemin de stockage ; réutiliser les modèles botaniques ; erreurs d’écriture explicites, sauvegarde préalable et publication atomique. Conservation des champs inconnus acceptés, refus d’écraser un fichier illisible, verrou et version de base. | Échec avant publication sans perte ; corruption initiale refusée ; sauvegarde/restauration vérifiées ; bibliothèque et réglages entièrement temporaires. Snapshots de projets inchangés. |
| L4 — import de 30 fiches | Préparer l’import structuré, politique de correspondance explicite par ID/source, rapport par ligne, lot atomique, reçu et annulation de transaction de bibliothèque. UI d’import et MCP partagent le service. | 30 fiches, doublon interne, homonymes légitimes, conflit de clé, ligne 17 invalide, disque non inscriptible, perte de réponse, relance du processus et annulation après modification concurrente. Aucun écrasement fondé sur le seul nom commun. |
| L5 — sol, recommandations et autres données du projet | Migrer analyses de sol et calculs en premier ; puis journal/récoltes/maladies/successions par sous-lots. Traiter les statuts/report de tâches séparément : commande dédiée et évolution explicite du refus actuel d’écriture MCP directe, via la source canonique `set_task_status`. Extraire des entrées simples des services liés à une scène ; maintenir références, historiques et task IDs canoniques. | Calcul déterministe sans Qt ; conseil sans écriture ; enregistrement explicite distinct ; héritage sol global/local et unités préservés ; chaque sous-lot couvre persistance/undo/échec avant d’annoncer sa capacité. |
| L6 — hôte MCP autonome | Après L1/L2 et coordination des fichiers, assembler un hôte sans fenêtre avec les mêmes opérations et le transport protégé existant. Chemin projet explicite, cycle ouvrir/éditer/sauver/fermer, historique de session, capacités conditionnelles. | Client HTTP MCP réel dans un processus interdisant Qt ; auth/refus, collisions de propriétaire, crash avant/après commit, reprise et relecture ; aucune capacité de rendu annoncée. Bibliothèque autonome après L3/L4 seulement. |
| L7 — consolidation ciblée | Retirer uniquement les règles devenues doublonnées ; conserver les shims publics utiles. Mettre à jour contrats MCP, guides et décision durable d’architecture. | Comparaison UI/MCP/autonome sur les mêmes jeux synthétiques, régressions héritées pertinentes et absence de deuxième source de vérité sur chaque famille migrée. |

Ordre recommandé : L0a → L0b → L1a → L1b → L1c → L2 ; L3 → L4 peut avancer séparément une fois les
contrats communs stabilisés. L5 procède famille par famille. L6 fournit d’abord
les parcours projet migrés ; il n’attend pas une extraction totale du dessin.
L7 clôt chaque famille puis le programme. L1 est le jalon qui prouve la direction
avant d’élargir le nombre d’outils.

## 6. Validation et protections de migration

Réutiliser en priorité les scénarios existants :

- `tests/integration/test_agent_task_soil_tools.py`, `test_tasks.py` et
  `test_calendar_task_convergence.py` : tâches, identité et convergence.
- `tests/unit/test_garden_memory.py`,
  `tests/integration/test_garden_memory_mcp.py` : précision, suivi et rejeu.
- `tests/integration/test_agent_domain_tools.py`, `test_agent_succession_tools.py`
  et `test_amendment_calculator.py` : calculs et données horticoles.
- `tests/unit/test_agent_api_bridge.py`,
  `tests/integration/test_agent_api_dns_rebinding.py` et
  `test_agent_api_writes.py` : frontière transport/thread et écritures protégées.
- `tests/integration/test_mcp_extended_workflows.py`,
  `test_mcp_file_workflows.py` : vrais fournisseurs, formulaires et fichiers.

Ajouter seulement les preuves nouvelles nécessaires : import réellement sans
Qt, équivalence des changements préparés, preview sans effet, transaction
échouée sans mutation, conflits après UI/undo/changement de document,
appels concurrents/rejeu après redémarrage et coupures aux frontières de commit.
Comparer valeurs métier et identités après sauvegarde–réouverture ; un compteur
de commandes ou un reçu HTTP réussi ne suffisent pas.

Tous les essais utilisent projets, bibliothèques, médias et QSettings isolés,
y compris les sous-processus. Pas d’ouverture en écriture du jardin personnel.
La preuve native Cocoa porte sur les interactions affectées ; l’offscreen ne
vaut pas acceptation visuelle native. Pas de seuil de performance inventé :
mesurer le lot synthétique de 30 fiches et la réactivité Qt avant de décider
d’un découpage supplémentaire.

Commandes de vérification choisies par lot : pytest ciblé avec
`.venv/bin/python`, Ruff sur le Python modifié, contrôle des traductions complet
si textes Qt changés, puis `git diff --check`. Pas de suite exhaustive pour ce
document de plan. Une revue indépendante `senior-reviewer` doit porter sur le
diff exact de chaque lot risqué (persistance, import, undo partagé, auth ou
refonte transversale) ; corriger et faire revoir les corrections à risque.

## 7. Organisation du travail délégué et documentation

Pour cette discussion, déléguer les tâches séparables à **GPT-6 Luna, effort
maximal**, avec propriété explicite des fichiers. Exemple de répartition lors
de L1 : un agent pour opérations/préparateur, un autre pour adaptateur UI/MCP,
un troisième pour preuves indépendantes, après accord sur le petit contrat
partagé. Aucun agent ne réécrit le travail d’un autre.

Le responsable principal vérifie ensuite le diff concret, les parcours annoncés
et les résultats accessibles ; il ne considère pas le compte rendu d’un agent
comme une preuve suffisante. Les changements de contrat sont intégrés avant de
paralléliser leurs consommateurs. La revue indépendante reste distincte de
l’auto-vérification de l’auteur.

Les skills existants architecture, extension, validation et livraison suffisent
pour commencer. Aucun nouveau skill ni outil de production n’est créé pendant
la planification. Après L1, si la procédure est stable, ajouter un runbook
court « opération métier partagée » renvoyant aux vrais modules et tests,
plutôt qu’une nouvelle couche de consignes spéculative. Le contrôle d’import
sans Qt devient alors un outil de test versionné et réutilisable.

Mettre à jour `MCP_COUVERTURE.md` à chaque capacité effectivement livrée,
`PROGRESSION.md` pour les résultats et limites, et les documents techniques
qui portent les frontières changées. Une décision d’architecture en anglais
sera justifiée lors du premier contrat durable livré ; ce plan français ne
prétend pas remplacer les ADR existants.

## 8. Fin de cette étape

Le livrable courant est ce plan, ses références contrôlées et sa draft PR.
Les choix locaux retenus sont : migration progressive, tâches comme première
preuve verticale, propriétaire d’écriture unique, lots atomiques dans un seul
stockage, bibliothèque distincte du canevas et autonomie sans Qt limitée aux
capacités effectivement migrées. L’implémentation applicative commence dans
une étape suivante ; aucune nouvelle garantie MCP n’est annoncée aujourd’hui.
