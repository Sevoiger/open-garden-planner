# Pistes produit élargies — 31 octobre 2026

Ces idées sont des hypothèses de valeur, pas des fonctions livrées. Elles
partent des capacités existantes d’OGP et évitent les propositions de suivi
des tâches, de chronologie et de liste d’achats écartées par le propriétaire.

1. **Comparer des aménagements avant de choisir.** Besoin : hésiter entre deux
   implantations sans modifier le jardin de référence. Exemple : comparer une
   pergola près de la terrasse ou au fond du jardin. Valeur : voir ensemble les
   différences de surface, de circulation et d’ombre. Première version :
   ouvrir deux copies `.ogp` et superposer leurs objets en lecture seule, avec
   un tableau simple des éléments ajoutés, retirés ou déplacés. Dépendances :
   identifiants d’objet et superposition de saison existante, à étendre au-delà
   des plantes ; risque de comparer deux fichiers sans origine commune. Critère :
   deux scénarios synthétiques sont comparables sans changer les originaux et
   l’utilisateur peut expliquer son choix à partir de la vue.
2. **Concevoir des massifs par surface et espacement.** Besoin : prévoir
   rapidement un ensemble de vivaces plutôt que déposer chaque plante à la main.
   Exemple : tester un mélange de trois espèces sur un massif irrégulier de 12 m².
   Valeur : visualiser l’occupation et obtenir un nombre indicatif de plants.
   Première version : aperçu réversible d’une seule espèce dans un contour,
   espacement explicitement choisi, validation avant création. Dépendances :
   géométrie du contour, objets et commandes existants ; risques : contours
   complexes, recouvrements et fausse précision sur la croissance réelle.
   Critère : l’aperçu reste dans des contours représentatifs, le nombre est
   compréhensible et toute l’insertion s’annule en une étape.
3. **Dessiner les cheminements et vérifier l’accès.** Besoin : savoir si une
   brouette ou une personne passe entre les massifs et accède à une réserve d’eau.
   Exemple : matérialiser un passage libre de 80 cm jusqu’au composteur. Valeur :
   éviter une implantation jolie mais difficile à entretenir. Première version :
   tracer un trajet et signaler les intersections avec des obstacles visibles,
   pour une largeur saisie. Dépendances : géométries et calques existants ;
   risques : feuillages évolutifs, pentes et objets manquants, à signaler comme
   limites de l’analyse. Critère : détecter les passages volontairement bloqués
   d’un plan test sans masquer les cas dont les dimensions sont inconnues.
4. **Comprendre l’eau sur le terrain.** Besoin : réfléchir à la récupération et
   au cheminement de l’eau sans transformer une météo en ordre d’arrosage.
   Exemple : dessiner une toiture de 25 m² et comparer la capacité d’une cuve à
   une pluie saisie de 10 mm. Valeur : rendre visibles volumes et hypothèses.
   Première version : calcul local surface × pluie avec rendement et capacité
   explicitement fournis ; affichage du volume théorique et de ses limites.
   Dépendances : surfaces du plan et objet cuve existants ; risques : pertes,
   débordement et données météo manquantes. Critère : retrouver les calculs à
   la main sur plusieurs surfaces, y compris les valeurs inconnues et nulles.
5. **Relever un jardin à partir d’une photo ou d’un croquis.** Besoin : démarrer
   avec une image imparfaite sans redessiner tout le terrain à l’aveugle.
   Exemple : importer un croquis coté, définir deux longueurs connues, puis
   vérifier l’écart sur une troisième. Valeur : préparer un fond exploitable et
   repérer les erreurs d’échelle avant de tracer. Première version : contrôle de
   calibration sur un fond existant, avec résidus affichés et correction manuelle.
   Dépendances : image de fond et points de référence ; risque : perspective,
   photo non plane et précision excessive. Critère : sur des images synthétiques
   calibrées et déformées, signaler les écarts sans modifier le plan original.
