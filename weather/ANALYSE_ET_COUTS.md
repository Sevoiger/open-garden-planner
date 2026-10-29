# Analyse locale, choix du modèle et coût indicatif

Vérification de documentation officielle OpenAI le **29 octobre 2026**.
Les tableaux de coûts ci-dessous restent des simulations. Des essais API à la
demande ont aussi été réalisés ; leurs tokens et bulletins sont conservés dans
`.local/weather/briollay/analyses/`. Un coût calculé depuis ces tokens ne constitue
pas une facture mesurée.

**Choix retenu pour l’onglet OGP : GPT-6.1 Sol, raisonnement `high`, analyse
occasionnelle à la demande, images aux dimensions d'origine.** Aucune réduction
ni analyse automatique n'est activée. Le gain financier d'un redimensionnement
paraît faible pour cet usage face au risque de rendre les légendes illisibles.

## Que font les cartes ?

Les cartes restent archivées et apportent le contexte régional : position et
évolution des zones pluvieuses, gradients thermiques et nuages. Les chiffres d'un
point de grille ne remplacent pas toute cette information spatiale. Les cartes et
séries peuvent aussi différer par leur maille ou leur run.

Pour réduire l'entrée du modèle, chaque nouveau run fournit `analysis-input.json`,
référencé dans `latest.json` par `analysis_input.path`. Il est calculé localement :
résumés journaliers déterministes, vent/rafales et statistiques journalières des
ensembles. Chaque membre est agrégé AVANT les quantiles ; seules les journées
complètes contribuent aux distributions journalières. Les effectifs restent visibles.
Il conserve l'état des cartes, sans prétendre en avoir interprété les pixels.
Sur l'archive vérifiée, il représente environ 43 000 caractères en JSON compact,
contre 758 000 pour le manifeste compact avec les membres bruts. Ce ne sont pas
des comptes de tokens. La lecture visuelle régionale peut être ajoutée avec les PNG.

Une extraction couleur→valeur locale est faisable : la pipette de Meteociel
[utilise elle-même une palette `keycolors`](https://modeles7.meteociel.fr/include/pickvalue.js?v2)
et renvoie une plage de valeurs. Elle exige cependant une correspondance validée
coordonnées géographiques→pixels pour chaque domaine et résolution. Traits, texte,
anti-aliasing, couleurs proches, blanc et valeurs hors palette doivent produire un
état masqué/inconnu, jamais un zéro automatique. Ce décodage géographique n'est pas
implémenté ; ne pas présenter les données d'Open-Meteo comme une extraction des cartes.
Les originaux restent la référence ; aucune extrapolation météo locale n'est ajoutée.

## Modèles et raisonnement

| Modèle API | Entrée / million de tokens | Sortie / million | Usage proposé ici |
| --- | ---: | ---: | --- |
| `gpt-6-luna` | 0,10 $ | 0,50 $ | Tester en `medium` pour le bulletin quotidien cadré et peu coûteux |
| `gpt-6.1-terra` | 1 $ | 5 $ | `high` retenu pour lecture visuelle et rédaction |
| `gpt-6.1-sol` | 2 $ | 10 $ | `medium` pour synthèse plus complexe ; `high` seulement si une comparaison réelle montre un gain |

Les trois prennent des images en entrée. Terra et Luna supportent `none`, `low`,
`medium`, `high`, `xhigh`, `max` ; Sol commence à `low`. Le prix unitaire ne change
pas avec l'effort, mais le nombre de tokens consommés peut augmenter. Le
raisonnement est facturé dans les tokens de sortie, même s'il n'est pas visible.
Ces usages sont des recommandations pour ce dossier, pas un benchmark météo.

Exemple de paramètres d'analyse, sans activer un appel :

```json
{"model":"gpt-6.1-sol","reasoning":{"effort":"high"}}
```

Le prompt est dans `analyse-prompt.md`. Il demande un bulletin sans longueur imposée,
centré sur les événements, leurs dates et les conséquences météo pour le jardin.
Le lanceur local omet `max_output_tokens` : aucun plafond supplémentaire n'est
ajouté par l'outil, dans les limites propres au modèle et à son contexte.
Les contrôles de provenance et de couverture restent des règles de travail : ils
ne sont exposés que si un défaut change concrètement la conclusion. Aucun bloc
générique « Limites », audit des runs ou rappel systématique sur le sol.

Pour un bulletin numérique économique,
fournir le petit JSON ; pour une analyse spatiale des cartes, fournir également
les images souhaitées. Ne jamais affirmer avoir lu une carte non fournie.

## Taille actuelle contre réduction des pixels

Archive mesurée : **82 PNG 768×768 et 16 PNG 768×512**, soit 98 cartes et
**54 657 024 pixels**. Réductions proportionnelles, sans agrandir les images.
Les PNG originaux ne sont pas modifiés par l'estimation.

**Limite du chiffrage** : le calculateur et la table de tokenisation consultés
référencent Astra et les familles 5.6, mais pas explicitement les trois IDs
ci-dessus. Les montants suivants utilisent donc l'hypothèse documentée pour ces
familles : `ceil(largeur/32) × ceil(hauteur/32) × 1,2`, arrondie par image. Le
multiplicateur 1,2 reste **à confirmer pour les IDs 6/6.1 demandés**, via les tokens
réels de l'API ou une documentation actualisée. Le détail est supposé conserver
la résolution envoyée ; un redimensionnement automatique de l'API changerait le résultat.

| Côté maximal envoyé | Total pixels | Tokens visuels supposés | Sol / 98 cartes | Terra / 98 cartes | Luna / 98 cartes |
| --- | ---: | ---: | ---: | ---: | ---: |
| Actuel, 768 px | 54,66 M | 64 120 | 0,1282 $ | 0,0641 $ | 0,00641 $ |
| 640 px | 37,96 M | 44 736 | 0,0895 $ | 0,0447 $ | 0,00447 $ |
| 512 px | 24,29 M | 28 648 | 0,0573 $ | 0,0286 $ | 0,00286 $ |
| 384 px | 13,66 M | 16 042 | 0,0321 $ | 0,0160 $ | 0,00160 $ |

Entrée des images uniquement, USD Standard hors taxes, sans cache, traitement
régional, outils, texte ou sortie. Réduire à 512 px enlève environ 56 % des pixels
et des tokens supposés. Compresser le fichier sans changer les pixels ne produit
pas ce gain. Les petits caractères des légendes deviennent difficiles à lire ;
le rendu réduit a été inspecté localement, sans test de précision par un modèle.
Un recadrage local peut également supprimer le contexte régional utile.

À 30 collectes/mois, images seules en taille actuelle : environ **3,85 $ Sol,
1,92 $ Terra, 0,19 $ Luna**, toujours sous cette hypothèse. En 512 px : environ
**1,72 $, 0,86 $, 0,086 $**. Le changement de modèle pèse davantage que la réduction.

Scénario supplémentaire explicite : 5 000 tokens de texte d'entrée et 5 000 tokens
de sortie totale, raisonnement inclus, ajoutent **0,06 $ Sol / 0,03 $ Terra /
0,003 $ Luna** par bulletin. Ce volume est une hypothèse de budget, pas une mesure
ni une conséquence fixe du niveau de raisonnement. Les outils et itérations s'ajoutent.

Reproduire localement, sans upload ni requête API :

```sh
.venv/bin/python scripts/estimate_weather_image_cost.py --archive .local/weather/briollay
```

Le paramètre `--multiplier` permet de corriger le calcul lorsque la valeur exacte
sera confirmée. Il s'agit d'une simulation pour ces petites images, sans simuler
les redimensionnements internes de l'API ni les limites des grands prompts.

Sources officielles : [Sol](https://developers.openai.com/api/docs/models/gpt-6.1-sol),
[Terra](https://developers.openai.com/api/docs/models/gpt-6.1-terra),
[Luna](https://developers.openai.com/api/docs/models/gpt-6-luna),
[raisonnement et facturation](https://developers.openai.com/api/docs/guides/reasoning),
[vision et tokenisation](https://developers.openai.com/api/docs/guides/images-vision),
[calculateur](https://developers.openai.com/api/docs/guides/image-cost-calculator).
