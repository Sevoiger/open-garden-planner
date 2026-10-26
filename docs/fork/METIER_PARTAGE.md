# Opérations métier partagées Qt / MCP

Ce guide décrit les opérations de données disponibles dans le fork. Elles
permettent de lire un projet, de préparer une modification sans l'écrire, puis
de l'appliquer après relecture. Les règles de préparation résident dans des
services Python sans dépendance Qt ; l'application ouverte et l'hôte autonome
les utilisent derrière des adaptateurs distincts.

## Outils documentaires

Les outils sont enregistrés par le serveur MCP et apparaissent dans
<code>get_capabilities</code> lorsqu'ils sont disponibles.

| Outil | Usage |
| --- | --- |
| <code>get_document_state</code> | Lire un instantané détaché du projet et sa révision opaque. |
| <code>prepare_document_operations</code> | Préparer de 1 à 100 opérations atomiques sans écrire ni modifier l'historique. Retourne un identifiant temporaire, les résultats et les champs avant/après. |
| <code>apply_document_operations</code> | Appliquer une préparation encore valide avec une clé de requête stable. Exige l'activation des écritures et le jeton MCP. |
| <code>get_document_operation</code> | Relire le reçu associé à une clé, notamment après une réponse perdue ou un redémarrage. |
| <code>assess_document_soil</code> | Lire l'état du sol et des conseils calculés ; n'enregistre ni relevé ni intervention. La surface, si fournie, est en mètres carrés ; zéro ne produit pas de dosage. |

La liste des noms admis est publiée par
<code>services/document_operations.SUPPORTED_OPERATIONS</code> :

- tâches manuelles : <code>add_manual_task</code>, <code>edit_manual_task</code>,
  <code>delete_manual_task</code>, <code>set_manual_task_status</code> ;
- fiches et suivi : <code>set_garden_card</code>,
  <code>set_planting_date</code>, <code>add_garden_intervention</code>,
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

L'ouverture en écriture prend un verrou de fichier coopératif et les
publications comparent la version du fichier avec celle lue au départ. Les
instances OGP qui respectent ces mécanismes se coordonnent ; en cas de fichier
déjà détenu, fermer son propriétaire ou utiliser le serveur MCP de l'application
ouverte. Le verrou est consultatif : un programme externe qui l'ignore peut
encore modifier le fichier et créer une course avec une écriture. Une différence
de version détectée fait refuser l'écriture pour éviter d'écraser silencieusement
le travail concurrent.

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

Parcours d’import : appeler <code>get_custom_plant_library</code> pour lire la base, préparer avec <code>prepare_plant_library_import</code>, examiner les correspondances par identifiant de source et le rapport par ligne, puis appliquer avec une <code>request_key</code> stable via <code>apply_plant_library_import</code>. En cas de réponse perdue, lire d’abord <code>get_plant_library_operation</code> ; <code>undo_plant_library_import(transaction_id)</code> annule une transaction si les fiches touchées n’ont pas changé. Cet historique reste indépendant de l’historique du projet et du canevas.

Dans OGP ouvert, l'application fournit sa bibliothèque configurée lorsque ces
outils sont activés. L'hôte autonome n'y accède que si un chemin JSON est donné
explicitement avec <code>--library PATH</code> ; sans ce paramètre il ne lit ni
n'écrit de bibliothèque personnelle. L'annulation d'un import utilise son
<code>transaction_id</code> dans cette bibliothèque et n'utilise pas l'historique
du document ou du canevas. Une modification de bibliothèque ne propage pas ses
fiches vers les instantanés botaniques déjà intégrés dans les projets.

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
