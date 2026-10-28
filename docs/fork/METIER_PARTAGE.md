# Opérations métier partagées Qt / MCP

Ce guide décrit les opérations de données disponibles dans le fork. Elles
permettent de lire un projet, de préparer une modification sans l'écrire, puis
de l'appliquer après relecture. Les règles de préparation résident dans des
services Python sans dépendance Qt ; l'application ouverte et l'hôte autonome
les utilisent derrière des adaptateurs distincts.

La [revue transversale du 27 octobre 2026](REVUE_TRANSVERSALE_METIER.md)
cartographie les appels, invariants vérifiés, défauts corrigés et limites de couverture.

## Outils documentaires

### Lectures bornées

`get_document_state` renvoie une projection métier, et non le fichier `.ogp`
brut. `domains` filtre les familles (plan, objects, tasks, soil, harvest,
pests, journal, succession, inventory, shopping, garden_memory). Les collections
possèdent leur pagination `offset`/`limit` et leur `next_offset` ; conserver la
révision entre les lectures. La réponse est plafonnée à 512 000 octets JSON.
Un dépassement demande de réduire les domaines ou la taille de page et ne
renvoie pas un résultat tronqué en silence. La géométrie détaillée reste
accessible avec les outils graphiques du serveur en direct.

Les médias embarqués sont décrits par un manifeste ; leurs données se lisent
séparément avec `get_document_media`, par fragments de 65 536 octets maximum,
en fournissant la révision attendue. Les champs inconnus du fichier sont
conservés sur disque, même lorsqu’ils ne font pas partie de cette projection.
Cela comprend le PNG des textures personnelles imbriqué dans
`metadata.personal_fill_pattern.png`, notamment pour les maisons et pergolas.
La largeur de texture et les autres métadonnées restent dans la projection ;
un champ métier `png` situé ailleurs n’est pas retiré pour ce seul nom.
Les offsets de médias comptent les octets UTF-8 du texte encodé ; pour du
base64 ASCII, un octet correspond à un caractère. Le fragment ne coupe pas un
caractère UTF-8.

### Préparation et application

Les outils sont enregistrés par le serveur MCP et apparaissent dans
<code>get_capabilities</code> lorsqu'ils sont disponibles.

| Outil | Usage |
| --- | --- |
| <code>get_document_state</code> | Lire un instantané métier paginé, sans géométrie ni médias intégrés, et sa révision opaque. |
| <code>get_document_media</code> | Lire séparément un fragment de média embarqué à une révision donnée. |
| <code>prepare_document_operations</code> | Préparer de 1 à 100 opérations atomiques sans écrire ni modifier l'historique. Retourne un identifiant temporaire, les résultats et les champs avant/après. |
| <code>apply_document_operations</code> | Appliquer une préparation encore valide avec une clé de requête stable. Exige l'activation des écritures et le jeton MCP. |
| <code>get_document_operation</code> | Relire le reçu associé à une clé, notamment après une réponse perdue ou un redémarrage. |
| <code>assess_document_soil</code> | Lire l'état du sol et des conseils calculés ; n'enregistre ni relevé ni intervention. La surface, si fournie, est en mètres carrés ; zéro ne produit pas de dosage. |

`get_capabilities` publie les schémas des opérations, les champs obligatoires,
leurs types et des exemples, ainsi que les schémas des outils réellement
enregistrés. Les listes de noms seules ne constituent plus le contrat de saisie.
Pour limiter la découverte, `get_capabilities(detail="summary")` publie l’index
et `schema_catalog.fingerprint` ; charger ensuite un schéma avec `schema_domain`
et éventuellement `schema_operation`. Le défaut complet reste compatible.
Les clés de cache, mesures et limites figurent dans le
[profil de performance MCP](MCP_PERFORMANCE.md).

Les lectures botaniques acceptent `view="summary"|"detail"` et `fields`
(champs de premier niveau). `raw_data`, `provider_data` et `html` de premier
niveau nécessitent désormais `include_source=true` ; les médias restent séparés.
`omitted_fields` explicite les exclusions. Pour un objet volumineux,
`get_document_state(object_fields=["item_id","name","type"])` rend une page
compacte et publie `omitted_object_fields`. Aucun champ absent n’est remplacé
par null, zéro ou faux.
`retry_policy` inclut les applications documentaires et botaniques à clé
persistante ; la durabilité du document en direct exige toujours `save_plan`.

La liste des noms admis est publiée par
<code>services/document_operations.SUPPORTED_OPERATIONS</code> :

- tâches manuelles : <code>add_manual_task</code>, <code>edit_manual_task</code>,
  <code>delete_manual_task</code>, <code>set_manual_task_status</code> ;
- fiches et suivi : <code>set_garden_card</code>,
  <code>set_planting_date</code>, <code>add_plant_measurement</code>,
  <code>edit_plant_measurement</code>, <code>delete_plant_measurement</code>,
  <code>add_garden_intervention</code>,
  <code>edit_garden_intervention</code>, <code>delete_garden_intervention</code> ;
- sol : <code>record_soil_test</code> ;
- récoltes, ravageurs et journal : <code>add_harvest_record</code>,
  <code>edit_harvest_record</code>, <code>delete_harvest_record</code>,
  <code>add_pest_log</code>, <code>edit_pest_log</code>,
  <code>delete_pest_log</code>, <code>add_journal_note</code>,
  <code>edit_journal_note</code>, <code>delete_journal_note</code> ;
- succession : <code>set_succession_plan</code>.

Un lot est limité à 100 opérations et doit respecter la taille maximale de
requête. Une erreur de validation ou un conflit empêche l'application du lot
entier. Les aperçus expirent après cinq minutes ; le cache est borné à 128
préparations et appartient au processus courant. Une expiration, une éviction
ou un redémarrage impose de préparer à nouveau.

En direct, un changement d'epoch invalide et retire les préparations du cache.
Cela comprend le remplacement du projet et, comme auparavant, la notification
de sauvegarde : préparer à nouveau après une sauvegarde intermédiaire.

`get_capabilities` publie les `inputSchema` des outils réellement enregistrés,
puis `operation_schemas.document.operations` décrit les champs, les types, les
champs requis et un exemple pour chaque opération générique. Le contrat botanique
`operation_schemas.plant_library_import.record` expose le modèle d'une fiche et
un exemple d'import. Son schéma canonique est fermé ; toute nouvelle extension
botanique doit se trouver dans `raw_data`. Lors d'une mise à jour, une extension
historique de premier niveau omise de la requête est conservée automatiquement.
La renvoyer sans changement est hors du schéma canonique et n'est pas nécessaire.
Les opérations récolte, ravageurs et notes conservent les champs d'extension ;
les positions gérées par Qt restent refusées pour les notes. Les opérations
documentaires strictes signalent leurs champs supplémentaires comme refusés,
sauf les extensions explicitement préservées par ces enregistrements.

Les règles distinguent les champs absents, inconnus et nuls. Une quantité
inconnue ne devient pas zéro. Pour une plantation, une date nulle conserve
l’état inconnu. Dans le lot documentaire, <code>record_soil_test</code> exige un
<code>test_date</code> ISO explicite et non vide, car le modèle ne représente pas un
relevé sans date. L’ancien outil MCP direct <code>record_soil_test</code> conserve son
défaut historique de date du jour ; ne pas confondre les deux parcours. Les
opérations de statut nouvelles couvrent les tâches manuelles existantes ; les
tâches générées restent dérivées et ne sont pas exposées en écriture.

### Parcours de relecture et d'application

1. Appeler <code>get_document_state</code> et identifier la cible par son UUID,
   pas par son nom affiché. Conserver la révision renvoyée.
2. Préparer une opération avec son nom exact, son <code>target_id</code> et ses
   champs autorisés. Comparer les valeurs avant/après, les résultats de chaque
   ligne et la cible concernée, puis examiner l’aperçu avant d’appliquer.
3. Appeler <code>apply_document_operations</code> avec le
   <code>preparation_id</code> reçu et un <code>request_key</code> stable et
   unique pour cette intention. La clé de requête sert au rejeu idempotent ;
   elle n'est pas le jeton d'authentification.
4. Si la réponse est perdue, appeler d'abord
   <code>get_document_operation(request_key)</code>. Le même contenu avec la
   même clé renvoie le reçu sans répéter la mutation ; réutiliser cette clé
   pour un autre contenu est refusé.

La politique est également publiée dans `get_capabilities.retry_policy` :
`apply_document_operations` et `apply_plant_library_import` ont chacun un outil
de lecture de reçu. Dans l'application ouverte, le reçu documentaire devient
durable lorsque le projet est sauvegardé ; l'import botanique et son reçu sont
commis ensemble dans la bibliothèque. Pour une intervention directe, répéter
`add_garden_intervention` avec la même clé et le même contenu rejoue l'identifiant
initial ; sa clé devient durable avec le projet sauvegardé.

Exemple de forme de requête (UUID et date à remplacer par les valeurs relues
dans le projet) :

~~~json
{
  "operations": [
    {
      "operation": "set_planting_date",
      "target_id": "00000000-0000-4000-8000-000000000001",
      "fields": {"planting_date": "2026-10-26"}
    }
  ],
  "expected_revision": "révision-renvoyée-par-get_document_state"
}
~~~

Le retour de préparation est à examiner avant l'appel d'application. La clé
utilisée ensuite pourrait être, par exemple,
<code>planting-date:00000000-0000-4000-8000-000000000001:2026-10-26</code>.
Elle reste stable si l'appel doit être relu ; une nouvelle intention reçoit une
nouvelle clé.

Un reçu durable décrit le résultat initial et l'état actuel des champs touchés :
<code>present</code> signifie que la modification est encore présente,
<code>reverted</code> que les valeurs d'avant sont revenues, et <code>changed</code>
que les champs concernés ont depuis changé autrement. <code>reverted</code> ne prouve
pas à lui seul qu'un undo a eu lieu : une modification puis un retour identique
peuvent produire le même état. Un reçu ne garantit donc pas que la modification
est toujours visible.

Pour le lot documentaire, <code>set_succession_plan</code> exige un champ <code>entries</code> de type liste et une année explicite : une liste vide supprime le plan, tandis qu’un champ absent ou nul est refusé. Le parcours historique de <code>record_soil_test</code> conserve son défaut de date du jour ; la forme générique préparée exige au contraire un <code>test_date</code> ISO non vide.

## Application ouverte et exécution autonome

| Mode | Document et historique | Écriture disque |
| --- | --- | --- |
| OGP ouvert avec MCP | Le document vivant reste celui de l'application. Un lot qui change les données devient une commande unique dans la même pile undo/redo que l'interface Qt ; un lot sans changement n'ajoute pas de commande. | La mutation reste en mémoire jusqu'à <code>save_plan</code>. Le reçu est stocké avec le document et survit à l'undo, à la suppression de l'enregistrement métier concerné et à une réouverture après sauvegarde. |
| Hôte autonome | Ouvre uniquement le fichier projet donné sur la ligne de commande. Il n'instancie ni fenêtre, ni canevas, ni objets graphiques. Son historique undo/redo est celui de la session et disparaît au redémarrage. | Chaque mutation, undo ou redo réussi est écrit atomiquement dans le fichier avant la réponse de succès. Les reçus restent dans le projet après redémarrage. |

L'hôte autonome s'exécute avec Python du projet, un chemin de projet explicite
et, pour activer les mutations, un jeton ASCII non vide fourni dans la variable
d'environnement <code>OGP_DOMAIN_TOKEN</code>. Ne pas mettre le jeton dans la
commande ou le journal du shell.

~~~sh
.venv/bin/python -m open_garden_planner.agent_api.autonomous /chemin/choisi/projet.ogp --port 8767 --write
~~~

Le port par défaut est 8767 ; le serveur autonome écoute sur l'interface de
boucle locale. Sans <code>--write</code>, les outils de mutation ne sont pas
enregistrés. Les capacités de lecture et de préparation peuvent rester
disponibles sans autoriser l'application.

L'hôte autonome détient le projet pendant sa session, même sans
<code>--write</code>. Son ouverture prend un verrou de fichier coopératif et les
publications comparent la version du fichier avec celle lue au départ. Les
instances OGP qui respectent ces mécanismes se coordonnent ; en cas de fichier
déjà détenu, fermer son propriétaire ou utiliser le serveur MCP de l'application
ouverte. Le verrou est consultatif : un programme externe qui l'ignore peut
encore modifier le fichier et créer une course avec une écriture. Une différence
de version détectée fait refuser l'écriture pour éviter d'écraser silencieusement
le travail concurrent.

Le document Qt conserve aussi son bail après une sauvegarde, jusqu'au changement
de document ou à la fermeture acceptée. Annuler la fermeture conserve le bail.
Réacquérir un bail pour le même document ne supprime pas le contrôle de version
disque. Un chargement refusé en prévalidation conserve la scène et son historique ;
dès que commence le remplacement de la scène, les anciennes commandes undo/redo
sont supprimées, y compris si la publication échoue ensuite.

## Bibliothèque botanique

La bibliothèque est un stockage distinct du projet et du canevas. Les outils
<code>get_custom_plant_library</code>,
<code>prepare_plant_library_import</code>,
<code>apply_plant_library_import</code>,
<code>get_plant_library_operation</code> et
<code>undo_plant_library_import</code> exigent tous le jeton d'écriture, y
compris pour la lecture et l'aperçu. Les imports utilisent des identifiants de
source explicites, préparent jusqu'à 100 fiches et publient une transaction
atomique avec reçu persistant.

`get_custom_plant_library` accepte `query`, `plant_id`, `offset` et `limit`
(100 fiches maximum). Par défaut, les médias embarqués sont exclus. Conserver
la `revision` et la fournir en `expected_revision` aux lectures suivantes.
Une fiche trop volumineuse devient un résumé explicitement signalé dans
`truncated_records` ; la réponse entière est plafonnée à 512 000 octets.
Les fragments de médias se demandent explicitement pour une seule fiche.
Le `media_manifest` décrit les médias sans contenu. `media_id` permet d’en
sélectionner un avec `plant_id` et `include_media=true`. Sans sélecteur, les
médias déjà terminés renvoient un fragment vide marqué `finished` ; ils
n’empêchent pas de poursuivre la lecture d’un média plus long.

La préparation refuse les champs inconnus au premier niveau. Les extensions
se placent dans l’objet `raw_data` ; les anciennes extensions déjà stockées
restent conservées. Les bornes pH, dimensions, rusticité et durées doivent être
cohérentes, les nombres finis et les dimensions/durées positives ou nulles.
Les offsets du calendrier peuvent traverser l’ancrage annuel et ne sont pas
assimilés à des durées min/max. Le modèle n’a qu’un seuil minimal de
température de germination : aucun maximum n’est inventé.
Le schéma publié décrit la saisie canonique : omettre les anciennes extensions
de premier niveau lors d’une mise à jour, elles sont conservées par le service.
L’écho inchangé d’une extension historique est toléré pour compatibilité par
le validateur, en dehors de ce schéma canonique ; il ne permet pas d’ajouter
ou de modifier un champ inconnu.

Pour une fiche incomplète, `edible` et `flowering` absents ou nuls restent
`null` (inconnus) ; `false` signifie une réponse négative explicite. Les valeurs
`false` historiques sont conservées : leur provenance ne permet pas de les
requalifier automatiquement. L’éditeur Qt affiche l’état comestible inconnu
avec une case intermédiaire et le conserve si un autre champ est modifié.
Les parseurs Trefle, Permapeople et Perenual conservent également l’absence
comme inconnue ; une liste vide de parties comestibles ne prouve pas qu’une
plante soit non comestible. Un booléen négatif explicite reste négatif.

Le panneau botanique conserve la révision de la fiche qu’il édite. Si un import
MCP ou un autre écrivain a modifié la bibliothèque, la sauvegarde de l’ancien
panneau est refusée et expliquée dans le panneau ; le modèle et le canevas
restent intacts. Désélectionner puis resélectionner la plante ne transforme pas
son ancien snapshot en fiche actuelle. Charger explicitement l’espèce courante
depuis la bibliothèque permet de reprendre l’édition. Ce chargement est une
action explicite ; les placements existants ne sont pas actualisés en silence.

Parcours d’import : appeler <code>get_custom_plant_library</code> pour lire la base, préparer avec <code>prepare_plant_library_import</code>, examiner les correspondances par identifiant de source et le rapport par ligne, puis appliquer avec une <code>request_key</code> stable via <code>apply_plant_library_import</code>. En cas de réponse perdue, lire d’abord <code>get_plant_library_operation</code> ; <code>undo_plant_library_import(transaction_id)</code> annule une transaction si les fiches touchées n’ont pas changé. Cet historique reste indépendant de l’historique du projet et du canevas.

Dans OGP ouvert, l'application fournit sa bibliothèque configurée lorsque ces
outils sont activés. L'hôte autonome n'y accède que si un chemin JSON est donné
explicitement avec <code>--library PATH</code> ; sans ce paramètre il ne lit ni
n'écrit de bibliothèque personnelle. L'annulation d'un import utilise son
<code>transaction_id</code> dans cette bibliothèque et n'utilise pas l'historique
du document ou du canevas. Une modification de bibliothèque ne propage pas ses
fiches vers les instantanés botaniques déjà intégrés dans les projets.

Dans l’hôte vivant, la résolution du chemin et la création du port se font sur
Qt. Les lectures `snapshot`/`result` et la préparation sont ensuite exécutées
sur le worker appelant, sans QObject. Les applications et annulations restent
sur Qt, publication disque comprise, puis rafraîchissent la bibliothèque UI.
Les lecteurs sont sérialisés entre eux, indépendamment du verrou des écritures :
une lecture disque lente ne retient pas Qt derrière ce verrou. Les fichiers
publiés atomiquement et la précondition disque à l’application protègent la
cohérence d’une préparation concurrente. Le cache de préparations utilise un
verrou bref distinct qui ne couvre aucune I/O. Aucun cache de contenu n’est ajouté.
Voir les [mesures de réactivité et leurs limites](MCP_REACTIVITE_QT.md).

## Listes personnelles et graines

La bibliothèque botanique ne constitue pas la liste de plantes à placer.
En direct, `get_plant_lists` lit des résumés de listes personnelles ;
`get_plant_list(list_id)` lit leurs entrées paginées et leurs identités
botaniques, sans placement. Ces outils globaux exigent le jeton et
l’activation des écritures, bien qu’ils soient en lecture seule.
L’URI historique `garden://plant-lists` est conservée pour compatibilité,
mais l’hôte live ne l’enregistre, ne l’annonce et ne la lit que si les écritures
sont activées avec un jeton et que son fournisseur Qt est présent. Le handler
valide ce jeton à chaque requête avant d’appeler le fournisseur. Sans cette configuration, les clients ne
voient pas cette URI dans `list_resources` ni `get_capabilities.resources` ; une
ancienne requête directe reçoit « ressource inconnue ». Les clients qui lisaient
auparavant cette URI sans jeton doivent activer les écritures et transmettre le
jeton (de préférence dans `?token=`, ou dans l’en-tête `Authorization: Bearer`).

`get_seed_inventory` filtre par `scope` (`project` par défaut ou `global`),
`packet_id` ou `query`, avec `offset`/`limit` (25 par défaut, maximum 100).
Le stock projet est dans le `.ogp`. Le stock global correspond à l’onglet
Inventaire de graines, exige le jeton et n’est disponible qu’en direct.
Les médias sont exclus et les longs textes limités à 2 000 caractères.
Chaque lecture renvoie une `revision` de sa projection ; fournir
`expected_revision` pour poursuivre ses pages. Les résumés de listes, les
entrées de chaque liste et chaque scope de graines ont leur propre révision.
Une insertion, suppression ou édition visible invalide la continuation :
reprendre alors à la première page.
Les listes et stocks globaux ne sont jamais ouverts implicitement par l’hôte
autonome. Aucun outil d’écriture dédié de ces deux stockages n’est ajouté ici ;
leurs modifications restent accessibles par les parcours Qt existants.

Les erreurs de lecture ne deviennent pas des réponses vides : le service MCP
fait échouer les lectures des listes ou des graines globales si leur fichier
est illisible. L’interface Qt signale également l’état et propose une reprise
explicite. Les sauvegardes globales sont atomiques, vérifient la version disque
sous verrou coopératif, et conservent les extensions inconnues. Les erreurs
laissent les modifications en mémoire visibles, avec des commandes pour
réessayer ou recharger après confirmation. Le détail des états et de la reprise
figure dans [Fiabilité des stockages personnels](STORES_PERSONNELS.md).

## Limites graphiques et métier

Les opérations partagées portent sur les données ; le dessin, la géométrie, le
rendu, les captures et les exports restent des capacités Qt. Les opérations de
journal pures gèrent le contenu d'une note sans créer d'épingle graphique. La
création et la suppression d'une épingle existante passent par la commande du
canevas Qt ; le préparateur refuse de supprimer une note encore liée à une épingle.
La politique MCP de succession refuse les chevauchements de périodes ; le
dialogue Qt conserve son parcours d'avertissement et d'affichage des périodes
qui se chevauchent.

Les outils et les capacités décrivent l'accès disponible, pas une preuve
d'acceptation visuelle ou artistique de l'application.

Voir aussi la [couverture MCP](MCP_COUVERTURE.md), le
[plan de réalisation](PLAN_METIER_PARTAGE.md) et l'[ADR-065](../09-architecture-decisions/README.md#adr-065-shared-document-operations-and-explicit-hosts).
