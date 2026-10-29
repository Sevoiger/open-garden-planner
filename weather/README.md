# Collecte météo du jardin — Briollay

Collecteur autonome, sans Qt ni ouverture du jardin. Archives personnelles dans
`.local/weather/briollay/`, ignoré par Git. La configuration de ce dossier est
indépendante de l'onglet météo existant ; aucun plan ni bibliothèque n'est modifié.

## Exécution

Depuis la racine du dépôt :

```sh
sh weather/collect.sh
```

Le wrapper résout les chemins et écrit `cron.log` dans le dossier des archives.
Commande directe pour voir le résultat :

```sh
.venv/bin/python -m open_garden_planner.services.weather_collection \
  --config weather/config.json --output .local/weather/briollay
```

`--status` lit les archives et recalcule leur ancienneté sans réseau ; `--force`
permet une reprise manuelle. Par défaut une tentative par jour civil Europe/Paris,
réussie ou partielle ; le changement de date autorise la suivante, même après
une journée de 23 heures. Pour `refresh_hours` différent de 24, durée écoulée exacte.
Retour 0 : sources récentes ; 1 : source périmée/indisponible ; 2 : erreur de
configuration, stockage ou collecte déjà en cours. Consulter aussi les échéances
manquantes : un produit peut être récent mais incomplet.

Exemple crontab prêt à installer, **non installé par le collecteur** :

```cron
15 8 * * * /bin/sh /Users/rauperpin/Documents/OpenGardenPlanner/weather/collect.sh
```

Le Mac doit être allumé ; cron ne rattrape pas la veille. L'heure est celle du
système (Europe/Paris sur le Mac cible). macOS doit autoriser l'accès à Documents
pour le processus chargé de la tâche. Aucune clé API requise.

## Périmètre choisi pour le bulletin jardin

Les chiffres locaux proviennent d'abord des séries structurées. Les cartes donnent
le contexte régional et permettent l'analyse visuelle de la pluie, du froid et du
vent. On ne télécharge pas tout le catalogue Meteociel.

| Source | Horizon demandé | Cartes sélectionnées par défaut |
| --- | --- | --- |
| AROME HD 1,3 km, Nord-Ouest | +12/+24/+36/+48 h | Température 2 m, pluie par intervalle, **cumul pluie**, type de précipitations, rosée, humidité 2 m, vent 10 m, rafales, couches nuageuses |
| AROME HD minima/maxima | +6/+18/+30/+42 h | Minima et maxima 2 m, aux échéances propres à ces produits |
| ARPEGE, Nord-Ouest | +72/+96 h | Température, pluie par intervalle/cumul, vent, rafales, nébulosité, humidité |
| ECMWF IFS 0,25°, France | +120/+168/+240/+360 h | Température, pluie par intervalle/cumul, vent, rafales, nébulosité |
| GFS, Europe | +168/+240/+360/+384 h | Température, pluie par intervalle/cumul et vent |

Cela représente **98 images demandées** par collecte, à disponibilité effective.
Les échéances sont depuis le run de chaque image, pas depuis le téléchargement :
+360 h d'un run d'hier ne signifie pas quinze jours après aujourd'hui. Les cartes
lointaines sont un contexte de tendance ; le détail horaire n'y est pas une certitude.

AROME ne peut pas aller à quinze jours. La relève est assurée par ARPEGE, puis
ECMWF/GFS. Les modèles restent séparés. Le collecteur ne fabrique ni cartes ni
prolongements ; il expose les échéances disponibles et manquantes. Les menus
Meteociel utilisent des codes de zone différents selon les modèles : ARPEGE
`map=301` désigne le Nord-Ouest, AROME `map=1`, ECMWF `carte=2` la France.

`config.json` contient les produits **activés**, libellés, sens physique et échéances.
`map-catalogue.json` conserve comme référence les 62 produits AROME HD et 2,5 km
fournis par le propriétaire ; ce catalogue **ne déclenche aucun téléchargement**.
Une entrée peut être copiée dans le groupe correspondant pour un besoin ultérieur.
Altitude, jet-stream, MUCAPE, confort thermique, radar et satellite simulés restent
hors du profil jardin par défaut. Les variantes AROME 2,5 km ne sont pas doublonnées.

Séries Open-Meteo : AROME France 48 h, ARPEGE Europe 96 h, ECMWF IFS 0,25° et GFS
Global 168 h. Requêtes séparées, aucun `best_match` ou seamless. Température,
humidité, rosée, précipitations, vent/direction/rafales, nuages totaux et bas,
pression mer, rayonnement et ET₀ selon disponibilité ; les absences restent nulles.
Les données horaires peuvent être interpolées depuis des sorties natives plus
espacées et corrigées de l'altitude par le fournisseur.

Ensembles **ECMWF IFS 0,25° et GEFS 0,5°**, jusqu'à 15 jours demandés : membres de
température 2 m et précipitations conservés ; médiane, P10/P90 et fractions de
membres sous 0 °C ou avec au moins 0,1 mm sur l'heure précédente. GEFS 0,25° ne
couvre que dix jours chez ce fournisseur : on choisit explicitement GEFS 0,5°.
Les comptes de membres présents sont exposés à chaque heure. Ces fractions sont
des distributions brutes d'ensemble, **pas des probabilités locales calibrées**.
Ne pas sommer des médianes horaires pour prétendre obtenir une médiane de cumul.
Pour une probabilité sur plusieurs heures/jours, agréger chaque membre d'abord.

Pas de radar observé, stations, mesures de sol, visibilité ni recommandations
automatiques. Pluie–ET₀ ne mesure pas la réserve en eau du terrain sableux.

## Provenance, fraîcheur et export JSON

Point géocodé le 29 octobre 2026 : **47.56446, -0.50805**, GeoNames 3030020 via
Open-Meteo, Briollay 49125, altitude indicative 20 m. Il s'agit du centre de commune,
pas d'une parcelle. Coordonnées et fuseau sont modifiables dans `config.json`.

`latest.json`, format **2**, est le point d'entrée. Chaque tentative terminée a son
`runs/<date UTC>/manifest.json`. Les chemins des fichiers sont relatifs au dossier
de `latest.json`, y compris pour un repli sur une archive ancienne.

| Champs | Sens |
| --- | --- |
| `attempted_at`, `checked_at`, `config_sha256` | Début de tentative, vérification et identité de configuration |
| `sources.<id>.state` | `ok`, `fallback` (ancienne réussite), `unavailable` |
| `fetched_at`, `age_hours`, `stale`, `error` | Fraîcheur de chaque source et cause d'échec |
| `raw`, `pages[]`, `maps[]` | Fichiers sources, URL et SHA-256 |
| `family`, `mode`, `label`, `semantics` | Produit exact ; instant, intervalle, cumul, extrême ou autre diagnostic |
| `requested_map_catalogue` | Sélection demandée, distincte de la référence exhaustive non activée |
| `requested_leads_hours`, `available_leads_hours`, `missing_leads_hours` | Couverture des cartes réellement publiées |
| `maps[].model_run_at`, `lead_hours`, `valid_at` | Run UTC, échéance, validité de **chaque image** |
| `mixed_runs`, `published_runs`, `run_selection` | Signale notamment le recours à un ancien run pour une échéance lointaine |
| `hours`, `units`, `missing`, `grid` | Séries déterministes, unités, manques et point/altitude retournés |
| `daily` | Résumés sur jours civils Paris de 23/24/25 h, avec comptes de points |
| `valid_at`, `members`, `statistics` | Dates, membres et statistiques des sources `ensemble_*` |
| `coverage_start`, `coverage_end`, `missing_hours` | Couverture numérique réelle, qui peut être inférieure à la demande |

Les URL PNG sont prises littéralement dans les pages, avec un run explicite ;
aucun nom d'image n'est deviné. GFS répartit ses échéances sur deux pages. Certains
sites exposent un run court récent et un run long plus ancien : choisir le run
publié le plus récent **pour chaque échéance**, et conserver sa date. Ne pas
additionner ou différencier des cartes de cumul issues de runs différents.

L'API numérique n'expose pas ici l'heure du calcul : `model_run_at=null`, jamais
la date de téléchargement à sa place. `generationtime_ms` est une durée technique.
Les cartes et séries ne sont pas nécessairement issues du même run ni de la même maille.

Température et rosée : °C ; humidité et nuages : % ; vent à 10 m : km/h ; direction :
degrés ; pression mer : hPa ; rayonnement moyen : W/m² ; précipitations équivalent
eau et ET₀ : mm sur **l'heure précédente**. Les cartes ont leurs légendes propres :
`mode=101` est un intervalle, `mode=125` une accumulation, jamais interchangeables.
Pour neige, extrêmes et cumuls, lire l'origine/intervalle/unité exacts de la légende.
Les valeurs manquantes ne valent pas zéro ; unités, longueurs, dates et nombres
sont validés. Aucun cumul de run n'est transformé en pluie horaire par le collecteur.

Les extrema et `frost_samples` journaliers concernent les instants du jour :
échantillons horaires, pas durée exacte de gel. Les cumuls concernent les intervalles
se terminant de 01 h à minuit suivant ; incomplets, ils valent null, comme pluie–ET₀.
Toutes les archives sont des prévisions, jamais un historique de pluie observée.

`stale` et `age_hours` sont datés : recalculer depuis `fetched_at` à la lecture ou
utiliser `--status`. La fraîcheur du téléchargement ne garantit pas celle du run.
En cas d'échec, les dernières données valides de la même configuration gardent
leurs fichiers et leurs dates. Verrou contre les exécutions simultanées et publication
JSON par remplacement atomique. Pas de purge automatique ; ne supprimer aucun run
encore référencé par `latest.json`. Un essai interrompu peut laisser un dossier sans
manifeste, sans remplacer le dernier manifeste valide.

## Analyse IA et conditions des sources

Fournir `analyse-prompt.md`, `latest.json` et les images référencées au second agent.
Un agent sans accès aux fichiers doit recevoir les PNG, pas seulement le JSON.
Le collecteur ne lance pas de fournisseur IA et n'envoie pas les cartes à un tiers.

Sources consultées le 29 octobre 2026 :
[Open-Meteo Météo-France](https://open-meteo.com/en/docs/meteofrance-api),
[API générale](https://open-meteo.com/en/docs),
[ensembles et horizons](https://open-meteo.com/en/docs/ensemble-api),
[conditions Open-Meteo](https://open-meteo.com/en/terms) (personnel non commercial,
attribution CC BY 4.0), [mentions Meteociel](https://www.meteociel.fr/copyright/mentions.php)
(usage commercial interdit) et [robots.txt](https://www.meteociel.fr/robots.txt).
Conserver les cartes et leurs mentions dans les archives personnelles, sans les
publier dans la PR. Aucune licence de redistribution générale n'est déduite de l'accès HTTP.

HTTPS via curl, client identifié, pas de cookies, redirection ni contournement ;
connexion 10 s, requête 40 s, une reprise limitée, une seconde entre appels,
12 Mio maximum par réponse. Un blocage HTTP devient un échec explicite.
