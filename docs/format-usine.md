# Le format d'une usine SatisPlanner

Ce document décrit le fichier que l'application écrit et relit, pour qui veut en
fabriquer un à la main, en générer par script, ou simplement comprendre ce qu'il y
a dans un `.sfp`.

L'exemple complet et fonctionnel vit dans [`exemple-usine.json`](exemple-usine.json)
— il est chargé par la suite de tests, donc il ne peut pas se périmer en silence.
Ce fichier-ci le commente.

---

## 1. Le contenant : le `.sfp`

Un `.sfp` est une **archive ZIP** contenant jusqu'à trois membres :

| Membre | Rôle | Obligatoire |
| --- | --- | --- |
| `factory.json` | le graphe : les nœuds et les lignes | oui |
| `manifest.json` | qui a écrit le fichier, quand, contre quelles données de jeu, sous quel schéma | non, mais son absence fait supposer le schéma courant |
| `thumbnail.png` | une image du canvas, pour l'explorateur de fichiers | non |

`manifest.json` :

```json
{
  "application_version": "1.1.0",
  "game_version": "1.2",
  "schema_version": 4,
  "saved_at": "2026-07-26T18:42:11+00:00"
}
```

`schema_version` est le seul champ qui change quelque chose à la lecture : il dit
par quelles conversions le document doit passer. Un fichier annonçant une version
**supérieure** à celle que connaît la build est refusé avec une phrase, jamais
ouvert de travers.

Il existe aussi un **code de partage**, qui transporte exactement le même
`factory.json` sous forme de texte : le préfixe `SFP1:` suivi du JSON compressé et
encodé en base64. C'est ce que copient et collent les commandes de partage.

---

## 2. Le contenu : `factory.json`

```json
{
  "schema_version": 4,
  "nodes": [ ... ],
  "edges": [ ... ]
}
```

Rien d'autre : un champ inconnu est **refusé**, pas ignoré. C'est délibéré — une
faute de frappe dans un nom de champ doit se voir tout de suite et non se traduire
par une valeur par défaut silencieuse.

### Ce que tout nœud possède

| Champ | Type | Défaut | Sens |
| --- | --- | --- | --- |
| `kind` | chaîne | — | le type de nœud ; il décide des autres champs |
| `id` | chaîne | — | unique dans le fichier ; c'est lui que les lignes citent |
| `label` | chaîne ou `null` | `null` | le nom affiché ; sans lui, le nom français de l'objet ou de la recette |
| `position` | `[x, y]` | `[0, 0]` | en pixels de la scène, alignée sur une grille de 20 |
| `show_deployed` | `true`, `false` ou `null` | `null` | dessiner une vignette par machine bâtie ; `null` signifie « suivre la préférence globale ». Purement visuel |

`position` et `show_deployed` ne changent **aucun débit**. Elles sont dans le
document parce qu'elles décrivent *ce* nœud dans *cette* usine, et qu'un utilisateur
qui a rangé son usine s'attend à la retrouver rangée.

---

## 3. Les neuf types de nœuds

### `resource` — un gisement

```json
{
  "kind": "resource",
  "id": "gisement1",
  "label": "Gisement de fer nord",
  "position": [-640.0, -160.0],
  "item_class": "Desc_OreIron_C",
  "extractor_class": "Build_MinerMk2_C",
  "purity": "pure",
  "count": 1.0,
  "clock_speed": 1.0
}
```

`purity` vaut `"impure"`, `"normal"` ou `"pure"` et multiplie le débit de base par
0,5, 1 ou 2. Elle s'applique à **tous** les extracteurs du nœud : un nœud *est* un
gisement. Deux gisements de puretés différentes sont deux nœuds.

`count` est le nombre d'extracteurs, strictement positif, décimales admises.
`clock_speed` est une fraction — `1.0` vaut 100 %, `2.5` vaut 250 % — bornée entre
0,01 et 2,5. Le débit lui est strictement proportionnel ; l'électricité, non.

Débit produit = `débit de base × pureté × count × clock_speed`.
Ici : 120 × 2 × 1 × 1 = **240 minerai/min**.

### `water_extractor` — une pompe à eau

```json
{
  "kind": "water_extractor",
  "id": "pompe1",
  "position": [-640.0, 240.0],
  "extractor_class": "Build_WaterPump_C",
  "count": 1.0,
  "clock_speed": 0.75
}
```

Pas de `purity` : l'eau n'a pas de gisement. Pas de `item_class` non plus — ce que
la pompe extrait est déclaré par le bâtiment lui-même dans les données du jeu.
Ici : 120 × 1 × 0,75 = **90 m³/min**.

### `external_source` — un apport venu d'ailleurs

```json
{
  "kind": "external_source",
  "id": "entree1",
  "label": "Charbon importe",
  "position": [-640.0, 560.0],
  "item_class": "Desc_Coal_C",
  "rate_per_minute": 30.0
}
```

Le débit est celui que vous annoncez, point. C'est ce qu'on utilise pour modéliser
un morceau d'usine sans redessiner tout ce qui l'alimente.

### `machine` — une machine de production

```json
{
  "kind": "machine",
  "id": "machine1",
  "position": [-240.0, -160.0],
  "recipe_class": "Recipe_IngotIron_C",
  "machine_count": 8.0,
  "clock_speed": 1.0
}
```

`machine_count` est une **entrée**, pas un résultat : vous dites combien de machines
sont bâties, décimales admises, et le moteur vous répond combien sont réellement
utiles. Le bâtiment se déduit de la recette ; le champ facultatif `building_class`
n'existe que pour qu'une incohérence soit diagnostiquée plutôt qu'ignorée.

Attention à une règle qui surprend : **un sous-produit sans issue arrête la machine
entièrement**. Si une recette produit deux choses, les deux doivent avoir une ligne
sortante, sinon le nœud tourne à 0 %.

### `generator` — une centrale

```json
{
  "kind": "generator",
  "id": "generateur1",
  "position": [-240.0, 400.0],
  "generator_class": "Build_GeneratorCoal_C",
  "fuel_class": "Desc_Coal_C",
  "count": 2.0,
  "show_deployed": true
}
```

`fuel_class` doit être un carburant que ce bâtiment accepte : un générateur à
carburant tourne au carburant *ou* au turbocarburant, et les deux n'ont pas du tout
le même appétit. Le carburant **et** l'eau d'appoint sont des entrées ordinaires,
sur des lignes ordinaires, soumises aux mêmes capacités que le reste.

Il n'y a délibérément **pas** de `clock_speed` ici : le jeu élève la production d'un
générateur à un exposant qui lui est propre, différent de celui qui facture une
machine surcadencée, et modéliser l'un avec l'autre inventerait des chiffres.

Un générateur n'a **aucune sortie** : ce qu'il produit est du courant, et le courant
ne circule pas sur une ligne.

### `storage` — un tampon

```json
{
  "kind": "storage",
  "id": "tampon1",
  "position": [160.0, 160.0],
  "storage_class": "Build_StorageContainerMk2_C",
  "item_class": "Desc_IronIngot_C",
  "initial_content": 0.0
}
```

`item_class` peut valoir `null` : le contenu est alors **déduit** de la seule ligne
entrante, et reste indéterminé s'il en arrive deux différentes.

Un tampon est un puits et une source infinis. L'application dit si les débits sont
tenables et en combien de temps un stock se vide, mais **ne simule pas le temps qui
passe**.

### `output` — une sortie d'usine

```json
{
  "kind": "output",
  "id": "sortie1",
  "position": [560.0, -160.0],
  "item_class": "Desc_IronIngot_C",
  "is_sink": false
}
```

`is_sink` à `true` marque un **rejet assumé** : torchère, collecteur AWESOME. Les
deux absorbent sans limite ; le drapeau ne change que la ligne du rapport où le flux
est compté — `final_outputs` ou `discarded_outputs`.

### `splitter` et `merger` — les raccords

```json
{
  "kind": "splitter",
  "id": "repartiteur1",
  "position": [400.0, 0.0],
  "item_class": null
}
```

Un répartiteur prend **une ligne** et en ressort jusqu'à **trois** ; un groupeur fait
l'inverse. Ils ne portent ni cadence, ni quantité, ni classe de bâtiment : lequel des
trois bâtiments du jeu c'est se déduit de la forme de ce qui passe — répartiteur de
convoyeurs et groupeur de convoyeurs pour un solide, jonction de pipeline pour un
fluide, parce qu'elle a quatre ports et fait les deux métiers.

`item_class` peut rester `null` : il se lit alors sur les lignes, comme le contenu
d'un tampon. Toutes les lignes d'un raccord portent le même objet — c'est ce qu'est
un convoyeur.

Un raccord **ne garde rien** : ce qui entre ressort. Il n'a pas de débit nominal et
il ne bride jamais rien, parce que dans le jeu il déplace plus que le convoyeur le
plus rapide. Un répartiteur partage **également entre ses branches raccordées**,
avec la règle max-min habituelle : ce qu'une branche ne peut pas prendre revient aux
autres. Un répartiteur qui ne mène nulle part bloque tout ce qui l'alimente, comme
un sous-produit sans issue.

---

## 4. Les lignes

```json
{
  "id": "e1",
  "source": "gisement1",
  "target": "machine1",
  "item_class": "Desc_OreIron_C",
  "transport_class": "Build_ConveyorBeltMk3_C"
}
```

Une ligne porte **un objet** et **un transport**. Les règles, vérifiées à la
construction et non découvertes par le solveur :

- un solide voyage sur un convoyeur, un fluide dans une tuyauterie, jamais l'inverse ;
- `source` doit produire cet objet, `target` doit le consommer ;
- une machine a au plus **4 entrées** et **2 sorties** distinctes en *objets* ;
- **un port porte une ligne**, et un nœud a autant de ports qu'il a de bâtiments :
  `ceil(count)` par objet et par sens. Huit fonderies ont huit sorties, une seule en
  a une. Un gisement, une pompe, une machine et un générateur suivent tous cette
  règle ; un tampon n'a pas de compte, donc il a un port de chaque côté ; une entrée
  et une sortie d'usine sont la frontière du modèle et pas des bâtiments, donc elles
  n'ont pas de limite ;
- une ligne ne boucle pas sur son propre nœud ;
- les identifiants de lignes sont uniques.

La capacité du tier est une **contrainte, pas une remarque** : un Mk.1 alimenté à
480/min en transporte 60 et refoule le reste en amont.

**Une ligne de trop se raccorde par un répartiteur, qui est un nœud comme un
autre.** Dans l'exemple les 240 lingots partent en trois lignes, et il n'y a aucun
raccord : le nœud compte huit fonderies, donc huit sorties, et trois lignes y
tiennent largement. Il en faudrait un si la même usine était dessinée avec deux
fonderies.

Un fichier écrit avant le schéma 5 est converti à l'ouverture : les raccords que sa
disposition supposait sont matérialisés, les lignes reprises à travers eux. Un
partage qui se ramène à des moitiés et des tiers — 2, 3, 4, 6, 9 lignes — donne
exactement les mêmes débits qu'avant ; un partage en 5 ou en 7 n'est pas égal dans
le jeu et ne l'est plus ici non plus, et la conversion dit lesquels, avec l'ancienne
et la nouvelle part.

---

## 5. Ce que l'exemple donne

Résolu contre les données du jeu 1.2 :

| | |
| --- | --- |
| Minerai consommé | 240 minerai de fer/min, 30 charbon/min |
| Fluides | 90 m³ d'eau/min |
| Sorties | 80 lingots/min |
| Rejets assumés | 80 lingots/min |
| Électricité | 60,673 MW consommés, 150 MW produits |
| Liste de courses | 8 fonderies, 1 Foreuse Mk.2, 1 pompe, 2 centrales, 1 conteneur, aucun raccord |
| Diagnostic | tampon en remplissage, +80/min, saturé en 1 h |

L'électricité est un **compteur, pas une contrainte** : consommation et production
sont affichées côte à côte, et un déficit ne bride aucun débit.

---

## 6. Écrire un fichier à la main

1. Écrivez le `factory.json` ;
2. zippez-le seul sous le nom `factory.json` et donnez à l'archive l'extension
   `.sfp` — le manifeste et la vignette sont facultatifs à la lecture ;
3. ouvrez-le dans l'application.

Les noms de classes (`Desc_*`, `Build_*`, `Recipe_*`) sont ceux du jeu. Ceux que
la base embarquée ne connaît pas font tomber le nœud concerné, avec un avertissement
qui les nomme et un document marqué comme **partiel** — l'application demande alors
confirmation avant de réécrire par-dessus le fichier d'origine.
