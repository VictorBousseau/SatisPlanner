# Conversion vers les répartiteurs explicites (schéma 4 → 5)

Ce document est le relevé de ce que la conversion a déplacé, mesuré sur les
**21 usines de référence** du projet : les 18 fixtures de `tests/fixtures/graphs`
et les trois usines générées du banc d'essai. Chaque usine a été résolue avant la
conversion avec le code de `e1e096f`, puis après avec le code de ce lot, et les
rapports comparés champ par champ.

Il est ici parce qu'il explique pourquoi certains chiffres ne sont plus les mêmes.
C'est la première fois dans ce projet qu'un lot en déplace volontairement.

---

## Ce qui a changé dans le modèle

**Un port porte une ligne, et un nœud a autant de ports qu'il a de bâtiments.**
`ceil(count)` par objet et par sens : huit fonderies ont huit sorties, une seule en
a une. Trois cas ne sont pas dans les données du jeu et ont été tranchés :

| | Budget | Pourquoi |
| --- | --- | --- |
| Gisement, pompe, machine, générateur | `ceil(count)` | un bâtiment, un port |
| Tampon | 1 | il n'a pas de compte ; un conteneur a une porte de chaque côté |
| Sortie d'usine, **entrée d'usine** | aucun | c'est la frontière du modèle, pas un bâtiment |
| Répartiteur / groupeur | 3 du côté nombreux, 1 de l'autre | la forme du bâtiment |

L'exemption de l'**entrée d'usine** n'était pas dans la liste des points tranchés :
elle est la symétrique exacte de la sortie, et pour la même raison — exiger un
répartiteur devant un apport venu d'ailleurs serait de la cérémonie sans
contrepartie en jeu. Deux convoyeurs qui arrivent de l'usine voisine sont deux
convoyeurs, pas une ligne partagée. Sa conséquence est visible plus bas :
`recycling_loop` perd sa jonction de pipeline.

Le nombre de branches d'un raccord, **3**, est écrit dans `core/constants.py` avec
sa raison : les ports d'un bâtiment vivent dans son blueprint et pas dans
`Docs.json`, où `mFactoryInputConnections` et ses semblables sont exportés vides.
Un test vérifie que la valeur du catalogue et celle de la constante s'accordent.

---

## L'asymétrie qu'il ne faut pas prendre pour un bug

**La forme de l'arbre décide des débits dès que la source est la contrainte**, ce
qui est la situation normale d'une usine dimensionnée au plus juste. Pour un
groupage, elle ne décide que **sous saturation de la ligne sortante**, condition
rare et déjà diagnostiquée à part.

Donc : la conversion des partages déplace des chiffres, celle des groupages presque
jamais. Ce n'est pas une inégalité de traitement, c'est la physique des deux
bâtiments.

---

## Les 21 usines

**20 sur 21 gardent tous leurs débits au bit près.** Une seule change, et c'est la
seule qui contienne un partage que l'arithmétique ne permet pas d'équilibrer.

### Débits identiques (20)

`allocation`, `allocation_redistribution`, `backpressure`, `belt_saturation`,
`blocked_byproduct`, `buffer_draining`, `buffer_filling`, `buffer_to_sink`,
`coal_power`, `computer_chain`, `deficit`, `fuel_power`, `iron_plate`,
`pipe_saturation`, `plastic_chain`, `recycling_loop`, `screws_reinforced_plate`,
`steel_chain`, `banc-200`, `banc-500`.

Quatre d'entre elles ont gagné des raccords, et leurs débits n'ont pas bougé parce
que 2 se ramène à une moitié :

| Usine | Raccords insérés | Débits |
| --- | --- | --- |
| `buffer_to_sink` | 1 répartiteur | identiques |
| `fuel_power` | 1 répartiteur | identiques |
| `recycling_loop` | 2 répartiteurs | identiques |
| `banc-200` | 13 répartiteurs | identiques |
| `banc-500` | 30 répartiteurs | identiques |

### Débits changés (1) — `banc-50`

Un banc de **4 fonderies** alimentait **5 lignes**. Quatre ports pour cinq lignes :
quatre lignes partent directement, la cinquième partage un port avec une autre à
travers un répartiteur. Les parts nominales passent donc de 20 % chacune à
**25 %, 25 %, 25 %, 12,5 %, 12,5 %**.

| Nœud | Avant | Après |
| --- | --- | --- |
| `sortie0x2` | 12 lingots/min | 15 |
| `sortie0x4` | 12 | 15 |
| `sortie0x6` | 12 | 15 |
| `sortie0x0` | 12 | 7,5 |
| `tampon1b` | 12 | 7,5 |
| Sorties d'usine (fer) | 138/min | 142,5 |

C'est ce que donne un arbre réel, et c'est ce que le jeu donne. Aucun artifice n'a
été employé pour retrouver les 12 : ce serait réintroduire exactement
l'approximation qu'on retire.

### La liste de courses, elle, bouge partout

Deux effets en sens contraire, tous deux voulus :

- **moins de raccords**, parce qu'un nœud à *n* bâtiments a *n* ports et n'a donc
  plus besoin de rien ;
- **plus de segments de ligne**, parce qu'un arbre a des lignes internes.

| Usine | Raccords avant | après | Convoyeurs avant | après |
| --- | --- | --- | --- | --- |
| `allocation` | 1 répartiteur | — | | |
| `allocation_redistribution` | 1 répartiteur | — | | |
| `computer_chain` | 1 répartiteur | — | | |
| `screws_reinforced_plate` | 1 répartiteur | — | | |
| `recycling_loop` | 2 répartiteurs, 1 jonction | 2 répartiteurs | 4 | 6 |
| `buffer_to_sink` | — | — | 4 | 5 |
| `fuel_power` | — | — | 4 tuyaux | 5 tuyaux |
| `banc-50` | 8 répartiteurs, 3 jonctions | 5 répartiteurs | 33 | 38 |
| `banc-200` | 17 répartiteurs, 12 jonctions | 13 répartiteurs | 129 | 142 |
| `banc-500` | 45 répartiteurs, 29 jonctions | 30 répartiteurs | 327 | 357 |

Les quatre premières perdent leur répartiteur parce qu'il était déduit d'un nœud
qui a assez de ports pour ses lignes. Les jonctions de pipeline disparaissent parce
qu'elles étaient déduites d'une **entrée d'usine** à deux lignes, qui n'est pas un
bâtiment.

**Conséquence à assumer : la liste de courses d'une usine équilibrée est plus
courte qu'avant.** C'est fidèle au jeu.

---

## Performance

Banc d'essai rejoué, **à usine égale** : la même conception de 50, 200 et 500 nœuds,
avant sans ses raccords et après avec. Médiane de cinq passages, poste de référence.

| Conception | Nœuds résolus | Résolution avant | après | Par nœud avant | après |
| --- | --- | --- | --- | --- | --- |
| 50 | 50 → 55 | 20,9 ms | 26,5 ms | 0,42 ms | 0,48 ms |
| 200 | 200 → 213 | 74,7 ms | 91,2 ms | 0,37 ms | 0,43 ms |
| 500 | 500 → 530 | 199,4 ms | 229,1 ms | 0,40 ms | 0,43 ms |

Le coût reste **linéaire en la taille de l'usine**, qui est la règle posée au Lot 1
et la seule qu'un seuil puisse honnêtement garder. L'édition et le déplacement sont
inchangés (l'édition à 500 nœuds passe même de 474 à 416 ms).

---

## La disposition

Un arbre inséré ne se pose sur rien : chaque raccord est placé entre le nœud et ce
qu'il dessert, et décalé vers le bas tant que la place est prise. La règle est
vérifiée sur les rectangles réellement dessinés, pas sur l'enveloppe supposée, et
sur le banc de 50 nœuds la conversion n'ajoute **aucune** superposition à celles que
la grille du banc avait déjà.

Quand deux colonnes sont trop rapprochées pour qu'un arbre tienne entre elles — des
nœuds larges à moins de 460 unités l'un de l'autre — les raccords sont empilés dans
une seule voie à mi-chemin plutôt que posés par-dessus les machines. Une usine
dessinée large reste rangée ; une usine dessinée serrée reçoit une colonne de
raccords qu'il peut être agréable de déplacer à la main.
