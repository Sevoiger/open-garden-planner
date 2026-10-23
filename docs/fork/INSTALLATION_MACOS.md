# Jardin sur macOS

Prérequis : Git et `uv` doivent être installés et accessibles dans le Terminal.
Le script télécharge son Python 3.11 si nécessaire ; aucun Python global récent
n’est requis pour lire la configuration.

Depuis le dossier du projet, exécuter `bash scripts/setup_macos.sh` une fois.
Le script utilise Python 3.11 natif, le verrou `uv.lock` et une installation
éditable dans `.venv`. Une connexion Internet est nécessaire à la préparation.

Ouvrir ensuite **Jardin.app** par double-clic : aucune fenêtre Terminal ne s'ouvre.
Pour déboguer, ouvrir **Lancer-Jardin.command** : les messages défilent dans le
Terminal et sont aussi enregistrés dans le journal. Le lanceur ne réinstalle
rien. Pour modifier l’application : fermer la fenêtre, modifier les fichiers sous
`src/open_garden_planner/`, puis rouvrir le lanceur. `Jardin.app` est un simple
lanceur vers les sources, à conserver dans le dossier du projet ; aucune
reconstruction n'est nécessaire.
L'entrée du bundle est un petit exécutable natif signé localement (signature
ad hoc, sans certificat Developer ID ni notarisation). Il évite le blocage
d'accès au dossier Documents rencontré avec une entrée Bash. Ses sources sont
dans `scripts/macos_launcher.m`. Après modification de ce lanceur natif seulement,
exécuter `bash scripts/build_macos_launcher.sh` pour le recompiler et le signer ;
la préparation macOS le fait également. Les modifications Python ne nécessitent
pas de nouvelle signature.
Après un changement de dépendances seulement, relancer le script de préparation.
Les scripts de contrôle du verrou et de préparation de version utilisent par
défaut le `uv` déjà installé dans `.tools/bootstrap` ; `--uv` permet de choisir
explicitement un autre exécutable. Le contrôle continue d’exiger la version exacte
du projet, même si un ancien `uv` reste accessible dans le PATH.
Si le dossier complet est déplacé, relancer également la préparation (les chemins
des environnements Python ne sont pas portables).

Le journal du lanceur est `.local/logs/launcher.log` dans le projet. Les erreurs
applicatives disposent aussi du journal rotatif habituel dans le dossier macOS
Application Support. En cas d’environnement manquant, le lanceur affiche la
commande à exécuter. En cas de plantage, `Jardin.app` affiche une alerte avec le
code et le chemin du journal ; le mode debug affiche aussi la fin du journal.

Les documents et projets privés doivent rester sous `sources-jardin/` ou
`jardin-personnel/`, exclus de Git. Ne jamais les joindre à une PR.

Sur macOS, les contrôles utilisent le style Qt Fusion pour que le thème de
l’application reste cohérent avec un macOS clair ou sombre. À faible largeur,
la barre d’état garde coordonnées/saisie/zoom et masque les détails secondaires ;
agrandir la fenêtre les rétablit.

Fermer l’application avant une suite de tests Qt. La validation du 23 octobre
comprend des tests natifs Cocoa et une revue de captures françaises à 13 pt des
fenêtres principale, données jardin et orientation ; les contrôles passent sur
des données et réglages isolés. Le détail et les limites sont dans
[l’audit technique](AUDIT_TECHNIQUE_2026-10-23.md).

Le contrôle de typage peut maintenant s’exécuter sur macOS :
`.venv/bin/python scripts/check_mypy_baseline.py`. La référence Darwin est séparée
des références Linux/Windows. Elle conserve les diagnostics hérités par fichier ;
réussir ce contrôle signifie qu’aucune régression n’est apparue, pas que toute
la dette de typage est supprimée.
