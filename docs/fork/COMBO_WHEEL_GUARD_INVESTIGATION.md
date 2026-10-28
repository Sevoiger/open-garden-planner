# Investigation : callback `ComboWheelGuard` après destruction

**État : non reproduit dans le run de diagnostic, cause racine ouverte.** Aucun
changement du filtre ou de son propriétaire n’est justifié par les éléments
actuels. Ce document conserve le symptôme précis et la procédure de reprise.

## Trace historique

Dans le premier cas d’un run combiné, avant tout résultat PASS/FAIL, le traitement
d’événements de Qt est entré dans
`src/open_garden_planner/ui/widgets/combo_wheel_guard.py:22`, puis PyQt a levé :

```text
RuntimeError: wrapped C/C++ object of type ComboWheelGuard has been deleted
Fatal Python error: Segmentation fault
```

La pile restante nommait `pytestqt/plugin.py:_process_events`,
`pytest_runtest_call` et pytest/pluggy, sans cadre applicatif plus précis. Le
symptôme établit qu’un callback Python `eventFilter` a été demandé alors que son
wrapper C++ n’était plus valide. Il n’établit pas pourquoi Qt aurait encore ce
filtre enregistré. La ligne 22 reçoit tous les événements; la trace ne dit pas
qu’il s’agissait d’une molette.

Commande exacte communiquée pour ce run :

```bash
.venv/bin/python -m pytest \
  tests/integration/test_shared_document_operations.py::test_manual_save_keeps_document_lease_and_refuses_external_overwrite \
  tests/integration/test_shared_document_operations.py::test_live_preview_apply_undo_and_gui_conflict \
  tests/unit/test_document_operations.py \
  tests/unit/test_command_history.py \
  tests/unit/test_library_operations.py
```

Environnement historique rapporté : macOS, Python 3.11.17, pytest 9.1.1,
pytest-qt 4.5.0, PyQt6 6.11.0 et Qt compilé/runtime 6.11.0. La valeur de
`QT_QPA_PLATFORM` n’a pas été conservée. Le `tests/conftest.py` courant choisit
`offscreen` avec `setdefault`, sauf si le shell définit déjà la variable.

Le premier test ne montre pas la fenêtre et ne génère pas de geste de molette. Il
crée une `GardenPlannerApp`, exerce deux sauvegardes puis la ferme dans un
`finally`. `qtbot` garde une référence faible et ferme/supprime les widgets à la
fin du test. La fenêtre installe le filtre sur `QApplication` et le garde comme
enfant de la fenêtre. Le premier test ne déclenche aucun appel explicite depuis un
worker; le test suivant utilise un `ThreadPoolExecutor`, dont l’accès UI passe
par le pont Qt existant. Le conftest redirige QSettings et les données fichiers
vers des emplacements synthétiques de test. Ces faits rendent le
teardown/dispatch Qt du harnais plus plausible que le chemin de transfert d’un
événement de molette, sans identifier le déclencheur. Aucun run interactif natif
de production n’a capturé ce crash; ces éléments ne déterminent donc pas si
l’interface livrée est touchée.

## Vérifications de reprise

Sur le checkout courant, macOS 27.0.1 Apple Silicon, Python 3.11.17, pytest
9.1.1, pytest-qt 4.5.0, PyQt6/Qt 6.11.0 :

- La commande exacte ci-dessus, lancée avec `-X faulthandler -vv`, passe :
  **59 tests en 3,21 s**.
- Les deux tests d’intégration de la commande ont passé dans **20 processus
  séparés** (40 cas, aucune exception ni segfault).
- `tests/integration/test_combo_wheel_guard.py` a passé **20 processus séparés**
  (10 cas par processus, 200 passages).
- Une sonde de 100 cycles a créé un filtre enfant installé sur `QApplication`,
  détruit son parent avec `sip.delete`, puis envoyé un nouvel événement : Qt
  n’a rappelé aucun filtre détruit.
- La variable `QT_QPA_PLATFORM` était absente du shell de ces essais; pytest a
  donc utilisé le défaut `offscreen` du conftest.

Les essais ne reproduisent pas la fenêtre temporelle qui a causé la trace
historique. La sonde confirme seulement que la destruction directe d’un parent
ne suffit pas à produire le callback périmé sur cette version de Qt.

## Hypothèses à départager

1. Une course de durée de vie entre suppression différée du widget (`close()` puis
   `deleteLater()` dans le harnais) et dispatch d’événements vers l’application.
   Le message d’erreur soutient cette piste; les cycles directs et les séquences
   de tests répétés ne la reproduisent pas.
2. Une corruption plus tôt dans le processus, éventuellement liée à un autre
   objet Qt ou worker, qui ne se manifeste qu’au prochain `processEvents()`. La
   pile disponible ne permet pas de l’exclure ni de l’attribuer.
3. Une interaction propre à la version/backend Qt qui n’est pas caractérisée,
   car le backend du run initial manque. L’erreur seule ne prouve pas un défaut
   de Qt/PyQt.
4. Une violation de thread Qt est possible en théorie, mais rien dans le premier
   test ne montre d’appel GUI depuis un worker. Des processus pytest séparés ont
   chacun leur `QApplication` : les exécuter en parallèle ne constitue pas, à
   lui seul, une violation du thread UI. Aucun élément ne prouve ici un test Qt
   concurrent dans un même processus.

## Reprise si le crash revient

1. Relancer exactement la commande ci-dessus dans un seul processus pytest, sans
   autres runs Qt concurrents, en enregistrant `QT_QPA_PLATFORM` avant le
   lancement et en activant `-X faulthandler`.
2. Conserver la sortie complète et le rapport de crash macOS dans un fichier
   temporaire dédié; noter le backend, l’ordre des tests et le premier test
   commencé au moment du crash.
3. Ajouter pour cette reproduction seulement une instrumentation temporaire
   autour des créations/destructions du garde et de `eventFilter` : identité du
   garde, état `sip.isdeleted`, thread Qt courant et thread d’affinité, type
   d’événement, parent QObject et instant de fermeture/suppression différée.
   Retirer cette instrumentation après l’essai.
4. Si aucun crash ne survient, comparer un seul backend explicite à la fois et
   réduire l’ordre des tests par élimination. Ne modifier ni les assertions ni
   le propriétaire du filtre sans reproduction qui relie cette action au
   callback périmé.

Pas de régression ajoutée : aucun scénario déterministe qui reproduit le défaut
n’a été établi, et un test de simple destruction confirmerait seulement le
comportement Qt déjà observé.

## Incident distinct pendant le profil complémentaire

Une première exécution du nouveau harnais de réactivité s’est terminée avec le
code 139, sans traceback exploitable. Ce harnais lie les providers à un objet
Python minimal et ne construit ni `GardenPlannerApp` ni `ComboWheelGuard`.
Ce signal ne permet donc pas de reproduire ni d’attribuer le défaut ci-dessus.
Le harnais attend désormais la livraison de chaque signal Qt de fin de requête,
plutôt que le seul état `Future.done()` observé au premier signal ; cela précise
sa frontière de durée de vie, sans preuve que cette modification explique le
code 139. Les exécutions conservées utilisent `-X faulthandler`.

Le 28 octobre, le groupe final de 433 tests inclut aussi les dix cas du filtre,
sans reproduction. Le système des mesures complémentaires est macOS 27.2,
avec les mêmes versions Python/PyQt/Qt indiquées plus haut. L’absence du crash
sur ce nouvel environnement ne ferme pas le diagnostic historique.
