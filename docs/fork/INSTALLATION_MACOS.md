# Jardin sur macOS

Depuis le dossier du projet, exécuter `bash scripts/setup_macos.sh` une fois.
Le script utilise Python 3.11 natif, le verrou `uv.lock` et une installation
éditable dans `.venv`. Une connexion Internet est nécessaire à la préparation.

Ouvrir ensuite **Lancer-Jardin.command** par double-clic. Le lanceur ne réinstalle
rien. Pour modifier l’application : fermer la fenêtre, modifier les fichiers sous
`src/open_garden_planner/`, puis rouvrir le lanceur. Aucun paquet `.app` à reconstruire.
Après un changement de dépendances seulement, relancer le script de préparation.
Si le dossier complet est déplacé, relancer également la préparation (les chemins
des environnements Python ne sont pas portables).

Le journal du lanceur est `.local/logs/launcher.log` dans le projet. Les erreurs
applicatives disposent aussi du journal rotatif habituel dans le dossier macOS
Application Support. En cas d’environnement manquant, le lanceur affiche la
commande à exécuter. En cas de plantage, il affiche le code et la fin du journal.

Les documents et projets privés doivent rester sous `sources-jardin/` ou
`jardin-personnel/`, exclus de Git. Ne jamais les joindre à une PR.

Sur macOS, les contrôles utilisent le style Qt Fusion pour que le thème de
l’application reste cohérent avec un macOS clair ou sombre. À faible largeur,
la barre d’état garde coordonnées/saisie/zoom et masque les détails secondaires ;
agrandir la fenêtre les rétablit.

Fermer l’application avant une suite de tests Qt. La première ouverture a révélé
des défauts de présentation ; les corrections automatiques restent à confirmer
visuellement sur votre écran. Les clics apparents ne sont pas encore expliqués.
