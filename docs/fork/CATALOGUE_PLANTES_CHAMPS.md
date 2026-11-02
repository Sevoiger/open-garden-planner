# Catalogue de plantes : données et validation

## Interface livrée

L'onglet catalogue utilise une carte par fiche botanique, un aperçu à la sélection,
et une fiche complète commune au bouton, à Entrée et au double-clic. Les bandes
de floraison et taille restent compactes sur la carte ; la plantation reste dans
la fiche complète. La fiche répartit les champs entre
présentation, culture/calendrier, exemplaires, médias et sources ; les champs
spécialisés et les calendriers supplémentaires sont repliables.

La bibliothèque reste `PlantLibrary`. Les exemplaires sans dessin sont conservés
dans la clé additive `plant_specimens` du projet courant ; ils partagent les mêmes
métadonnées et le seul `garden_memory` que les objets placés. Aucun dessin invisible,
second stockage de mesures ou second historique d'interventions n'est créé.

Le premier enrichissement d'une fiche sélectionnée complète ses inconnues et
protège ses valeurs présentes. Le lien fournisseur est distinct de son UUID
personnel ; les actualisations et contrôles de doublon utilisent cette identité
externe exacte. Les anciens payloads et les corrections restent conservés. Les
photos de référence se récupèrent explicitement depuis la source, en worker,
puis s'associent avec contrôle de révision ; ouvrir une fiche reste hors ligne.

## Matrice champ existant / adaptation / champ nouveau

| Domaine | Existant réutilisé | Adaptation / nouveau champ structuré |
| --- | --- | --- |
| Identité | `PlantSpeciesData` : noms, famille, genre, source et identifiant ; `PlantLibrary` : identité et transactions | `raw_data.botanical_profile` : espèce, cultivar, nom commercial, autres noms, synonymes, catégorie, étiquettes ; résumé distinct de la description détaillée existante |
| Dimensions | Fourchettes hauteur/envergure adultes, croissance et couleurs | Saisie des bornes facultatives ; port, persistance et caractéristiques du feuillage/fleurs, parfum, fruit, intérêt saisonnier, espacement avec unité/contexte dans des champs séparés |
| Conditions | Exposition, eau, sol, pH et zones de rusticité | Expositions possibles, drainage, richesse, humidité, pH qualitatif, rusticité sourcée en texte, climat, sécheresse/excès d'eau, pot, usages/support, plantation/paillage/entretien/taille/hivernage ; aucune conversion température-zone |
| Particularités | Données fournisseur originales dans `raw_data` | Pollinisation, pollinisateurs, multiplication, usages comestibles, précautions et source/degré de certitude séparés. Une absence reste inconnue |
| Calendriers | Semis intérieur/direct, transplantation et récolte en semaines relatives aux gelées | `botanical_calendar` : plantation, floraison, taille, plantation optimale, récolte/fructification, semis, multiplication ; chaque période porte mois facultatifs, texte source et indicateur de correction. Plages disjointes et hiver conservés ; climat dans son champ propre |
| Relations | Bibliothèque et listes personnelles existantes | Drapeaux non exclusifs `catalog_relations` dans la fiche ; relation personnelle indépendante dans l'exemplaire. Pas de nouvelle bibliothèque ni copie des listes |
| Exemplaire | `PlantInstance`, UUID, fiche de suivi, état, zone et date/période de plantation | Lien exact `library_plant_id`, surnom, mode de culture, emplacement textuel, acquisition/date partielle/provenance/prix payé/devise/justificatif, contenant/substrat/notes, ancien numéro/alias/NFC. Placement transfère le même UUID et ses métadonnées |
| Suivi | Mesures, observations, interventions, déplacement et historique `garden_memory` | Accès aux mêmes formulaires pour exemplaire placé ou non ; rempotage comme observation explicite, sans nouveau type incompatible |
| Médias | `garden_archive.import_attachment`, chemins relatifs et archive portable | Originaux et miniatures par UUID, catégorie référence/personnelle/illustration/document, légende, crédit/source, dates de prise de vue et import distinctes ; photo principale et comparaison ; aucun effacement lors d'un retrait d'association |
| Provenance | `data_source`, `source_id`, document source et champs inconnus | URI du fichier choisi, empreinte, récupération/vérification datées, baseline et historique des sources, différences protégées à l'actualisation ; corrections horticoles distinctes du document source |
| Impression | `PdfReportService`, moteur Qt PDF | Fiche/sélection/version personnelle, choix des photos par couple UUID/média, images locales uniquement, périodes et provenance ; export atomique et protection des originaux présents dans bibliothèque et instantanés |

Les champs spécialisés sont facultatifs. Le format `.ogp` conserve ses champs
inconnus et sa version ; les nouvelles clés sont additives. Une ancienne version
ne présente pas les exemplaires sans dessin mais conserve les extensions lors de
sa sauvegarde. Les commandes Qt réutilisent le gestionnaire undo/redo. Le service
de suivi partagé et ses requêtes incluent les exemplaires non placés sans modifier
l'authentification ni le contrat MCP existant.

## Preuves des neuf scénarios

Tous les essais de mutation utilisent des fichiers et réglages temporaires.

| Scénario demandé | Preuve reproductible |
| --- | --- |
| Deux exemplaires, pot et terre, photos distinctes | `test_plant_specimens.py`, `test_plant_media.py` ; PDF illustré et capture native des exemplaires |
| Plante recherchée sans exemplaire ni forme | `test_plant_catalog_filters.py` (unit/UI) ; fixture native « Églantier recherché » |
| Fiche manuelle avec inconnues | `test_plant_catalog_view.py`, `test_plant_sheet_dialog.py` ; carte « Plante à identifier » et dernière page du PDF |
| Import fixture Promesse + JSON local | `test_local_plant_sources.py`, `test_local_plant_import.py` ; fichier unique/dossier, aperçu éditable et annulation sans écriture |
| Rafraîchissement préservant correction | `test_plant_source_import.py`, `test_plant_source_worker.py` ; baseline trois voies, traits et calendriers manuels, aucun changement aux exemplaires |
| Liaison d'une plante existante sans duplication/perte | `test_apply_database_species.py`, `test_library_editor_conflict.py` ; lien exact, géométrie, UUID, mesures et undo/redo, réouverture dans `test_specimen_placement.py` |
| Sauvegarde/réouverture et photos | `test_specimen_placement.py`, `test_plant_media.py`, `test_botanical_media_scopes.py` ; Save As et archive globale placés/non placés, références retirées de la fiche mais retenues dans instantané |
| PDF vérifié visuellement | `test_botanical_catalog_pdf.py`, `test_plant_catalog_export_dialog.py` ; `scripts/verify_plant_catalog_pdf.py` produit six pages françaises relues après rendu Poppler : longs textes, accents, calendriers, photos distinctes, inconnu |
| Erreur réseau/disque sans perte | `test_plant_source_import.py`, `test_plant_media.py`, `test_botanical_catalog_pdf.py` ; timeout/refus/robots/redirection, original inchangé, ancien PDF préservé et destinations protégées |

Validation consolidée : 305 tests de la frontière affectée passent, puis quatre
régressions supplémentaires des libellés HTML spécialisés passent avec les 21 tests
de l'adaptateur local. Ruff sur `src/ tests/ scripts/` passe. Les trois catalogues
EN/FR/DE sont synchronisés et compilés (3 618 clés), puis vérifiés par la gate.
La revue indépendante du diff a conduit à corriger et revalider la protection PDF
des anciens instantanés, la lecture EXIF standard et les redirections réseau.
Une seconde revue ciblée valide la protection persistante des corrections lorsque
le fournisseur rejoint leur valeur puis la change de nouveau (12 tests du service).
Les 36 contrôles supplémentaires d'architecture, sérialisation et transfert de
workspace passent, ainsi que le test MCP de suivi avec authentification, rejeu et
réouverture. Le test MCP et la QA Cocoa ont nécessité l'accès natif hors du bac à
sable, sans utiliser les données personnelles.

La QA native Cocoa utilise `scripts/verify_plant_catalog_ui.py` avec données et
réglages isolés ; les captures sont dans `tmp/catalogue-ui-qa/`. Le PDF synthétique
est `output/pdf/catalogue-exemple.pdf`. Ces artefacts ne contiennent pas de données
personnelles et ne sont pas intégrés au catalogue réel.

## Limites explicites et extensions

La passe UI du 2 novembre allège l'aperçu et les cartes (floraison/taille,
champs renseignés, résumé court, photo plus grande et filtres repliables).
Affichage > Onglets conserve l'ordre et la visibilité par identifiants stables ;
les titres Calendrier et Semences sont raccourcis. Dix captures synthétiques
headless sont reproductibles dans `tmp/catalogue-ui-v2/`, dont le catalogue à
1000 × 720 et les neuf onglets à 1100 pixels de large.

Validation de cette passe : 177 tests ciblés réussis, un cas 3D natif ignoré et
un cas MCP HTTP omis après refus de bind localhost dans le sandbox. Les 50
contrôles supplémentaires du thème, du dernier correctif photo et de la largeur
des onglets passent également. Ruff et les traductions EN/FR/DE compilées passent
(3 637 clés actives). La revue indépendante a revalidé la protection des traits
anciens, le dédoublonnage d'une fiche enrichie, l'ordre persistant et la saisie
photo d'une fiche locale non fournisseur. Aucun test ni capture de cette passe
n'a ouvert de fenêtre native sur l'ordinateur du propriétaire.

- Le premier adaptateur URL est Promesse de Fleurs. Les autres fournisseurs
  utilisent une future correspondance dédiée ; aucun script arbitraire depuis l'UI.
- Les redirections vers une autre URL sont refusées avant leur requête ; certains
  sites ou hébergeurs d'images nécessitent donc le fichier local. Aucun contournement.
- Les photos personnelles et les exemplaires appartiennent au projet courant.
  Les références botaniques appartiennent à la bibliothèque ; pour transporter
  les deux ensembles, utiliser l'archive globale avec les données personnelles.
- Le dossier photo configurable reste dans le dossier du projet pour préserver
  les chemins relatifs. Aucun scan ni réorganisation automatique de Dropbox.
- La requête URL réelle du fournisseur n'est pas une condition des essais :
  extraction, restrictions et échecs sont vérifiés sur fixtures, pour rester
  reproductibles et ne pas déclencher de téléchargement personnel.
- Aucun push, PR, merge ni publication n'est réalisé sans instruction explicite.
