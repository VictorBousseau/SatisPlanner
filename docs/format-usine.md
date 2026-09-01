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
  "application_version": "5.0.0",
  "game_version": "1.2",
  "schema_version": 7,
  "saved_at": "2026-08-02T18:42:11+00:00"
}
```

`schema_version` est le seul champ qui change quelque chose à la lecture : il dit
par quelles conversions le document doit passer. Un fichier annonçant une version
**supérieure** à celle que connaît la build est refusé avec une phrase, jamais
ouvert de travers.

Le schéma courant est le **9**. Les versions successives, et ce que chacune a
ajouté :

| Schéma | Ce qui change | Conversion à l'ouverture |
| --- | --- | --- |
| 1 | l'origine | — |
| 2 | la cadence des machines et des extracteurs | rien à faire : la cadence prend 100 % |
| 3 | le nœud `generator` apparaît | rien à faire : un document d'avant n'en a pas |
| 4 | `show_deployed` par nœud | rien à faire |
| 5 | les répartiteurs et les groupeurs deviennent des nœuds | rien à faire **depuis le schéma 7** — voir ci-dessous |
| 6 | le mode d'un répartiteur et les filtres de ses branches | rien à faire : tout répartiteur ancien est un `standard` |
| 7 | `attachment_mode` : la règle des ports devient celle du document | le mode est lu sur le contenu, rien n'est réécrit |
| 8 | le nœud `resource_well` apparaît | rien à faire : un document d'avant n'en a pas |
| 9 | le nœud `geothermal` apparaît | rien à faire : un document d'avant n'en a pas |

Un numéro est incrémenté même quand il n'y a rien à convertir. C'est le seul moyen
qu'une build ancienne refuse un fichier récent par une phrase plutôt qu'en ignorant
en silence un champ qu'elle ne connaît pas — un répartiteur en mode « surplus » lu
par une build du schéma 5 afficherait des chiffres faux sans un mot.

**Le passage du 4 au 5 a changé de sens, et c'est le seul revirement de cette
liste.** Pendant une version il insérait l'arbre de répartiteurs qu'un port à trois
lignes supposait, parce que la règle était celle de la build et que tout document
devait s'y plier. Depuis le schéma 7 la règle est celle du document : un fichier
antérieur garde la forme dans laquelle il a été dessiné et s'ouvre en mode
**simple**, qui est le mode sous lequel il a été écrit. Le convertir changerait ses
chiffres pour répondre à une question que son auteur n'a jamais posée.

La matérialisation n'a pas disparu pour autant : c'est ce que fait la bascule vers
le mode fidèle, à la demande, avec le même rapport port par port.

Il existe aussi un **code de partage**, qui transporte exactement le même
`factory.json` sous forme de texte : le préfixe `SFP1:` suivi du JSON compressé et
encodé en base64. C'est ce que copient et collent les commandes de partage.

---

## 2. Le contenu : `factory.json`

```json
{
  "schema_version": 7,
  "attachment_mode": "simple",
  "nodes": [ ... ],
  "edges": [ ... ]
}
```

`attachment_mode` vaut `"simple"` ou `"faithful"`, et c'est le seul champ du
document qui décide d'une **règle** plutôt que d'une valeur.

| | `simple` (défaut) | `faithful` |
| --- | --- | --- |
| Lignes sur un port | autant qu'on veut | une par bâtiment, `ceil(count)` |
| Partage | max-min, au port | max-min, à travers l'arbre de raccords |
| Raccords | **déduits** des lignes | **comptés** là où ils sont posés |
| À quoi ça sert | réfléchir aux débits | construire |

Il est dans le document et non dans les préférences parce qu'il change les
résultats : une usine partagée doit s'ouvrir chez le destinataire dans le mode où
elle a été pensée. Une couleur d'objet, elle, n'a aucune raison de voyager.

**Les deux comptes de raccords diffèrent, et ce n'est pas une incohérence.** En
simple, le compte est ce que le dessin *implique*, calculé port par port dans le
chaînage le plus économe. En fidèle, c'est ce que quelqu'un a réellement posé — et
une usine dessinée à la main en utilise souvent davantage : un arbre bâti pour la
symétrie, un raccord resté en place après qu'un nombre de machines a baissé. Un
total qui monte en basculant est le dessin qui dit quelque chose que la déduction
ne pouvait pas savoir.

Rien d'autre : un champ inconnu est **refusé**, pas ignoré. C'est délibéré — une
faute de frappe dans un nom de champ doit se voir tout de suite et non se traduire
par une valeur par défaut silencieuse.

### Ce que tout nœud possède

| Champ | Type | Défaut | Sens |
| --- | --- | --- | --- |
| `kind` | chaîne | — | le type de nœud ; il décide des autres champs |
| `id` | chaîne | — | unique dans le fichier ; c'est lui que les lignes citent |
| `label` | chaîne ou `null` | `null` | le nom affiché ; sans lui, le nom de l'objet ou de la recette dans la langue de l'interface |
| `position` | `[x, y]` | `[0, 0]` | en pixels de la scène, alignée sur une grille de 20 |
| `show_deployed` | `true`, `false` ou `null` | `null` | dessiner une vignette par machine bâtie ; `null` signifie « suivre la préférence globale ». Purement visuel |

`position` et `show_deployed` ne changent **aucun débit**. Elles sont dans le
document parce qu'elles décrivent *ce* nœud dans *cette* usine, et qu'un utilisateur
qui a rangé son usine s'attend à la retrouver rangée.

---

## 3. Les onze types de nœuds

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

La règle vaut pour un **gisement**, c'est-à-dire pour ce type de nœud, et pas pour
tout ce qui sort quelque chose du sol. Un **puits de ressource** (`resource_well`,
plus bas) n'a pas de pureté : un pressuriseur ouvre plusieurs satellites d'un coup
et rien ne dit qu'ils se ressemblent, donc la pureté y est **par satellite** et le
nœud porte un décompte au lieu d'une valeur. C'est précisément pour garder la règle
ci-dessus entière que le puits est un type de nœud à part et non un `resource`
élargi : une règle qui plie sur la valeur d'un champ n'en est plus une.

`count` est le nombre d'extracteurs, strictement positif, décimales admises.
`clock_speed` est une fraction — `1.0` vaut 100 %, `2.5` vaut 250 % — bornée entre
0,01 et 2,5. Le débit lui est strictement proportionnel ; l'électricité, non.

Débit produit = `débit de base × pureté × count × clock_speed`.
Ici : 120 × 2 × 1 × 1 = **240 minerai/min**.

### `resource_well` — un puits de ressource

```json
{
  "kind": "resource_well",
  "id": "puits1",
  "label": "Puits d'azote",
  "position": [-640.0, 880.0],
  "item_class": "Desc_NitrogenGas_C",
  "extractor_class": "Build_FrackingExtractor_C",
  "satellites": { "impure": 0, "normal": 2, "pure": 0 },
  "clock_speed": 1.0
}
```

**Deux bâtiments en un nœud**, ce qu'aucun autre type n'est. `extractor_class` est
le *satellite* — `Build_FrackingExtractor_C`, 60 m³/min de base, 0 MW. Le
pressuriseur n'est pas écrit : il est lu dans le catalogue à partir du satellite,
pour qu'un fichier ne puisse pas porter un couple qui se contredit. C'est lui qui
consomme, et il consomme tout : **150 MW**, un seul quel que soit le nombre de
satellites.

`satellites` est le décompte par pureté. Les clés absentes valent zéro, et les trois
peuvent coexister — c'est tout l'objet de ce type de nœud. Chaque satellite donne
30, 60 ou 120 m³/min selon sa pureté, et **chacun porte sa propre canalisation** :
un puits de trois satellites a trois ports de sortie en mode fidèle.

`clock_speed` s'applique au puits entier : le débit de chaque satellite lui est
proportionnel, et la consommation du pressuriseur suit son exposant. C'est la seule
lecture sous laquelle surcadencer un puits coûte quelque chose, les satellites étant
déclarés à zéro.

Débit produit = `Σ (satellites × débit de base × pureté) × clock_speed`.
Ici : 2 × 60 × 1 × 1 = **120 m³ d'azote/min**.

Trois ressources seulement ont des puits dans le jeu, et la liste est lue et non
écrite : **pétrole brut, azote, eau**. L'azote n'a que celui-là — aucun extracteur
ne le sort du sol autrement.

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
sortante, sinon le nœud tourne à 0 %. Elle se remarque surtout à l'Encodeur
quantique, dont **chaque** recette rend en résidus de matière noire exactement le
volume de matière photonique qu'elle avale : sans torchère pour les résidus, la
machine est arrêtée — et arrêtée, elle consomme quand même son gigawatt.

Rien dans le fichier ne dit la puissance : elle est lue dans le catalogue, sur le
bâtiment ou, pour trois machines, sur la recette. Le **Convertisseur**,
l'**Accélérateur de particules** et l'**Encodeur quantique** déclarent une plaque à
zéro et portent leur consommation sur ce qu'ils fabriquent. Le bilan retient le
**milieu** de leur plage, qui est la moyenne d'une oscillation symétrique, parce que
ce modèle est en régime permanent et n'a pas de notion de temps :

| Machine | Plage annoncée | Ce que le bilan compte |
| --- | --- | --- |
| Convertisseur | 100 à 400 MW | 250 MW, ses 25 recettes |
| Accélérateur de particules | 250 à 750 MW | 500 MW, diamants et plutonium |
| Accélérateur de particules | 500 à 1500 MW | 1000 MW, matière noire et ficsonium |
| Encodeur quantique | 0 à 2000 MW | 1000 MW, ses 6 recettes |

L'Accélérateur est le seul à avoir deux paliers, et c'est lui qui interdit de poser
le chiffre sur le bâtiment : aucune valeur unique n'est juste pour ses deux moitiés.

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

Un générateur n'a **aucune sortie**, à une exception près : ce qu'il produit est du
courant, et le courant ne circule pas sur une ligne. La **centrale nucléaire**, elle,
rend ses déchets sur un convoyeur — 50 par barre d'uranium, soit 10 par minute, et
10 par barre de plutonium ; une barre de ficsonium ne laisse rien. La règle du
sous-produit s'y applique donc entièrement : **une centrale dont les déchets ne vont
nulle part s'arrête** et ne produit plus un mégawatt.

| | Puissance | Eau d'appoint | Sortie |
| --- | --- | --- | --- |
| Brûleur de biomasse | 30 MW | — | — |
| Générateur à charbon | 75 MW | 45 m³/min | — |
| Générateur à carburant | 250 MW | — | — |
| Centrale nucléaire | 2500 MW | **240 m³/min** | 10 déchets d'uranium/min |

### `geothermal` — un générateur sur un geyser

```json
{
  "kind": "geothermal",
  "id": "geyser1",
  "label": "Geyser pur du plateau",
  "position": [-240.0, 1360.0],
  "generator_class": "Build_GeneratorGeoThermal_C",
  "purity": "pure",
  "count": 1.0
}
```

**Ni entrée ni sortie, et pas de carburant** : c'est le seul bâtiment du jeu qui
produise du courant à partir de rien. Ce qui décide, c'est la `purity` du geyser
sous lui — d'où un type de nœud à part et non un `generator` sans carburant, pour la
même raison qu'un puits n'est pas un gisement.

`count` regroupe les geysers de même pureté, comme `count` regroupe les extracteurs
d'un gisement. Pas de `clock_speed` : le jeu refuse de surcadencer ce bâtiment,
seul de tous les générateurs.

La production oscille, et le modèle étant en régime permanent, c'est la **moyenne**
qui est comptée. Les chiffres sont ceux que le jeu imprime dans la description du
bâtiment :

| Geyser | Ce que le jeu annonce | Ce que le bilan compte |
| --- | --- | --- |
| impur | 50 à 150 MW | **100 MW** |
| normal | 100 à 300 MW | **200 MW** |
| pur | 200 à 600 MW | **400 MW** |

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
  "item_class": null,
  "mode": "standard",
  "filters": {}
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

#### Les trois répartiteurs

`mode` vaut `standard`, `smart` ou `programmable`, et c'est le choix d'un bâtiment :
répartiteur de convoyeurs, répartiteur intelligent, répartiteur programmable. Ils ne
coûtent pas la même chose, et la liste de courses suit.

`filters` dit ce qui est écrit sur chaque branche, **indexé par le nœud à l'autre
bout** — pas par la ligne, dont l'identifiant est un accident de l'ordre dans lequel
le document a été assemblé et qu'un collage réécrit. Trois valeurs :

| Valeur | Sens |
| --- | --- |
| `"*"` | n'importe lequel — le comportement standard, et la valeur par défaut |
| `"+"` | surplus — ne prend que ce dont les autres branches n'ont pas voulu |
| `Desc_..._C` | filtré sur cet objet |

Un `standard` n'a rien d'écrit, un `smart` a **une** branche réglée et un
`programmable` les a toutes. Un `programmable` dont toutes les branches sont en
`"*"` rend exactement les mêmes chiffres qu'un `standard` : c'est le même code.

Deux choses à savoir, qui viennent du jeu et pas de l'outil :

- **une ligne porte un objet**, donc une branche filtrée sur autre chose que ce que
  la ligne transporte ne reçoit jamais rien. Ce n'est pas refusé — on peut construire
  vers quelque chose — mais c'est signalé ;
- **le jeu ne filtre que les convoyeurs.** Il n'existe pas de jonction de pipeline
  intelligente, donc un raccord sur un fluide ne peut être que `standard`.

C'est le mode `surplus` qui déplace les chiffres : il donne un **ordre de service**,
le même mécanisme que les puits illimités servis en dernier. C'est ce qui permet
d'envoyer un sous-produit au recyclage jusqu'à saturation et le reste ailleurs, ce
que le partage égal ne savait pas dire.

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
- **un port porte une ligne** — en mode `faithful` seulement —, et un nœud a autant
  de ports qu'il a de bâtiments : `ceil(count)` par objet et par sens. Huit fonderies
  ont huit sorties, une seule en a une. Un gisement, une pompe, une machine et un
  générateur suivent tous cette règle ; un tampon n'a pas de compte, donc il a un
  port de chaque côté ; une entrée et une sortie d'usine sont la frontière du modèle
  et pas des bâtiments, donc elles n'ont pas de limite. En mode `simple` la règle ne
  s'applique pas et un port porte ce qu'on y dessine ;
- une ligne ne boucle pas sur son propre nœud ;
- les identifiants de lignes sont uniques.

La capacité du tier est une **contrainte, pas une remarque** : un Mk.1 alimenté à
480/min en transporte 60 et refoule le reste en amont.

**Une ligne de trop se raccorde par un répartiteur, qui est un nœud comme un
autre.** L'exemple montre les deux cas côte à côte. Les 240 lingots de fer partent
en trois lignes **sans aucun raccord** : le nœud compte huit fonderies, donc huit
sorties, et trois lignes y tiennent largement. La fonderie de cuivre, elle, est
seule : elle n'a qu'une sortie, et ses deux lignes passent donc par un répartiteur,
qui leur donne 15 et 15. Ses deux gisements se rejoignent symétriquement par un
groupeur, parce qu'une fonderie seule n'a aussi qu'une entrée.

**La bascule entre les deux modes** matérialise ou dissout les raccords, dans les
deux sens et en une seule annulation. Vers le fidèle, les raccords que la
disposition supposait sont posés et les lignes reprises à travers eux ; un partage
qui se ramène à des moitiés et des tiers — 2, 3, 4, 6, 9 lignes — donne exactement
les mêmes débits qu'avant, un partage en 5 ou en 7 n'est pas égal dans le jeu et ne
l'est plus ici non plus, et le rapport dit lesquels, avec l'ancienne et la nouvelle
part. Vers le simple, les raccords disparaissent et les lignes redeviennent
directes ; les mêmes ports redeviennent égaux, et le même rapport le dit dans
l'autre sens. Chaque ligne rescapée prend le **palier le plus étroit du chemin**
qu'elle remplace : une chaîne porte ce que porte son maillon le plus fin.

Un répartiteur **intelligent ou programmable interdit le retour au simple**, et
l'application refuse la bascule en le nommant. Le filtrage et le surplus n'existent
que parce que le raccord existe ; les dissoudre effacerait un routage sans le dire.

---

## 4 bis. Ce qu'une usine générée a de particulier

**Rien.** « Générer une usine depuis un objectif » écrit un `factory.json` comme
n'importe quel autre : mêmes types de nœuds, mêmes lignes, mêmes raccords, mêmes
règles de port. Il n'y a pas de champ « généré », pas de mode à quitter, et rien
dans le fichier ne dit d'où il vient. Elle est générée **dans le mode du document
courant** : en fidèle elle pose ses raccords, en simple elle n'en pose aucun.

Deux choses s'y reconnaissent quand même, et c'est voulu :

- les identifiants viennent du nom de l'objet **dans la langue de l'interface au
  moment de la génération** — `plaque-de-fer` en français, `iron-plate` en anglais —
  derrière un préfixe qui, lui, reste français parce qu'il finit dans le fichier :
  `gisement-`, `sortie-`, `entree-`, `tampon-`. Une usine générée en anglais porte
  donc `gisement-iron-ore`. C'est lisible, mais hybride, et **la question de savoir
  si l'identifiant doit suivre la langue ou rester stable n'est pas tranchée** — voir
  le point ouvert en fin de document ;
- les gisements sont en **pureté normale avec le premier extracteur venu**. Ce
  n'est pas une estimation, c'est une valeur par défaut : ce qui se trouve sur la
  carte n'est écrit nulle part dans les données du jeu, et le rapport de génération
  le dit en toutes lettres.

Les nombres de machines sont **décimaux** dans la variante « ratios exacts » — le
modèle l'a toujours permis — et entiers dans la variante arrondie, qui pose un
conteneur partout où l'arrondi produit un surplus, et nulle part ailleurs.

---

## 5. Ce que l'exemple donne

Résolu contre les données du jeu 1.2 :

| | |
| --- | --- |
| Solides bruts consommés | 240 minerai de fer/min, 30 minerai de cuivre/min, 75 charbon/min, 10 plaques de fer/min |
| Fluides | 255 m³ d'eau/min, 120 m³ d'azote/min |
| Sorties | 80 lingots de fer/min, 30 lingots de cuivre/min, 30 m³ d'acide nitrique/min |
| Rejets assumés | 80 lingots de fer/min |
| Électricité | 312,234 MW consommés, 775 MW produits |
| Liste de courses | 9 fonderies, 1 Mélangeur, 1 Foreuse Mk.2, 2 Foreuses Mk.1, 3 pompes, 1 pressuriseur, 2 satellites de puits, 5 générateurs à charbon, 1 géothermique, 1 conteneur, 1 répartiteur, 1 groupeur |
| Diagnostic | tampon en remplissage, +80/min, saturé en 1 h |

Le puits d'azote à lui seul pèse **150 MW sur les 312**, ce qui explique les cinq
centrales à charbon là où deux suffisaient avant qu'il soit là. Ce n'est pas un
travers de l'exemple : un pressuriseur coûte cela, et l'azote n'a pas d'autre porte
d'entrée.

Tous les nœuds tournent à 100 % et rien n'est en erreur : c'est ce que la suite de
tests vérifie à chaque exécution, pas seulement que le fichier se charge.

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

---

## 7. Le module : le `.sfm`

Un module est un morceau d'usine rangé sous un nom. La bibliothèque vit dans
`%LOCALAPPDATA%\SatisPlanner\modules\`, **un fichier par module**, en JSON UTF-8 :

```json
{
  "module_version": 1,
  "name": "Plaque de fer 40/min",
  "description": "Deux constructeurs sur un banc de fonderies",
  "saved_at": "2026-08-02T18:42:11+00:00",
  "inputs": { "Desc_IronIngot_C": 60.0 },
  "outputs": { "Desc_IronPlate_C": 40.0 },
  "thumbnail": null,
  "code": "SFP1:eJx..."
}
```

**La charge utile est un code de partage**, pas un second format. C'est tout le
choix de conception : le code sait déjà se comprimer, refuser une version future et
— par `migrate()` — relire un document écrit par une build ancienne. Un module
enregistré aujourd'hui continuera donc de s'ouvrir quand un type de nœud sera
ajouté, sans que la bibliothèque ait à en entendre parler.

`module_version` est la version de l'**enveloppe** — les champs autour du code — et
non du document à l'intérieur, qui porte la sienne. Elle vaut 1 et n'a pas bougé.

`inputs` et `outputs` sont une **étiquette**, calculée une fois à l'enregistrement
en résolvant le module seul, entrées servies et sorties écoulées. Sans quoi un
module pris au milieu d'une chaîne s'annoncerait « produit zéro ». Inséré dans une
usine qui l'affame, il en fera moins.

Un fichier illisible coûte **un** module et une phrase qui le nomme, jamais la
bibliothèque : la lecture continue au fichier suivant.

## Un point ouvert : l'identifiant et la langue

Depuis la 5.1.0, l'interface est bilingue, et `_slug()` construit l'identifiant d'un
nœud généré à partir du **nom affiché**, qui suit désormais la langue. Le préfixe,
lui, reste français parce qu'il est écrit en dur et qu'il finit dans le fichier. Une
usine générée en anglais porte donc des identifiants comme `gisement-iron-ore` ou
`sortie-iron-plate` : moitié français, moitié anglais.

**Rien n'est cassé** — l'identifiant n'est qu'une clé, unique dans le document, et
les deux usines se résolvent à l'identique. La promesse « un fichier ne dépend pas de
la langue » tient aussi : elle porte sur un graphe donné, enregistré dans les deux
langues, et pas sur deux générations séparées.

Mais l'état est bâtard, et il y a deux réponses cohérentes, pas trois :

1. **L'identifiant reste français quoi qu'il arrive.** `_slug()` lit
   `display_name_fr` plutôt que `name`. Un identifiant devient alors stable comme un
   nom de classe : deux personnes qui génèrent la même usine dans deux langues
   obtiennent le même document, ce qui est la lecture la plus proche du reste du
   projet.
2. **Tout suit la langue, préfixe compris.** `gisement-` devient `deposit-`, et
   l'identifiant est franchement un libellé. Plus lisible pour qui lit l'anglais,
   mais l'identifiant cesse d'être comparable d'un document à l'autre.

La question n'a pas été tranchée parce qu'elle n'a été vue qu'après coup, en relisant
ce document. **Elle appartient à un lot, pas à une correction de passage.**
