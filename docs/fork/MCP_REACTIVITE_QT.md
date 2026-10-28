# Réactivité Qt : snapshots et bibliothèque vivante

Suivi du 28 octobre 2026, base `d9400b3`. Cette campagne complète le
[profil précédent](MCP_PERFORMANCE.md) et la
[revue de cohérence](REVUE_TRANSVERSALE_METIER.md). Elle mesure les protections
ajoutées, sans supprimer la copie profonde des snapshots ni leurs préconditions.
Les fichiers de mesure et de test sont synthétiques et temporaires.

## Méthode reproductible

`scripts/benchmark_agent_reactivity.py` appelle les vrais providers vivants de
`GardenPlannerApp`, liés à un hôte minimal avec `CanvasScene`, `ProjectManager`
et `MainThreadBridge`. Il ne charge pas la fenêtre complète ni ses panneaux.
Un seul thread principal Qt fonctionne dans chaque processus. Les mesures
avant/après utilisent le même script et le même environnement ; seule la racine
source importée change. La base provient de `git archive d9400b3 src`, extraite
dans `/tmp/ogp-reactivity-baseline`.

Jeux : petit projet de 12 rectangles ; grande scène de 2 000 rectangles ;
12 rectangles avec chaîne média ASCII de 8 Mo dans la scène et la bibliothèque ;
12 rectangles avec 10 000 reçus documentaires et 10 000 reçus de bibliothèque.
L’historique testé est celui des reçus persistants, pas une pile undo graphique.
La chaîne média immuable n’est pas recopiée octet par octet par `deepcopy`.

Parcours : ancien `providers.snapshot` puis résumé ; lecture documentaire des
tâches ; page et recherche bibliothèque ; préparation bibliothèque. Les appels
providers séparent attente du bridge, exécution Qt et durée totale sur worker.
Une requête supplémentaire instrumentée fournit les temps **exclusifs** par
catégorie (copie, lecture, hash, JSON, validation, projection, attente, capture,
autre), puis les frontières **inclusives** de copie finale et lecture disque.
Ces deux tableaux ne s’additionnent pas. cProfile modifie les durées : ses temps
ne remplacent pas ceux des échantillons non instrumentés.

Une autre requête mesure le pic d’allocations Python via `tracemalloc`, à
l’échelle du processus, sans inclure les allocations C++ Qt. Le RSS maximal est
cumulatif sur le processus. Un heartbeat Qt de 2 ms mesure les interruptions de
livraison d’événements pendant la boucle ; le temps de soumission des threads
par le harnais est publié séparément. Ni ce heartbeat ni un rendu offscreen ne
prouvent une fluidité visuelle ou une latence d’interaction native.

Les requêtes passent aussi par un vrai serveur HTTP MCP local : temps client
SDK et temps serveur de dispatch sont mesurés séparément. Le transport,
l’ordonnanceur AnyIO, la sérialisation et le client expliquent une partie de
leur différence avec les appels directs. Chaque niveau de concurrence est
borné, puis rejoint avant le suivant : 1, 2, 4, 8, 16, 32 requêtes, six groupes
par niveau. Ce n’est pas une charge d’arrivée continue ni une simulation de
clients sans limite. Les sondes Cocoa montrent un canevas synthétique visible,
avec trois groupes aux niveaux 1, 4, 16 et deux jeux ; elles ne simulent pas de
geste utilisateur ni l’ensemble de l’application.

```sh
OGP_BENCH_SOURCE_ROOT=/tmp/ogp-reactivity-baseline/src .venv/bin/python -X faulthandler scripts/benchmark_agent_reactivity.py --output docs/fork/mcp-reactivity-before.json --repeat 6 --concurrency 1,2,4,8,16,32 --http
.venv/bin/python -X faulthandler scripts/benchmark_agent_reactivity.py --output docs/fork/mcp-reactivity-after.json --repeat 6 --concurrency 1,2,4,8,16,32 --http
QT_QPA_PLATFORM=cocoa .venv/bin/python -X faulthandler scripts/benchmark_agent_reactivity.py --output docs/fork/mcp-reactivity-native-after.json --repeat 3 --concurrency 1,4,16 --datasets small,many --visible
```

Pour la mesure native avant, utiliser la dernière commande avec la variable
`OGP_BENCH_SOURCE_ROOT` et le fichier `mcp-reactivity-native-before.json`.
Les fichiers JSON contiennent les versions de plateforme et tous les percentiles.

## Correction et invariants conservés

La lecture disque, la validation et la préparation de la bibliothèque vivante
étaient exécutées dans le callable Qt. Elles se font maintenant sur le worker
MCP appelant, après un court passage Qt pour obtenir le port configuré. Aucun
QObject ni cache UI n’est utilisé sur ce worker. Application et undo restent
sur Qt, ainsi que la publication et le rafraîchissement du cache botanique UI.
Tous les appelants de `library_operation` ont été examinés : les handlers MCP
exécutent leurs providers synchrones sur AnyIO ; l’hôte autonome garde son port
Python propre. Un appel direct effectué depuis Qt reste synchrone sur Qt.

Les lecteurs sont sérialisés entre eux pour borner leurs allocations. Ils ne
partagent pas le verrou des écrivains : une lecture worker lente ne peut pas
bloquer Qt derrière ce verrou. Les publications atomiques donnent une ancienne
ou une nouvelle version complète ; le CAS disque au commit refuse une
préparation devenue périmée. Le petit verrou du cache des préparations ne couvre
aucune I/O ni aucun callback Qt. Les écrivains gardent leur ordre et leur verrou
fichier. Le cache existant reste limité à 128 préparations, cinq minutes ; aucun
cache de contenu n’est ajouté et aucun nouveau protocole d’invalidation n’est
nécessaire. Les anciennes lectures de document gardent leur copie profonde sur
Qt ; les nouvelles gardent capture/révision cohérentes et projection sur worker.

## Résultats

Le déplacement réduit la section Qt médiane d’une page bibliothèque avec 10 000
reçus de 32,65 ms à environ 0,01 ms. À 32 lectures du fichier média, le plus grand
intervalle sans heartbeat passe de 617 à 25 ms. Sur Cocoa avec 2 000 espèces et
16 lectures, il passe de 187 à 15 ms. Ces mesures soutiennent le déplacement des
lectures ; elles ne prouvent pas une accélération globale du client HTTP, dont
les temps restent comparables ou augmentent dans certains cas.

Les captures de scène restent coûteuses : sur 2 000 objets, environ 38–40 ms
Qt par lecture, et plus d’une seconde sans heartbeat à 32 résumés simultanés.
Sur 10 000 reçus, la copie finale ajoutée est mesurée séparément à 55,5 ms sous
cProfile, dans un parcours non instrumenté de 55,8 ms au total : ces chiffres
appartiennent à des exécutions différentes et ne doivent pas être soustraits.
Aucune optimisation de ces copies n’est livrée sans mécanisme équivalent de
cohérence. La concurrence est reportée du bridge vers la file des lecteurs
bibliothèque, qui reste sérialisée ; la durée totale ne disparaît pas.

Les lectures disque profitent du cache système normal, sans purge. Cette
campagne mesure les parcours applicatifs chauds, pas la latence d’un volume
froid ou distant. Les écarts avant/après sur les chemins inchangés rappellent
la variabilité de la machine ; aucune significativité statistique n’est annoncée.

Les tableaux de mesures consolidées ci-dessous sont produits à partir des
quatre rapports conservés avec ce document.

Rapports : [avant](mcp-reactivity-before.json), [après](mcp-reactivity-after.json), [Cocoa avant](mcp-reactivity-native-before.json), [Cocoa après](mcp-reactivity-native-after.json).

Environnement commun : macOS-27.2-arm64-arm-64bit, Python 3.11.17, PyQt/Qt 6.11.0. Toutes les durées ci-dessous sont en millisecondes.

### Appel séquentiel : médianes sans instrumentation

| Jeu | Parcours | Qt avant → après | Total avant → après |
| --- | --- | ---: | ---: |
| small | `legacy_summary` | 0.25 → 0.23 | 0.31 → 0.30 |
| small | `document_tasks` | 0.25 → 0.25 | 0.37 → 0.35 |
| small | `library_page` | 0.06 → 0.00 | 0.19 → 0.18 |
| small | `library_search` | 0.04 → 0.00 | 0.17 → 0.18 |
| small | `library_prepare` | 0.15 → 0.00 | 0.19 → 0.20 |
| many | `legacy_summary` | 35.57 → 38.41 | 35.80 → 38.70 |
| many | `document_tasks` | 37.75 → 39.72 | 41.14 → 43.53 |
| many | `library_page` | 1.94 → 0.01 | 10.80 → 11.63 |
| many | `library_search` | 1.93 → 0.01 | 12.47 → 12.57 |
| many | `library_prepare` | 2.24 → 0.01 | 2.34 → 2.43 |
| media | `legacy_summary` | 0.37 → 0.41 | 0.45 → 0.49 |
| media | `document_tasks` | 0.32 → 0.29 | 17.06 → 14.68 |
| media | `library_page` | 7.90 → 0.01 | 22.86 → 23.14 |
| media | `library_search` | 7.62 → 0.01 | 23.25 → 20.76 |
| media | `library_prepare` | 6.40 → 0.00 | 6.47 → 6.47 |
| history | `legacy_summary` | 62.17 → 55.44 | 62.70 → 55.78 |
| history | `document_tasks` | 60.02 → 57.81 | 60.67 → 58.24 |
| history | `library_page` | 32.65 → 0.01 | 32.88 → 24.83 |
| history | `library_search` | 26.68 → 0.01 | 26.93 → 25.71 |
| history | `library_prepare` | 24.23 → 0.01 | 24.31 → 27.55 |

### Concurrence : attente et interruption des événements

Les médianes de service Qt ne comprennent pas l’attente du bridge. Le gap est le maximum observé, pas un percentile de latence visuelle.

| Jeu / parcours | N | Attente bridge médiane avant → après | Total p95 avant → après | Gap événements max avant → après |
| --- | ---: | ---: | ---: | ---: |
| many / legacy_summary | 1 | 0.03 → 0.05 | 42.30 → 45.98 | 42.25 → 45.56 |
| many / legacy_summary | 4 | 50.32 → 58.67 | 145.84 → 168.52 | 152.36 → 168.89 |
| many / legacy_summary | 16 | 265.18 → 271.11 | 551.88 → 578.34 | 600.59 → 658.06 |
| many / legacy_summary | 32 | 545.05 → 580.91 | 1076.63 → 1159.41 | 1187.12 → 1278.08 |
| history / legacy_summary | 1 | 0.08 → 0.06 | 72.59 → 70.58 | 72.51 → 70.51 |
| history / legacy_summary | 4 | 79.61 → 79.34 | 203.65 → 217.36 | 199.90 → 214.00 |
| history / legacy_summary | 16 | 385.23 → 373.69 | 798.13 → 771.69 | 858.98 → 822.87 |
| history / legacy_summary | 32 | 809.26 → 769.28 | 1601.23 → 1506.90 | 1731.91 → 1637.26 |
| media / library_page | 1 | 0.08 → 0.07 | 23.75 → 24.11 | 21.03 → 14.42 |
| media / library_page | 4 | 26.17 → 0.15 | 85.43 → 87.08 | 86.62 → 16.75 |
| media / library_page | 16 | 150.83 → 0.29 | 328.75 → 300.91 | 309.22 → 16.71 |
| media / library_page | 32 | 312.17 → 0.31 | 640.93 → 568.41 | 616.87 → 24.88 |
| history / library_page | 1 | 0.06 → 0.06 | 35.05 → 34.68 | 35.02 → 30.40 |
| history / library_page | 4 | 32.88 → 0.12 | 101.36 → 100.61 | 101.33 → 26.93 |
| history / library_page | 16 | 199.29 → 0.48 | 403.87 → 419.84 | 275.46 → 35.56 |
| history / library_page | 32 | 411.94 → 4.25 | 827.11 → 819.77 | 847.20 → 41.50 |

### Coût de copie et allocations : diagnostics séparés

Les frontières de copie incluent leurs appels imbriqués et sont instrumentées. Le total exclusif « copy » du JSON mesure toutes les copies du parcours, y compris celles déjà présentes dans la capture. La copie finale ajoutée n’est donc pas tout le coût de capture.

| Jeu / parcours | Capture snapshot_dict après, inclusive | Copie finale Qt après, inclusive | Pic Python avant → après (Mio) |
| --- | ---: | ---: | ---: |
| small / legacy_summary | 0.74 | 0.20 | 0.023 → 0.023 |
| small / document_tasks | 0.69 | 0.21 | 0.043 → 0.043 |
| many / legacy_summary | 103.60 | 28.41 | 2.563 → 2.625 |
| many / document_tasks | 107.12 | 29.50 | 5.494 → 5.557 |
| media / legacy_summary | 0.70 | 0.21 | 0.023 → 0.023 |
| media / document_tasks | 0.98 | 0.23 | 15.302 → 15.302 |
| history / legacy_summary | 132.65 | 55.50 | 6.318 → 6.318 |
| history / document_tasks | 112.75 | 74.33 | 6.318 → 6.318 |

Bibliothèque : exemple de décomposition exclusive d’une page sur le jeu history. Les catégories heuristiques cProfile séparent les fonctions nommées ; « autre » contient le coût Python non classé.

| Catégorie | Qt avant | Worker après |
| --- | ---: | ---: |
| disk_read | 0.04 | 0.04 |
| json | 4.75 | 5.00 |
| validation | 0.20 | 0.14 |
| hash | 0.15 | 0.17 |
| copy | 35.31 | 34.53 |
| projection | 0.00 | 0.13 |
| wait | 0.00 | 0.11 |
| other | 12.69 | 12.47 |

Les données complètes conservent toutes les catégories pour chaque parcours. Les pics RSS cumulatifs des processus offscreen sont 268.2 Mio avant et 280.1 Mio après ; ils ne sont pas des allocations par requête.

Pics Python bibliothèque, sur une requête diagnostique séparée (Mio) :

| Jeu | Page avant → après | Préparation avant → après |
| --- | ---: | ---: |
| small | 0.039 → 0.044 | 0.041 → 0.039 |
| many | 5.979 → 5.977 | 5.980 → 5.977 |
| media | 22.927 → 22.925 | 22.927 → 22.925 |
| history | 8.055 → 7.946 | 8.055 → 7.947 |

### Transport HTTP : serveur et client distincts

| Jeu / outil | N | Dispatch serveur médian avant → après | Client p95 avant → après |
| --- | ---: | ---: | ---: |
| small / get_plan_summary:default | 1 | 0.49 → 0.54 | 22.73 → 24.20 |
| small / get_document_state:default | 1 | 0.50 → 0.49 | 2.96 → 3.23 |
| small / get_custom_plant_library:default | 1 | 0.32 → 0.37 | 2.62 → 2.80 |
| small / get_custom_plant_library:search | 1 | 0.28 → 0.29 | 2.53 → 2.86 |
| small / prepare_plant_library_import:default | 1 | 0.30 → 0.48 | 2.50 → 4.99 |
| history / get_plan_summary:default | 32 | 66.76 → 70.76 | 2013.23 → 2121.87 |
| history / get_document_state:default | 32 | 67.14 → 71.00 | 2019.20 → 2050.48 |
| history / get_custom_plant_library:default | 32 | 43.14 → 44.45 | 1022.90 → 1076.47 |
| history / get_custom_plant_library:search | 32 | 42.74 → 44.57 | 1021.29 → 1080.22 |
| history / prepare_plant_library_import:default | 32 | 44.29 → 46.34 | 1033.71 → 1140.67 |

### Cocoa : canevas visible, heartbeat natif

| Jeu / parcours | N | Gap événements max avant → après |
| --- | ---: | ---: |
| small / legacy_summary | 16 | 5.27 → 6.06 |
| small / document_tasks | 16 | 6.15 → 7.07 |
| small / library_page | 16 | 3.16 → 2.79 |
| small / library_search | 16 | 3.57 → 2.76 |
| small / library_prepare | 16 | 3.13 → 3.36 |
| many / legacy_summary | 16 | 594.60 → 615.70 |
| many / document_tasks | 16 | 649.31 → 683.50 |
| many / library_page | 16 | 186.82 → 15.41 |
| many / library_search | 16 | 229.51 → 17.90 |
| many / library_prepare | 16 | 55.12 → 16.33 |

Aucune erreur provider ni réponse MCP `isError` dans ces quatre campagnes, avec timeout bridge par défaut de cinq secondes. La concurrence maximale reste 32 et les arrivées ne sont pas continues. La persistance des protections est prouvée par les tests de cohérence, pas par cette absence de timeout.


## Preuves et limites

`test_live_library_worker` vérifie les trois actions de lecture/préparation sur
worker, le détachement des résultats, un appel Qt et une vraie écriture pendant
une lecture disque bloquée, puis préparation/édition Qt/conflit/application/
rejeu/undo. `test_library_port_concurrency` bloque par événements une lecture et
une publication, vérifie le progrès du writer puis le refus d’une préparation
prise pendant une écriture. Les attentes bornées des tests sont des garde-fous,
leurs séquences dépendent d’événements explicites, pas de délais arbitraires.
Les suites bibliothèque, bridge et opérations partagées complètent ces preuves.

Le bridge conserve son comportement : une requête abandonnée avant démarrage
ne s’exécute pas ; après démarrage, un timeout ou une fermeture ne garantit pas
l’annulation. Les tests déterministes `test_agent_api_bridge` couvrent ces deux
états et le résultat indéterminé d’une mutation commencée. Le profil borné ne
remplace pas ces tests et ne justifie pas à lui seul des quotas ou une politique
de backpressure. Les écritures bibliothèque et leurs I/O restent sur Qt ; les
copies de grande scène/historique et le GIL peuvent encore retarder les événements.
La lecture complète et le hash croissent avec le fichier, même pour une page
courte. Une étude de charge continue et une mesure d’interaction dans l’application
native seraient nécessaires pour dimensionner un service ou annoncer une fluidité.

Un premier harnais s’est terminé une fois avec le code 139 sans traceback.
Il ne créait pas de `ComboWheelGuard` ; aucune cause commune n’est établie.
Voir le [ticket non résolu](COMBO_WHEEL_GUARD_INVESTIGATION.md).

La validation finale des corrections comprend 433 tests consolidés réussis,
Ruff global et les catalogues de traduction compilés. La procédure et le groupe
exact figurent dans [la validation des stores](STORES_PERSONNELS.md#validation-livrée-le-28-octobre-2026).
La revue indépendante a vérifié les frontières et verrous ; elle n’a pas répété
les mesures. Les durées publiées ne constituent pas une preuve des invariants.
