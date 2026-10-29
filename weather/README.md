# Collecte météo personnelle — Briollay

Collecteur autonome utilisable sans Qt et sans ouverture du jardin. Les données
restent dans `.local/weather/briollay/` (ignoré par Git). Il ne modifie ni le plan
ni la bibliothèque. L'onglet météo existant continue son propre fonctionnement.

## Exécution et collecte quotidienne

Depuis la racine du dépôt, avec `curl` et le Python existant :

```sh
.venv/bin/python -m open_garden_planner.services.weather_collection \
  --config weather/config.json --output .local/weather/briollay
```

Par défaut, une tentative par jour civil Europe/Paris, réussie ou partielle :
le changement de date autorise la suivante, même lors du passage à l’heure d’été.
Pour une valeur de `refresh_hours` différente de 24, le cache utilise la durée
écoulée exacte. `--force` permet une reprise manuelle ; `--status` consulte
les archives et recalcule leur ancienneté sans réseau. Code retour : 0 toutes les
sources sont récentes, 1 au moins une source périmée/indisponible, 2 erreur de
configuration, stockage ou collecte déjà en cours. Les erreurs sont dans le JSON.
Les archives ne sont pas purgées automatiquement ; supprimer des anciens runs
seulement après avoir vérifié qu'aucun chemin de `latest.json` ne les référence.

Exemple de ligne **crontab**, à adapter au chemin du dépôt. Le wrapper résout les
chemins, charge la configuration et écrit le journal. Le Mac doit être allumé ;
cron ne rattrape pas les exécutions pendant la veille. L'accès macOS au dossier
Documents doit être autorisé pour le processus chargé de la tâche.

```cron
15 8 * * * /bin/sh /Users/rauperpin/Documents/OpenGardenPlanner/weather/collect.sh
```

Cette ligne est fournie comme configuration prête à installer ; elle n'est pas
installée par le collecteur. L'heure de cron est celle du système (Europe/Paris
sur le Mac cible). Il n'y a pas de service réseau ni de clé API à configurer.

## Configuration et sources

`config.json` fixe le point géocodé le 29 octobre 2026 : **47.56446, -0.50805**,
GeoNames 3030020 via le géocodeur Open-Meteo, commune de Briollay, 49125,
altitude indicative 20 m. Ce n'est pas une localisation de parcelle. Le fuseau
Europe/Paris gère les changements d'heure. Modifier coordonnées, nom, fuseau,
contexte jardin et page locale ensemble si la commune change.

- Open-Meteo, requêtes séparées à modèles explicites : AROME France 48 h,
  ARPEGE Europe 96 h, ECMWF IFS 0.25° et GFS Global 168 h. Chaque requête demande
  un horizon prudent, **sans prolonger** la disponibilité effective d'un modèle.
  La couverture réelle des données non nulles est exportée. Aucun `best_match`
  ni assemblage seamless. Les données horaires peuvent être interpolées depuis
  des sorties natives moins fréquentes et corrigées de l'altitude par le fournisseur.
- Meteociel : page locale brute (prévisions classiques GFS), cartes AROME HD
  température 2 m (`mode=100`) et précipitations (`mode=101`), zone Nord-Ouest
  (`map=1`), échéances +3, +12, +24, +36 et +48 h. Deux pages de cartes sont lues,
  puis seules les URL PNG **avec run explicite publiées dans ces pages** sont
  téléchargées. Aucune URL d'image devinée. Un changement de HTML ou une échéance
  manquante est un échec explicite, pas une image de remplacement.

Pas de collecte radar, d'observations, d'ensembles, de sol, de visibilité ou de type
précis des précipitations en v1. Ces limites évitent de présenter comme disponibles
les nombreuses options du prompt initial. Pas de recommandation automatique.

Sources et conditions vérifiées le 29 octobre 2026 :
[API Météo-France Open-Meteo](https://open-meteo.com/en/docs/meteofrance-api),
[API générale et définitions](https://open-meteo.com/en/docs),
[conditions Open-Meteo](https://open-meteo.com/en/terms) : usage personnel
non commercial, attribution CC BY 4.0 ;
[mentions Meteociel](https://www.meteociel.fr/copyright/mentions.php) : usage
commercial interdit ; [robots.txt](https://www.meteociel.fr/robots.txt) consulté,
aucune interdiction générale applicable à notre client identifié sur les chemins
choisis. Cela ne constitue pas une licence de redistribution. Garder les cartes
et leurs mentions dans les archives personnelles, ne pas les publier dans la PR.

Collecte HTTPS via `curl`, User-Agent identifié, pas de cookies ni de contournement,
pas de redirections, délai de connexion 10 s et requête 40 s, une reprise sur les
erreurs temporaires prises en charge par curl, 2 s entre appels, 12 Mio maximum
par réponse. HTTP 403/429 ou autre échec devient un statut d'erreur. Verrou local
contre les collectes simultanées. Les JSON sont publiés par remplacement atomique.

## Export pour le second agent

`latest.json` est le point d'entrée ; `runs/<date UTC>/manifest.json` fige chaque
tentative. Les chemins des fichiers sont relatifs au dossier de `latest.json`,
y compris lorsqu'une ancienne archive est réutilisée après échec.

| Champ | Sens |
| --- | --- |
| `schema_version` | Format 1 |
| `attempted_at`, `checked_at` | Dates UTC ISO 8601 de collecte et vérification |
| `config_sha256` | Empêche de réutiliser un cache d'une autre configuration |
| `location`, `garden_context` | Point demandé et contexte, sans données personnelles de jardin |
| `sources.<id>.state` | `ok`, `fallback` (ancienne réussite), `unavailable` |
| `fetched_at`, `age_hours`, `stale`, `error` | Fraîcheur de chaque source et cause de l'échec |
| `raw`, `page`, `maps[]` | Fichiers sources, URL et empreinte SHA-256 |
| `model`, `grid`, `model_run_at` | Modèle exact, point/altitude retournés, heure de calcul si connue |
| `hours`, `units`, `missing` | Séries horaires à dates ISO avec décalage Paris, unités, manques |
| `coverage_start`, `coverage_end`, `missing_hours` | Disponibilité réelle et trous horaires |
| `daily` | Résumés calculés sur jours civils Europe/Paris (23/24/25 h) |

L'API ne fournit pas l'heure du run dans ces réponses : `model_run_at=null` ;
`generationtime_ms` est un temps de traitement, pas une date de calcul.
Les cartes ont un run explicite UTC, une échéance et une date de validité.
Ne pas supposer que les cartes et séries proviennent du même run AROME.

Température/rosée à 2 m : °C ; humidité et nuages : % ; vent moyen et rafales
à 10 m : km/h ; direction : degrés ; pression mer : hPa ; rayonnement : W/m² ;
précipitations totales (équivalent eau) et ET₀ FAO : mm sur **l'heure précédente** ;
rayonnement : moyenne sur l'heure précédente. Pas de conversion depuis un cumul
de run, pas de neige en hauteur. Les valeurs nulles restent nulles. Unités,
longueurs, doublons et ordre des heures sont contrôlés ; les trous sont signalés.

Dans `daily`, les extrema et `frost_samples` concernent les températures aux
instants du jour : des échantillons horaires, pas une durée exacte de gel. Les
sommes incluent les intervalles se terminant entre 01 h et minuit suivant.
Un cumul ou pluie–ET₀ incomplet vaut **null** ; les comptes de points sont fournis.
Pas de minimum nocturne, épisodes, séquences de gel ou fenêtres glissantes en v1.
Les modèles restent séparés : comparer les mêmes périodes et leur complétude.
Toutes ces archives sont des **prévisions**, jamais de la pluie observée.

`stale` et `age_hours` sont des constats datés : utiliser `--status` ou recalculer
l'âge depuis `fetched_at` au moment de la lecture. Une collecte quotidienne ne
permet pas le suivi de pluie imminente. En cas d'échec, le dernier fichier valide
reste disponible et garde sa date ; aucune nouvelle réussite n'est simulée.

## Analyse IA des cartes

Donner au second agent `analyse-prompt.md`, `latest.json` et les PNG référencés
(ou accès au dossier local). Le prompt demande une lecture visuelle des cartes,
une comparaison numérique des modèles et un bulletin court pour le jardin.
Il ne suffit pas d'envoyer le seul JSON à un agent dépourvu d'accès aux images.

L'exécution quotidienne prépare les données ; elle ne lance pas de fournisseur IA
et ne transmet pas d'images à un tiers. L'agent déjà utilisé peut lire ce dossier.
Une API d'analyse automatique pourra être branchée ensuite avec le fournisseur
et le mode d'authentification choisis par le propriétaire.
