# Utiliser le suivi du jardin

## Partir d’un plan vide

Lancer `Lancer-Jardin.command`, choisir **File > New**, puis enregistrer le nouveau
plan dans `jardin-personnel/`. Dessiner ses limites et ajouter les plantes au fur
et à mesure. Les anciens fichiers restent disponibles ; nul besoin de les effacer.

## Essayer la démonstration (facultatif)

Pour découvrir le suivi, ouvrir `examples/jardin-demo.ogp` avec
**File > Open**. Ce fichier est entièrement fictif : un massif, un arbre, une
vivace, deux groupes de bulbes et cinq interventions. Le fond quadrillé est
artificiel (une cellule = un mètre), sans relation avec un jardin réel.
Enregistrer une copie dans `jardin-personnel/` avant vos propres essais.

Dessiner avec les outils habituels. Pour un massif, choisir le type Garden Bed
sur un contour existant ; pour une plantation ou un groupe, un cercle suffit.
Les coordonnées internes restent en centimètres. Le diamètre dessiné représente
l’emprise choisie ; les cercles de croissance future restent ceux du moteur OGP.
Aucun déplacement dessiné ne crée automatiquement une transplantation.

Sélectionner une forme, puis **Properties > Garden records…**. On peut aussi
ouvrir **Garden > Garden records…** et choisir une forme dans la liste à gauche,
y compris un groupe sous un arbre. Le nom et les notes sont les données
existantes de l’objet. Le nom « bulbes à identifier » convient. Choisir
Individual, Planting group ou Zone, l’état et éventuellement une quantité.
**Save card** constitue une seule opération annulable. La quantité reste facultative.

La liste indique aussi le **calque** de chaque objet. Dans **Calques affichés…**,
décocher par exemple « Fond » ou « Bâtiments » pour retirer leurs formes de la
liste et des choix de cibles pour les nouvelles interventions. **Afficher tous
les calques** permet de les retrouver. Ce filtre est mémorisé sur ce Mac par
identifiant de calque, y compris après réouverture du plan ; il reste indépendant
de la visibilité des calques sur le dessin et ne modifie pas le fichier `.ogp`.

Le journal général masque les interventions dont toutes les cibles appartiennent
à des calques décochés. Une intervention commune à une cible affichée reste visible,
ainsi que l’historique d’objets supprimés. La fiche d’une plantation conserve les
interventions héritées de sa zone, même si le calque de la zone est décoché.
Les exports restent complets ; aucune fiche ni intervention n’est supprimée.

Le champ de date accepte `2023-10-21`, `2023-10`, `Automne 2023`, `2023`, ou
une case vide pour une date inconnue. Le libellé et la précision sont conservés.
`Herbst 2023` et `autumn 2023` sont également acceptés. Une date partielle ne
devient jamais un jour exact. Le champ de date exact de l’ancienne fiche
botanique est en lecture seule pour les objets suivis : modifier la date dans
Garden records afin de garder une seule écriture cohérente.

Choisir une **Explicit zone** pour établir un lien durable. La proximité et le
parent OGP automatique ne valent pas cette association explicite. Depuis la
zone, le sélecteur **Plantings in this zone…** donne accès à ses plantations.
Renommer une forme conserve ses liens grâce à son identifiant. L’état Dormant
ne signifie pas Confirmed dead ; Removed or moved conserve la forme et son
historique. Une suppression définitive de la forme garde les cibles nommées
lisibles dans le journal global.

**Add intervention…** prépare la cible choisie. On peut cocher d’autres cibles,
indiquer une date connue ou partielle, un type, une description et Planned ou
Completed. Les matériaux, quantités, unités, surfaces et profondeurs sont
facultatifs. Les décimales françaises (`4,5`) sont acceptées. Une surface traitée
est une valeur historique saisie : agrandir le massif ne la recalcule pas.
Une intervention ne modifie aucune mesure de pH et ne convertit pas les litres
en kilogrammes. Un conseil ou un projet doit rester Planned.

Dans une plantation, les lignes préfixées **Zone:** viennent de sa zone ; elles
ne sont pas dupliquées. La première ligne à gauche ouvre le journal global.
Filtres : texte, type, cible et bornes de date. Les périodes sont recherchées
par recouvrement de limites calendaires conventionnelles ; les dates inconnues
restent visibles sans filtre de date. Les boutons Undo/Redo annulent/rétablissent
les modifications. Fermer ce dialogue ne remplace pas **File > Save**.

Enregistrer le projet avant d’importer une pièce jointe. Dans une intervention,
**Attachments…** importe, ouvre ou retire un lien vers un fichier. Les fichiers
sont copiés dans `<projet>.assets/` et référencés relativement au `.ogp`.
Des noms identiques ne s’écrasent pas. Retirer un lien, annuler ou supprimer une
intervention ne détruit pas les fichiers nécessaires à un rétablissement.

**Export…** fournit le JSON complet, le tableau CSV de consultation et une
sauvegarde ZIP portable (projet, fond embarqué et médias). **Restore backup…**
restaure dans un nouveau sous-dossier, sans remplacer le projet ouvert. Ouvrir
ensuite le `.ogp` restauré avec File > Open. Un Save As dans un autre dossier
copie aussi les médias relatifs ; pour d’anciens médias externes, privilégier le ZIP.

Au premier enregistrement enrichissant un ancien fichier, une copie
`<nom>.pre-jardin.ogp` est conservée (suffixe numéroté si nécessaire).
Conserver un seul `.ogp` de référence au fil des années. Après le premier
changement de suivi, une copie de saison conserve tous les objets, identifiants,
notes et interventions, même si l’option historique de retrait des plantes est
cochée. Les projets sans suivi gardent le comportement saisonnier amont.

L’application reste en anglais/allemand ; les libellés, notes, accents et dates
français fonctionnent. Une traduction intégrale de l’application en français
reste une amélioration distincte. Le propriétaire a choisi de construire son plan
à la main. Les fiches de plantes importées enrichissent la bibliothèque sans imposer
de positions, de dates de plantation ni de dessin automatique.

## Vérification sur le Mac après les tests automatiques

- Ouvrir le lanceur : textes lisibles, contrôles dans la fenêtre, aucun clic spontané.
- Ouvrir la démo, sélectionner un groupe sous l’arbre dans Garden records et
  modifier sa note ; Undo/Redo doit revenir puis rétablir la modification.
- Ajouter une observation sans date, enregistrer, fermer et rouvrir : date inconnue.
- Créer un ZIP, restaurer ailleurs et ouvrir la copie : fond et pièces jointes présents.
- Fermer l’application avant les tests automatisés. Après une modification sous
  `src/`, relancer suffit ; pas de reconstruction d’exécutable.


Les panneaux de droite s'ouvrent et se referment en cliquant sur leur titre ; un
simple passage de souris ne les ouvre plus. Une sélection sur le plan peut ouvrir
les panneaux pertinents. Les catégories en haut se replacent dans l'écran ; utiliser
leur défilement pour atteindre les derniers objets sur une petite surface d'affichage.

Dans une catégorie, Filter… est un champ de recherche : y saisir par exemple
`apple`. Les boutons de caractéristiques peuvent réduire la liste à zéro résultat.
Le message No matching objects l'indique ; Clear filters réaffiche tous les objets.
La réouverture d'une catégorie repart sans filtre de recherche ni caractéristique.


## Faire défiler les formulaires

Sur un menu déroulant fermé, la molette ou le pavé tactile fait défiler le formulaire,
sans modifier la valeur du menu, même après lui avoir donné le focus. Cliquer pour
ouvrir la liste puis sélectionner une valeur ; le clavier reste utilisable. La liste
ouverte garde son défilement. Cette protection concerne les menus déroulants dans
l’ensemble de l’application, pas les champs numériques ni le zoom du plan.

Les formulaires gardent leurs champs lisibles. Si nécessaire, utiliser les barres de
défilement verticale et horizontale. Les boutons de validation restent en bas ; une
longue rangée de boutons dispose aussi d’un défilement horizontal.
