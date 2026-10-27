# Plan d’implémentation — métier partagé Qt / MCP

Date : 26 octobre 2026. Base de réalisation : 9217bf9 ; audit initial : 6d8dbdc.
**Statut : implémenté et contrôlé sur `feat/shared-domain-operations`, avec les limites documentées ci-dessous.**
Ce document décrit le périmètre désormais disponible et ses limites. Voir le
[guide utilisateur des opérations partagées](METIER_PARTAGE.md), la
[couverture MCP](MCP_COUVERTURE.md) et l’[ADR-065](../09-architecture-decisions/README.md#adr-065-shared-document-operations-and-explicit-hosts).

## 1. Résultat livré et périmètre

Le serveur peut lire l’état d’un projet, préparer un lot de mutations documentaires
sans effet, puis l’appliquer après revalidation. Les mêmes préparateurs de données
sont appelés par l’adaptateur Qt et l’hôte autonome. Les opérations admises sont
définies par <code>services/document_operations.SUPPORTED_OPERATIONS</code> et détaillées
dans le [guide métier partagé](METIER_PARTAGE.md).

Le mode vivant conserve le projet et sa pile d’annulation dans OGP. L’hôte
autonome ouvre un fichier <code>.ogp</code> explicite, utilise les mêmes opérations de données
sans instancier Qt et publie chaque mutation dans le fichier. L’accès éventuel à
la bibliothèque botanique est un port et une transaction distincts ; aucun
chemin de stockage personnel n’est déduit par l’hôte autonome.

Le rendu, le dessin, la géométrie, les interactions du canevas et les exports
graphiques restent des capacités Qt. Les données brutes des plantes déjà
intégrées au projet ne sont pas propagées automatiquement depuis la bibliothèque.
Les notes de journal sans épingle ont un CRUD documentaire ; créer ou supprimer
une épingle reste un parcours de commande du canevas. Les statuts de tâches
générées ne font pas partie des écritures documentaires exposées, et la politique
MCP de succession refuse les chevauchements que le dialogue Qt peut afficher
avec avertissement.

## 2. État réellement livré

| Frontière | Implémentation courante | Conséquence |
| --- | --- | --- |
| Données du projet | <code>core.project_data</code> et <code>core.plan_io</code> sont importables sans Qt ; <code>core.project</code> conserve les exports publics historiques. | Les snapshots et conversions purs préservent les champs supplémentaires, tandis que le gestionnaire de scène reste Qt. |
| Exports de paquets | Les façades <code>core</code>, <code>services</code> et <code>agent_api</code> résolvent paresseusement leurs exports. Les modules nommés dans les tests de frontière sont importés dans un sous-processus qui interdit PyQt6/PySide. | La preuve porte sur les modules explicitement testés ; elle ne signifie pas que tout <code>core</code>, <code>services</code> ou <code>agent_api</code> soit Qt-free. |
| Génération de tâches | <code>services/task_generator</code> ne consulte QtCore que si cette bibliothèque est déjà chargée ; un calcul headless conserve le libellé source. | La génération et ses calculs peuvent s’exécuter sans Qt, avec les traductions conservées dans une session UI initialisée. |
| Opérations documentaires | <code>services/document_operations</code> prépare les candidats ; les services tâche, jardin, sol, récolte, ravageurs, journal et succession portent leurs validations pures. | L’aperçu et l’application partagent les mêmes règles et les modifications de champs inconnus traversés restent préservées. |
| Historique | <code>core.command_history.CommandHistory</code> détient les piles Python. <code>core.commands.CommandManager</code> en est la façade Qt avec les signaux historiques. | Le live ajoute une seule commande à la pile commune Qt ; la session autonome utilise le même moteur d’historique, limité à sa durée de vie. |
| Adaptateur live | <code>LiveDocumentOperations</code> capture et publie un snapshot via <code>ProjectManager</code>, sur le thread Qt. Il remplace les stores métier pris en charge et ajoute les reçus, sans construire ou détruire d’items graphiques. | Une mutation live reste en mémoire jusqu’à <code>save_plan</code>. |
| Hôte autonome | <code>agent_api.autonomous</code> ouvre le chemin explicite via <code>core.document_session.DocumentSession</code>, et n’importe ni fenêtre ni canevas. | Chaque apply/undo/redo réussi est sauvegardé atomiquement avec ses reçus ; aucun rendu ou export graphique n’y est offert. |
| Coordination fichier | <code>DocumentOwnership</code> prend un verrou de fichier consultatif ; les écritures comparent aussi la version disque attendue. | Les écrivains OGP coopératifs se coordonnent. Un éditeur externe qui ignore le verrou reste hors de cette garantie. |
| Bibliothèque botanique | <code>services/library_operations</code> et <code>LibraryPort</code> préparent des transactions d’import séparées du document. | En autonome, la bibliothèque n’est ouverte que si un chemin est fourni explicitement ; sa transaction et son undo ne touchent pas la pile du canevas. |

Les tests d’import headless interdisent PyQt6 et PySide dans des sous-processus pour
les modules ciblés. Les services Qt, la scène, le rendu et certains adaptateurs
restent intentionnellement dépendants de Qt. Une façade paresseuse n’est pas une
garantie d’autonomie de tous ses modules.

Les clés inconnues des documents sont conservées par les mutations documentaires
prises en charge. Cette règle ne transforme pas les snapshots botaniques en
références vivantes vers la bibliothèque : une mise à jour de bibliothèque ne
réécrit pas les plantes embarquées dans un <code>.ogp</code>.

## 3. Architecture livrée

~~~text
Outils MCP documentaires ──┐
                            ├── services/document_operations + préparateurs purs
Dialogue / commandes Qt ───┘                         │
                                      candidat détaché + patches avant/après
                                                     │
                         ┌───────────────────────────┴──────────────────────┐
                         │                                                   │
              LiveDocumentOperations                              DocumentSession
              ProjectManager + CommandManager                 .ogp explicite + CommandHistory
~~~

Les services de domaine sont des préparateurs de candidats détachés : tâches
manuelles, fiches et interventions, relevés et évaluations de sol, récoltes,
ravageurs, notes, plans de succession et bibliothèque. Ils valident les
identités, références et règles de chaque famille ; les hôtes fournissent
l’accès aux médias et la frontière de publication.

En mode live, la préparation et l’application se font via l’adaptateur du
document courant. Les mutations compatibles avec cette surface passent par une
commande de la pile Qt existante ; aucun historique MCP concurrent n’est créé.
Les commandes de dessin et les objets graphiques restent possédés par Qt.
L’hôte autonome partage le moteur <code>CommandHistory</code>, mais pas une pile durable
après son arrêt.

Les modules d’export public des paquets sont paresseux afin de ne pas importer
un adaptateur Qt en important une frontière pure. Cela ne retire pas la
dépendance Qt à l’application, à ses commandes graphiques ou aux modules de
rendu. Les textes UI restent traduits par Qt ; le calcul headless des libellés de
tâches retourne le texte source si QtCore n’est pas déjà chargée.

L’import de bibliothèque est explicitement distinct des mutations de projet :
une bibliothèque JSON est une transaction séparée, avec reçu et annulation
propres. La modification d’une bibliothèque n’altère ni un projet ouvert ni les
snapshots botaniques enregistrés dans celui-ci.

## 4. Contrats réellement livrés et limites

### Préparer, relire, appliquer

<code>get_document_state</code> renvoie un snapshot détaché et une révision.
<code>prepare_document_operations</code> accepte de 1 à 100 opérations, vérifie toutes les
lignes sur un candidat et ne modifie ni le document, ni la pile, ni le disque.
Il retourne des résultats par ligne et les valeurs touchées avant/après. La
limite d’entrée est de 1 000 000 octets ; les changements d’aperçu sont aussi
bornés.

L’application utilise le même préparateur et revalide la révision. Les
préparations sont opaques, en mémoire du processus, limitées à 128 entrées et
expirent après 300 secondes. Elles ne survivent pas à un redémarrage. Un conflit,
une expiration ou une éviction exige une nouvelle lecture et un nouvel aperçu.
Les médias ne sont résolus que par l’adaptateur hôte ; la validation de média est
rejouée à l’application.

### Historique, sauvegarde et reçus

Un lot documentaire qui modifie les données devient une commande unique de la
pile Qt ; un lot sans changement n’ajoute pas de commande. Le document reste modifié en mémoire jusqu’à <code>save_plan</code>. L’undo restaure
les valeurs métier mais ne retire pas le reçu de rejeu. Après sauvegarde, le reçu
est enregistré dans le document <code>.ogp</code> et reste consultable après réouverture,
y compris si l’enregistrement a ensuite été annulé ou supprimé.

L’hôte autonome écrit atomiquement la mutation et son reçu avant de répondre.
<code>CommandHistory</code> permet undo/redo pendant le processus ; cette pile de session
n’est pas restaurée après redémarrage. Les reçus survivent, eux, puisqu’ils sont
dans le fichier projet.

<code>request_key</code> est non vide, limité à 256 caractères et associé à l’intention
canonique du lot. Même clé et même intention renvoient le résultat précédent ;
même clé avec un contenu différent est refusée. <code>get_document_operation</code> indique
l’effet actuel des champs suivis : <code>present</code>, <code>reverted</code>, <code>changed</code> ou
<code>unchanged</code>. <code>reverted</code> signifie que les valeurs d’avant sont revenues, mais
ne prouve pas qu’un undo a eu lieu : une édition puis un retour identique donne le
même état. Un reçu décrit l’acceptation initiale, pas une garantie que le résultat
est encore visible.

### Stockage et concurrence

Les hôtes Qt et autonome coordonnent les ouvertures internes par bail de fichier
consultatif et comparent la version disque juste avant publication. Si un autre
écrivain OGP détient le document, l’ouverture autonome échoue et indique
d’utiliser le serveur live ou de fermer le document. Une différence de version
détectée empêche l’écrasement. Un éditeur externe qui ignore le verrou peut
toujours créer une course ; le verrou n’est pas imposé par le système à tous les
programmes.

La bibliothèque botanique n’appartient pas au batch de document. Ses outils
requièrent le jeton d’écriture, y compris pour lire ou préparer, et ses
transactions ont leurs propres reçus/undo. Le chemin de bibliothèque est
optionnel et explicite dans l’hôte autonome.

### Comportements qui restent distincts

<code>assess_document_soil</code> est un calcul de lecture seule. Dans le lot documentaire,
<code>record_soil_test</code> exige un <code>test_date</code> ISO explicite et non vide. L’ancien
outil MCP direct conserve son défaut historique de date du jour. Un conseil ne coche
pas une tâche et ne crée pas d’intervention. Les unités de tests de kit restent
distinctes des ppm et les valeurs inconnues ne sont pas converties en zéro.

<code>set_manual_task_status</code> n’accepte que des tâches manuelles présentes. Les statuts
des tâches générées ne sont pas exposés par les nouvelles opérations documentaires.
Le chemin canonique du projet, <code>set_task_status</code> avec restauration exacte par
<code>restore_task_status</code> et <code>restore_task_statuses</code>, reste le propriétaire des
statuts et de leur miroir legacy.

La préparation pure de notes gère le contenu du journal mais pas la géométrie
des épingles. La création/suppression d’une épingle reste une commande du
canevas Qt ; le préparateur refuse la suppression d’une note encore épinglée.
La politique de succession des nouvelles opérations refuse les chevauchements,
tandis que le dialogue Qt conserve son parcours d’avertissement et d’affichage.
<code>set_succession_plan</code> exige une liste <code>entries</code> et une année explicite ;
une liste vide supprime le plan, mais un champ absent ou nul est refusé.

Les familles non listées dans <code>services/document_operations.SUPPORTED_OPERATIONS</code>
ne deviennent pas disponibles par cette frontière. Dessin, géométrie, rendu,
captures d’écran et exports graphiques restent Qt.

## 5. État des lots et critères restants

Le plan initial reste utile pour suivre les critères de sortie ; l’état observé
au 26 octobre 2026 est résumé ci-dessous. Les résultats chiffrés de vérification
sont ajoutés par la livraison principale après collecte ; ce document ne les
anticipe pas.

| Lot | État | Livré / limite connue |
| --- | --- | --- |
| L0a — frontière d’import | Réalisé | Façades de paquets paresseuses, traductions du générateur compatibles avec le headless, frontières vérifiées pour les modules ciblés. Les autres modules Qt ne sont pas rendus Qt-free par cette extraction. |
| L0b — données documentaires pures | Réalisé | <code>ProjectData</code> et conversions isolés, exports historiques conservés ; lecture/écriture des documents synthétiques sans Qt et préservation des champs inconnus. |
| L1a — tâches manuelles | Réalisé | CRUD et validation communs, opérations préparables dans un batch ; les statuts générés ne sont pas inclus. |
| L1b — historique commun | Réalisé | <code>CommandHistory</code> pur est utilisé par la façade Qt <code>CommandManager</code> et les sessions documentaires autonomes. |
| L1c — aperçu, préconditions et reçus | Réalisé | Préparation en cache borné, revalidation de révision, clé idempotente, reçu consultable et effet courant distingué. |
| L2 — fiches et interventions | Réalisé | Préparateurs communs aux parcours documentaires ; les notes avec épingle conservent les mutations graphiques dans le canevas Qt. |
| L3 — bibliothèque fiable | Réalisé | Port à chemin explicite, publication transactionnelle et protection de version ; bibliothèque séparée du projet. |
| L4 — import de fiches | Réalisé | Aperçu, import transactionnel, reçu et undo de bibliothèque ; correspondance explicite par identifiant de source, pas par nom commun seul. |
| L5 — sol et autres données projet | Réalisé avec limites documentées | Relevés et calculs de sol, récoltes, ravageurs, journal, succession et statut des tâches manuelles ont des opérations de données. Les statuts générés et les épingles graphiques ne sont pas des écritures documentaires. La politique de chevauchement MCP de succession est plus stricte que le dialogue Qt. |
| L6 — hôte MCP autonome | Réalisé pour les opérations documentaires | Chemin de projet explicite, écriture authentifiée et persistée par mutation. La bibliothèque n’est accessible que via un chemin <code>--library</code> explicite ; aucun outil de dessin, rendu ou export graphique. |
| L7 — consolidation et documentation | Réalisé pour le périmètre documentaire | Guide utilisateur, couverture MCP, progression et ADR durable mis à jour ; le skill existant est mis à jour séparément par la livraison principale. |

Modules de référence : <code>services/document_operations.py</code>,
<code>services/task_operations.py</code>, <code>services/garden_operations.py</code>,
<code>services/soil_assessment.py</code>, <code>services/record_operations.py</code>,
<code>services/succession_operations.py</code>, <code>services/library_operations.py</code> ;
adaptateurs : <code>app/domain_adapter.py</code>, <code>core/document_session.py</code> et
<code>agent_api/autonomous.py</code>. Les limites utilisateur sont récapitulées dans
[METIER_PARTAGE.md](METIER_PARTAGE.md).

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

## 7. Documentation et maintien du contrat

Le guide utilisateur [METIER_PARTAGE.md](METIER_PARTAGE.md) porte les appels,
la relecture des aperçus, les clés de rejeu, la différence entre hôte live et
autonome, les outils de bibliothèque et les limites graphiques. La couverture
[MCP_COUVERTURE.md](MCP_COUVERTURE.md) décrit les outils réellement disponibles ;
[PROGRESSION.md](PROGRESSION.md) porte l’état de réalisation. L’ADR-065
documente le contrat durable, en anglais, dans l’index des décisions.

Le skill existant <code>ogp-architecture-contract</code> est ajusté uniquement pour les
assertions désormais changées : frontière d’import Qt-free, moteur
<code>CommandHistory</code> partagé et chemin canonique de restauration du statut. Aucun
nouveau skill ni outil de production n’est ajouté.

## 8. Résultat et suite

Le résultat est un ensemble d’opérations de données partageant les préparateurs
entre l’application Qt et un hôte MCP autonome explicite. Les révisions,
préparations, reçus, baux fichier et limites de stockage sont décrits dans ce
plan ; le parcours destiné à l’utilisateur est dans
[METIER_PARTAGE.md](METIER_PARTAGE.md).

Le dessin, le rendu, les exports graphiques, la création/suppression des épingles
de journal et les écritures de statut des tâches générées restent liés à Qt ou
hors des opérations documentaires. L’outil n’affirme ni performance mesurée ni
acceptation artistique ; les résultats de tests exacts sont ajoutés par la
livraison principale après vérification.
