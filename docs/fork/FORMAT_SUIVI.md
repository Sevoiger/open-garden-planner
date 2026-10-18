# Format du suivi pérenne et compatibilité

Le document JSON `.ogp`, au format de fichier 1.4, reste la source de référence.
Aucune base de données ni aucun serveur compagnon n’est ajouté. Voir ADR-059.
Les nouvelles clés s’ajoutent au format existant :

- `objects[].metadata.garden_tracking` contient le type `kind` (`individual`,
  `group`, `zone`), l’état `state`, la période `period`, ainsi que les champs
  facultatifs `quantity`, `zone_id`, `depth_cm`, les médias et la provenance.
  Les UUID et la géométrie des objets existants sont conservés. Le nom reste
  `objects[].name` ; notes et cultivar réutilisent `metadata.plant_instance.notes`
  et `variety_cultivar`. Ces champs n’ont pas de deuxième stockage modifiable.
- `garden_memory = {"version":1,"events":{event_id:event},"requests":{key:receipt}}`
  est l’unique stockage des interventions. Les anciennes notes positionnelles du
  journal restent des notes : elles ne deviennent pas des interventions dupliquées.
  Une transplantation s’enregistre explicitement ; déplacer le dessin n’en crée
  pas une. Le lien automatique `parent_bed_id` conserve son rôle géométrique et
  de conteneur. `garden_tracking.zone_id` désigne l’association explicite avec une
  zone ; il ne modifie ni le parent Qt ni le déplacement des enfants.

Une période s’écrit `{precision,value,label}`. Sa précision est `day`
(`YYYY-MM-DD`), `month` (`YYYY-MM`), `season` (`YYYY-spring/summer/autumn/winter`),
`year` (`YYYY`) ou `unknown` (valeur vide). Le libellé d’origine est conservé.
Une date exacte renseigne aussi l’ancien champ `plant_instance.planting_date` ;
une date partielle ou inconnue retire ce champ exact. Un ancien projet sans suivi
reprend sa date exacte si elle existe ; sinon elle reste inconnue. Le chargement
n’inscrit jamais la date du jour à la place d’une date manquante.

La recherche emploie des limites calendaires conventionnelles de l’hémisphère
nord : printemps de mars à mai, été de juin à août, automne de septembre à novembre,
hiver de décembre à février de l’année suivante. Les filtres de dates utilisent
un recouvrement inclusif ; les dates inconnues apparaissent uniquement en l’absence
de bornes. Le tri se fait par début conventionnel puis identifiant stable ; les
dates inconnues viennent en dernier. Ces limites calculées ne sont pas enregistrées
comme dates exactes d’événement. Saisons et années acceptent 0001 à 9998 ; jours et
mois exacts suivent le calendrier ISO pris en charge par Python. Ces intervalles
logiciels ne décrivent ni la phénologie locale ni une observation météorologique.

Les états sont `planned`, `in_place`, `dormant`, `uncertain`, `confirmed_dead` et
`removed_moved`. Dormance ne signifie jamais mort. Un archivage par état conserve
l’objet et son identité. Chaque intervention possède un identifiant stable, un
horodatage de création UTC, une période, un type, un statut (`planned` ou `done`),
une description non vide et une ou plusieurs cibles `{id,name}`. Le nom constitue
un repère historique lisible si la cible est supprimée ; renommer un objet ne
rompt pas son lien UUID. Quantité, surface historique traitée (`area_m2`) et
profondeur (`depth_cm`) sont facultatives, finies et positives ou nulles. Une valeur
absente diffère de zéro ; la saisie accepte la virgule décimale. Matériaux, unités,
modalités d’application et médias relatifs au projet sont facultatifs. Aucune
intervention ne recalcule une mesure de laboratoire ni ne déduit une masse d’un volume.

La première écriture explicite d’une fiche ou intervention active la continuité
pérenne, avec un stockage versionné non vide. Copier une saison conserve alors tous
les identifiants, notes, interventions, reçus de requêtes et historiques. Une copie
ordinaire par presse-papiers crée une identité d’objet OGP ordinaire ; elle ne
reproduit pas les interventions. Une plantation explicitement copiée garde son
lien de zone jusqu’à réaffectation. Les anciens projets sans suivi conservent le
comportement amont de création d’une nouvelle saison.

Les écritures de l’interface et du MCP passent par `GardenMemoryService` et
`GardenEditCommand` sur le thread Qt, avec état modifié et annulation/rétablissement
globaux. Un reçu de création garde une empreinte canonique du contenu et l’identifiant
réel de l’intervention. Rejouer une requête avec un autre contenu provoque un conflit.
Supprimer une intervention conserve son reçu : une nouvelle tentative ne la recrée
pas. Annuler/rétablir restaure les instantanés des événements tout en conservant les
reçus du protocole. Une tentative tardive après annulation ne ressuscite donc pas
l’intervention ; un ajout volontaire utilise une nouvelle clé de requête. L’heure
de création et l’identité ne sont pas modifiables. Les nouveaux médias doivent
pointer vers des fichiers situés dans le projet enregistré.

L’export JSON contient l’instantané canonique complet et `garden_export_version:1`.
Le CSV est en UTF-8 avec BOM : une fiche/intervention par ligne, précision/valeur/
libellé distincts, identifiants et noms des cibles stables, quantités absentes laissées
vides plutôt qu’à zéro. C’est un export de consultation, pas un format de synchronisation.

Les ZIP portables contiennent `garden.ogp` et des médias nommés par leur empreinte.
Les fonds PNG sont déjà embarqués par OGP. La restauration refuse une destination
existante, les traversées de dossiers, liens symboliques, chemins dupliqués et archives
de plus de 2 000 entrées ou 1 Go décompressé. Les pièces jointes sont copiées dans
`<projet>.assets/` ; annuler une modification ne les supprime pas silencieusement.
Enregistrer sous et copier une saison pérenne conservent les références relatives
et copient les fichiers ; un contenu différent à destination est refusé. Un export
ZIP peut rapatrier d’anciens médias externes en copiant explicitement leurs fichiers.

Avant le premier enrichissement d’un fichier existant, l’enregistrement crée une
sauvegarde exclusive `.pre-jardin.ogp`, numérotée si nécessaire. La validation précède
toujours le remplacement de la scène et l’écriture atomique. Cette version conserve
aussi les clés principales et métadonnées inconnues lors d’un cycle ouverture/
enregistrement ; l’omission historique de `paper_layouts` reste volontaire. Cela
ne signifie pas que les futurs types d’objets ou formats inconnus sont pris en charge.

Cette version lit les anciens `.ogp`. La version amont 1.30.10 non modifiée ignore
et perd `garden_memory` lors d’un nouvel enregistrement : elle ne connaît ni les
interventions ni leurs reçus. Elle peut conserver certaines métadonnées opaques,
mais ne garantit pas la cohérence des dates partielles et exactes. Pour préserver
le suivi, conserver l’original `.pre-jardin`, utiliser cette version et ses sauvegardes
portables ; éviter les allers-retours avec la version amont.
