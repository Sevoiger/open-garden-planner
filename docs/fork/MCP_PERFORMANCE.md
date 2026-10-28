# Profil MCP pour agents — 27 octobre 2026

Profil initial : `cf76166`. Python 3.11.17 ; macOS-27.0.1-arm64-arm-64bit. Données entièrement synthétiques, fichiers temporaires, aucun plan ni bibliothèque personnels. Les quatre rapports JSON voisins contiennent les mesures initiales, médianes, p95 et allocations par parcours. Les latences présentées sont celles de cette machine, pas celles du connecteur.

## Méthode et reproduction

```bash
mkdir -p /tmp/ogp-bench-before
git archive cf76166 src | tar -x -C /tmp/ogp-bench-before
OGP_BENCH_SOURCE_ROOT=/tmp/ogp-bench-before/src .venv/bin/python scripts/benchmark_agent_reads.py --output /tmp/before.json
.venv/bin/python scripts/benchmark_agent_reads.py --output /tmp/after.json
OGP_BENCH_SOURCE_ROOT=/tmp/ogp-bench-before/src .venv/bin/python scripts/benchmark_agent_qt.py --output /tmp/qt-before.json
.venv/bin/python scripts/benchmark_agent_qt.py --output /tmp/qt-after.json
```

12 répétitions séquentielles après une lecture initiale directe. Le premier appel est nommé `initial_ms` ; il ne comprend pas le démarrage du processus, le chargement initial du fichier ni le handshake MCP. p95 = rang supérieur `ceil(0,95 × n)`, donc maximum avec 12 mesures. Concurrence : groupes de quatre, trois groupes HTTP, douze groupes Qt. Les spans HTTP mélangent appels séquentiels et concurrents, comme indiqué par leurs échantillons ; leurs percentiles ne se soustraient pas.

Jeux : petit jardin (12 objets et fiches), nombreuses entrées (2 000 objets et fiches), médias (8 Mo dans une extension imbriquée du plan et 8 Mo dans raw_data botanique), historique (10 000 reçus persistants). Ce dernier mesure les reçus ; il ne simule pas une pile Qt de 10 000 commandes. Tous possèdent une tâche ; les fiches incluent un bloc source HTML synthétique. Les mutations du parcours HTTP créent dix tâches dans le seul fichier temporaire, après les lectures de référence.

Les temps directs sont mesurés sans profileur ni tracemalloc. Un appel supplémentaire par opération donne les allocations Python maximales transitoires (`peak_allocated_bytes`), pas le RSS. Un autre appel utilise cProfile : `exclusive_profile_ms` classe le temps propre des fonctions en dispatch, lecture/validation disque, snapshot, copie, révision, filtrage, projection/manifeste, encodage/sérialisation et autres. Le temps JSON du hash est donc dans encodage, pas compté une deuxième fois dans révision. Recherche et projection partagent actuellement une fonction ; le delta entre `library_search` et `library_page` mesure leur parcours observable, sans prétendre isoler exactement la recherche.

HTTP : vrai serveur uvicorn éphémère, MCP SDK local, authentification synthétique. Instrumentation seulement dans le sous-processus du benchmark : ToolManager, conversion des résultats SDK et frontière ASGI. `server_request_ms` couvre la requête ASGI, `tool_dispatch_ms` le dispatch SDK avec validation/provider/conversion, `sdk_result_conversion_ms` la conversion textuelle/structurée et `asgi_send_ms` le temps attendu par send. Ce dernier ne mesure pas la livraison distante. Le temps client inclut SDK, HTTP et scheduling local ; aucune mesure du connecteur hébergé ni du réseau distant.

Qt : vrais ProjectManager, CanvasScene, objets et MainThreadBridge ; aucune fenêtre applicative initialisée, paramètres et stockage redirigés avant usage. Temps d’attente de dispatch Qt, durée sur Qt, durée totale worker et intervalle maximal de traitement des événements séparés. Offscreen ne prouve pas la fluidité visuelle native. Les gaps dépendent aussi du GIL et du scheduling du harnais ; ils ne sont pas une fréquence d’affichage.

Tokens : unavailable; estimate = ceil(compact JSON UTF-8 bytes / 4), not billed tokens. Estimation utile uniquement pour comparer des volumes, aucune affirmation de facturation ou de tokenizer du connecteur. Les octets directs utilisent le JSON UTF-8 compact ; la limite applicative reste calculée avec un encodage conservateur. Les octets enveloppe HTTP utilisent le modèle MCP complet sérialisé, distinct du texte du premier content.

## Lectures directes avant / après

Chaque cellule de latence indique médiane / p95 en ms. Allocation maximale par appel, en octets ; sortie UTF-8 hors enveloppe MCP.

| Jeu | Lecture | Avant ms | Après ms | Octets avant → après | Allocation avant → après |
| --- | --- | ---: | ---: | ---: | ---: |
| small | tasks | 0.075 / 0.096 | 0.034 / 0.065 | 470 → 470 | 10696 → 6562 |
| small | objects_page | 0.079 / 0.087 | 0.030 / 0.046 | 587 → 587 | 10696 → 6562 |
| small | library_page | 0.153 / 0.165 | 0.099 / 0.132 | 1799 → 422 | 33464 → 33464 |
| small | library_search | 0.166 / 0.296 | 0.100 / 0.115 | 1802 → 426 | 33382 → 33448 |
| many | tasks | 7.420 / 8.537 | 0.153 / 0.221 | 470 → 470 | 2218640 → 290682 |
| many | objects_page | 6.961 / 18.060 | 0.180 / 0.197 | 589 → 589 | 2218640 → 290682 |
| many | library_page | 21.488 / 34.392 | 10.874 / 12.852 | 1801 → 424 | 6245718 → 6052915 |
| many | library_search | 22.176 / 33.275 | 12.163 / 25.115 | 1805 → 429 | 6245718 → 6052981 |
| media | tasks | 17.777 / 19.072 | 2.903 / 3.106 | 470 → 470 | 16010898 → 8006623 |
| media | objects_page | 17.066 / 18.686 | 2.911 / 3.303 | 764 → 764 | 16010898 → 8006623 |
| media | library_page | 23.572 / 25.000 | 23.862 / 25.183 | 1937 → 560 | 24033553 → 24033619 |
| media | library_search | 23.532 / 25.175 | 23.180 / 23.923 | 1802 → 426 | 24033435 → 24033386 |
| history | tasks | 32.220 / 48.436 | 0.501 / 0.647 | 470 → 470 | 7272938 → 1454344 |
| history | objects_page | 33.470 / 53.763 | 0.505 / 0.679 | 587 → 587 | 7272938 → 1454344 |
| history | library_page | 0.163 / 0.181 | 0.095 / 0.107 | 1799 → 422 | 33211 → 33450 |
| history | library_search | 0.167 / 0.182 | 0.106 / 0.123 | 1802 → 426 | 33217 → 33450 |

RSS maximal cumulé du processus : 160186368 → 153092096 octets. Il inclut imports, jeux et HTTP ; ce n’est pas la mémoire résidente d’une requête ni un gain de mémoire garanti.

## Transport local

| Jeu | Appel | Avant médiane / p95 ms | Après médiane / p95 ms | Enveloppe avant → après |
| --- | --- | ---: | ---: | ---: |
| small | get_document_state | 2.350 / 7.200 | 3.075 / 9.973 | 864 → 864 |
| small | get_custom_plant_library | 2.326 / 2.521 | 3.099 / 3.905 | 2171 → 796 |
| small | get_capabilities | 5.634 / 15.867 | 7.755 / 18.925 | 130385 → 134873 |
| small | concurrent_tasks_4 | 7.795 / 8.710 | 8.939 / 9.875 | — → — |
| media | get_document_state | 19.953 / 28.132 | 5.971 / 10.995 | 864 → 864 |
| media | get_custom_plant_library | 24.597 / 26.348 | 26.922 / 28.652 | 2388 → 1013 |
| media | get_capabilities | 6.738 / 8.190 | 6.923 / 7.989 | 130385 → 134873 |
| media | concurrent_tasks_4 | 56.102 / 88.772 | 16.951 / 35.701 | — → — |

Aucun gain annoncé pour les petits appels HTTP ou la bibliothèque avec 8 Mo de médias : ils sont stables ou sensibles au bruit. Le coût bibliothèque reste proportionnel au fichier lu et au hash couvrant les médias. Les lectures concurrentes sont sérialisées par DocumentSession/LibraryPort ; les écritures conservent leur ordre.

## Qt : section graphique et attente

| Jeu / concurrence | Service Qt médian avant → après ms | Attente médiane avant → après ms | Total worker médian avant → après ms | Gap max avant → après ms |
| --- | ---: | ---: | ---: | ---: |
| small_concurrency_1 | 0.39 → 0.24 | 0.02 → 0.02 | 0.42 → 0.32 | 1.65 → 0.64 |
| small_concurrency_4 | 0.36 → 0.23 | 0.42 → 0.24 | 1.17 → 0.81 | 1.27 → 1.12 |
| many_concurrency_1 | 50.09 → 32.98 | 0.04 → 0.04 | 50.16 → 36.34 | 50.78 → 37.12 |
| many_concurrency_4 | 50.12 → 33.75 | 69.34 → 51.72 | 125.81 → 101.26 | 200.42 → 146.06 |
| media_concurrency_1 | 14.91 → 0.26 | 0.04 → 0.03 | 14.97 → 16.17 | 15.91 → 13.78 |
| media_concurrency_4 | 14.87 → 0.26 | 16.76 → 0.57 | 43.93 → 50.40 | 59.21 → 51.79 |

Le déplacement du hash hors Qt réduit fortement la section graphique avec médias, sans gain de temps total dans ce cas. Avec 2 000 objets, la sérialisation de scène et la capture restent dominantes. Les calculs Python hors Qt peuvent encore retarder Qt via le GIL. Aucun cache de scène fondé seulement sur mark_dirty n’est ajouté sans preuve qu’il couvre toutes les mutations graphiques.
## Contrats livrés et compatibilité

`get_capabilities()` garde son contenu complet par défaut. `detail="summary"`
conserve outils disponibles, règles de stockage/reprise et index des domaines,
sans les schémas détaillés. `schema_domain="document"` et
`schema_operation="add_manual_task"` récupèrent un contrat ;
`schema_domain="tools", schema_operation="get_document_state"` récupère un outil.
`schema_domain="plant_library_import"` récupère le modèle d’import lorsque disponible.
`schema_catalog.fingerprint` est le SHA-256 canonique des schémas des outils et
opérations enregistrés, indépendant de l’ordre des dictionnaires. Il identifie
les schémas, pas les données ni l’autorisation d’une future requête.

Sur le serveur live synthétique avec écritures, JSON compact : complet 74 468
octets, résumé 7 349, opération ciblée 8 640, outil ciblé 8 070. L’enveloppe HTTP
**autonome** du résumé mesure 9 194 octets dans ce relevé. Ces contextes ne sont
pas le connecteur live du constat initial : ne pas comparer directement cette
valeur aux 244 000 caractères observés dans son enveloppe.

Les représentations content/structuredContent historiques des capacités sont
conservées. Leur duplication coûte encore sur le résultat complet ; la réduire
sans négociation client casserait potentiellement les consommateurs. Les outils
métier partagés conservent `structured_output=False`, déjà présent avant ce diff.
Les descriptions et règles d’autorisation/sauvegarde/undo/reprise sont conservées ;
ce profil ne démontre pas un bénéfice à les réécrire toutes.

`get_custom_plant_library` propose `view="summary"|"detail"` et `fields` (au plus
32 noms de champs de premier niveau). Le défaut reste détail, mais exclut désormais
`raw_data`, `provider_data`, `html` de premier niveau ; `include_source=true`
retrouve cette représentation historique, toujours sans médias intégrés et sous
les plafonds existants. **Changement de contrat explicite** pour les clients qui
consommaient ces données source par défaut. `omitted_fields` liste les champs
écartés par la projection ou la troncature, `truncated_records` reste présent.
Une sélection ne fabrique pas les champs absents et préserve null, zéro et faux.
Les champs de source imbriqués dans une extension arbitraire ne sont pas
reclassifiés automatiquement : utiliser `fields` pour une projection stricte.

`get_document_state(object_fields=["item_id","name","type"])` rend une page
utilisable même si les métadonnées d’un objet dépassent le plafond. Les exclusions
sont publiées dans `omitted_object_fields`. Une erreur de plafond indique cette
reprise, au lieu de conseiller seulement de réduire un limit déjà égal à un.
La géométrie et les médias restent exclus même si leur nom figure dans la sélection.
Les chemins et identifiants des médias utilisent les indices originaux de page ;
les extensions imbriquées et les motifs personnels gardent leur manifeste.
Les chaînes base64 ASCII sont comptées et découpées sans allocation de l’intégralité
des octets. Le chemin Unicode conserve les contrôles de frontière UTF-8.

## Caches et concurrence

| Cache / optimisation | Clé et borne | Invalidation / garanties |
| --- | --- | --- |
| Catalogue des capacités | JSON canonique des inputSchema + mode + activation des écritures + disponibilité de l’inventaire global ; LRU de 8 catalogues fixes générés par cette version | Un changement de schéma ou de gate donne une autre clé. Valeurs JSON immuables ; nouveau dictionnaire pour chaque appel. Aucun résultat de bibliothèque ou de document ni aucun jeton en cache. Taille bornée par 8 catalogues applicatifs, pas un cache alimenté par les données utilisateur. |
| Fingerprint autonome | Un SHA-256 de 64 caractères par DocumentSession, sous son RLock ; préfixe epoch/sequence séparé | Invalidation après chaque écriture publiée, y compris undo/redo. Nouvelle ouverture = nouvelle session. Échec d’écriture = ancienne valeur conservée. Vérification du fichier **avant chaque dispatch** et au commit inchangée ; un changement externe provoque une erreur et exige réouverture. |
| Snapshot bibliothèque | Aucun cache | Relu et validé depuis le fichier à chaque appel ; les modifications externes sont observées. L’arbre vient d’un nouveau json.loads, donc trois copies complètes étaient redondantes. Le supprimer ne partage pas le résultat avec le stockage ou les appels suivants. |
| Snapshot Qt | Aucun cache de scène | Capture et génération cohérentes sur Qt, puis hash/projection sur le worker à partir du dictionnaire détaché. Préparer et appliquer restent sur Qt avec révision recalculée et contrôles atomiques existants. |

Les révisions du document restent des valeurs opaques de portée document complet,
partagées entre pagination et préparation. Elles couvrent toujours les médias et
les champs inconnus ; elles ignorent agent_meta, les reçus et metadata.modified
comme auparavant. Le hash a exactement le même préimage logique ; il ne copie
plus l’historique exclu. La séquence peut changer sans modification du hash métier.
La révision de bibliothèque est une empreinte du contenu des plantes ; ce n’est
ni la révision du document ni une précondition de commit de bibliothèque.
Les imports utilisent leurs propres versions et empreintes de stockage,
préparations et reçus. Ne pas intervertir ces portées.

Pas de cache périmé autorisant une écriture : préparation/application vérifient
leurs préconditions sur leur hôte sérialisé ; le stockage autonome contrôle
encore le disque. Aucun changement d’authentification, de clé de rejeu, de reçu,
d’écriture atomique ou de commande undo/redo. Les différences historiques des
outils directs et des lots préparés restent décrites dans METIER_PARTAGE.md.

## Parcours et budgets

Le benchmark HTTP exécute réellement recherche de plante → lecture des tâches →
préparation de dix ajouts → application → lecture du reçu : **5 appels avant et
après**, dont le détail par appel est enregistré. Les dix mutations restent un
lot et une unité d’undo, déjà possibles avant cette optimisation. Aucun
aller-retour économisé n’est revendiqué. La découverte complète demande un appel ;
la découverte compacte puis le premier schéma ciblé en demandent deux. Le client
peut réutiliser les schémas tant que l’empreinte est identique, en conservant les
gates et le mode comme contexte. Une future négociation conditionnelle ou un
outil multi-identifiants n’est pas ajouté sans parcours qui le justifie.

Budgets de surveillance sur cette machine et ces jeux, motivés par le profil
initial, pas des garanties universelles ni des assertions de CI sensibles à la
charge :

- p95 direct tâches : < 1 ms sur 2 000 objets et < 2 ms sur 10 000 reçus
  (contre plusieurs ms et plusieurs dizaines de ms initialement) ;
- p95 direct tâches avec 8 Mo : < 5 ms, en conservant le contrôle disque ;
- p95 page bibliothèque 2 000 fiches : < 25 ms ; recherche < 30 ms ; aucun budget d’amélioration pour la
  bibliothèque avec 8 Mo, dont la lecture/hash restent mesurables ;
- section Qt p95 : < 1 ms avec 8 Mo ; < 45 ms avec 2 000 objets. Ce dernier
  excède déjà 16,7 ms et signale une limite à traiter avant une promesse de 60 fps ;
- résumé de capacités live : < 10 000 octets JSON compacts dans ce catalogue ;
  limites dures métier inchangées : réponse conservatrice 512 000 octets,
  fiche 32 000 avant fallback, fragment média 65 536, manifeste document 100 entrées.

Les allocations et gaps max sont surveillés dans les rapports, sans budget RSS
par requête que ces instruments ne permettraient pas de défendre.

## Limites et suites justifiées

Le contrôle disque autonome lit et hache encore tout le fichier ; le hash de
bibliothèque couvre aussi les médias. Les supprimer ou remplacer par mtime/size
perdrait la protection contre les modifications externes. Un format séparant
révision durable et médias exigerait une évolution de stockage, non justifiée
par ces seuls relevés. Le premier hash autonome demeure proportionnel au document.
Aucun gain indépendant de la taille des médias n’est donc revendiqué pour la
chaîne complète ; il est démontré pour la projection métier ASCII et le manifeste
restreint à la page, et pour la section Qt avec médias.

La bibliothèque conserve une lecture disque complète par requête, sans index
persistant ni cache de recherche. Pour les 2 000 fiches, supprimer les copies
redondantes apporte déjà un gain mesuré ; un index seul ne retirerait ni la
lecture ni le hash. Avec davantage de données ou des fichiers plus lourds,
profiler un cache de lecture tenant compte des remplacements externes avant
son introduction. Les médias Unicode atypiques nécessitent encore un encodage
complet ; seuls les médias base64 ASCII usuels ont un accès sans cette allocation.

La file Qt conserve le timeout et l’annulation des opérations non démarrées.
Les appels métier HTTP passent par les workers AnyIO (limiteur SDK) et les hôtes
sérialisés ; aucune concurrence d’écriture n’est ajoutée. Il n’existe pas encore
de quota applicatif explicite par client ou de mesure de saturation avec des
centaines de requêtes ; les quatre appels concurrents ne suffisent pas à fixer
un tel quota. Les captures Qt de grandes scènes et le GIL restent les prochains
points à examiner. Pas de mesure native des interactions souris/clavier dans ce
profil, ni de mesure des grandes bibliothèques réelles.

Les erreurs historiques exposent encore majoritairement isError et un message ;
un code/cible/retryable/action de reprise uniforme demanderait une évolution
cohérente des outils directs et préparés. Ce diff améliore l’instruction de
reprise d’un objet volumineux mais ne revendique pas ce nouveau contrat global.
De même, il n’ajoute pas de « inchangé », de schéma conditionnel ni de lecture
multi-identifiants. Ces limites sont conservées explicitement plutôt que de
faire diverger silencieusement quelques opérations.

Validation : tests ciblés HTTP, Qt, médias imbriqués, source opt-in et sélection,
limites/pagination, bibliothèque externe, cache et invalidations, undo/redo,
conflits, reçus/rejeu, absence de Qt en mode autonome. La revue indépendante a
identifié et fait corriger le fallback qui ajoutait des champs non demandés.

Le [profil complémentaire du 28 octobre 2026](MCP_REACTIVITE_QT.md) mesure aussi
la copie profonde des anciens snapshots, le port bibliothèque vivant et la
concurrence jusqu’à 32 requêtes. Les mesures ci-dessus restent historiques.
