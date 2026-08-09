# SatisPlanner

Planificateur d'usines **théoriques** pour Satisfactory 1.2. On pose des nœuds sur un canvas
(gisement → fonderie → constructeur → assembleuse, pétrole → raffinerie…), on les relie par des
convoyeurs et des tuyauteries, et l'application calcule en régime permanent : débits, nombre de
machines réellement utile, goulots d'étranglement, saturation des lignes, consommation électrique
et liste des bâtiments à construire.

L'application est **autonome** : la base de recettes est embarquée dans l'exécutable, il n'y a
rien à configurer, et **Satisfactory n'a pas besoin d'être installé** sur la machine qui l'exécute.

Ce n'est **ni un mod ni un lecteur de sauvegarde**. Aucune interaction avec le jeu en cours
d'exécution, aucun accès réseau au runtime.

## Ce que l'outil ne fait pas

Autant le dire avant d'ouvrir la fenêtre.

- **Il raisonne en débits, pas en géométrie.** Aucune notion de distance, d'élévation, ni de
  hauteur de refoulement des pompes. Un tuyau dont le débit théorique passe sera déclaré valide,
  même si en jeu il faudrait une pompe. Une usine validée ici tient sur le papier, pas
  nécessairement sur le terrain.
- **Régime permanent uniquement.** Les tampons sont des puits et des sources infinis, jamais des
  réservoirs simulés dans le temps. L'application dit si les débits sont tenables et en combien de
  temps un stock se vide, mais ne joue pas le film.
- **Pas de Somersloop ni d'amplification de production.** Le surcadençage, lui, est modélisé :
  de 1 % à 250 %, débits proportionnels et électricité en loi de puissance. (Somersloop en V2)
- **L'électricité est un compteur, pas une contrainte.** Consommation et production sont
  affichées côte à côte, et un déficit est signalé en erreur — mais il ne bride aucun débit. En
  jeu, manquer de courant ne ralentit pas l'usine : cela disjoncte tout le réseau jusqu'à
  intervention manuelle. Afficher tout à zéro n'apprendrait rien, et un bridage partiel serait une
  invention. Les générateurs tournent à 100 % : leur surcadençage suit un exposant différent de
  celui des machines. (V2)
- **Deux modes, et c'est l'usine qui choisit.** En **simple** — le défaut d'une usine neuve — un
  port porte autant de lignes qu'on veut, le partage max-min se fait là, et les raccords sont
  déduits pour la liste de courses sans être dessinés : c'est le mode pour réfléchir aux débits.
  En **fidèle**, la règle du jeu s'applique — un port, une ligne — et un répartiteur ou un groupeur
  est un nœud qu'on pose et qu'on voit : c'est le mode pour construire. Le réglage vit dans le
  document et voyage avec lui, parce qu'il change les chiffres. Les raccords ne sont jamais un
  goulot — un répartiteur passe 2000 items/min quand le meilleur convoyeur plafonne à 1200 — mais
  ils décident du partage.
- **Hors périmètre V1** : Mélangeur, Convertisseur, Encodeur quantique, Accélérateur de
  particules, nucléaire, extracteur de puits de ressources, Clean Pipeline.
- **Le nombre de machines est une saisie**, pas un résultat — sauf si vous demandez l'inverse.
  « Je veux 2 Cadres modulaires lourds par minute » est le **mode objectif**, et il construit
  l'usine ; mais il n'**optimise** rien. Il suit la recette standard, ou celle que vous imposez,
  sans jamais chercher la meilleure combinaison d'alternatives : cela demanderait un programme
  linéaire, donc une dépendance, et c'est de la V2.
- **Il ne connaît pas votre carte.** Une usine générée pose ses gisements en pureté normale avec
  le premier extracteur venu, et le dit. Rien dans les fichiers du jeu ne sait où sont vos nœuds
  ni ce qu'ils valent.

## Installation

### Pour utiliser l'application

Récupérer le dossier construit, le décompresser où l'on veut, lancer `SatisPlanner.exe`. Il n'y a
pas d'installateur, pas de dépendance à installer, rien à configurer. Windows peut afficher un
avertissement SmartScreen au premier lancement — l'exécutable n'est pas signé.

### Pour développer

Windows, Python 3.12. Le lanceur `py` est utilisé car `python` nu peut renvoyer le raccourci
Microsoft Store.

```bash
py -3.12 -m venv .venv
.venv/Scripts/python.exe -m pip install --upgrade pip
.venv/Scripts/python.exe -m pip install -e ".[dev]"
```

Lancer l'application :

```bash
.venv/Scripts/python.exe main.py
```

Vérifications :

```bash
.venv/Scripts/python.exe -m pytest
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m mypy
```

## Utilisation

Palette à gauche, les usines ouvertes au centre, trois panneaux à droite.

- **Onglets** : plusieurs usines ouvertes à la fois. `Ctrl+N` ou `Ctrl+T` pour un nouvel onglet,
  `Ctrl+W` pour fermer celui du dessus, `Ctrl+Tab` pour passer au suivant ; ouvrir un fichier ou
  importer un code de partage ouvre son propre onglet, et un fichier récent aussi. Le titre de
  l'onglet porte le nom du fichier et un point quand il reste du travail non enregistré.
  **Chaque onglet garde son zoom, son cadrage et sa sélection** : y revenir retrouve l'usine là où
  on l'avait laissée. `Annuler` défait toujours dans l'usine qu'on regarde, jamais dans une autre.
  Les préférences, la palette et les couleurs par objet restent communes à toute l'application.
- **Palette** : recherche insensible aux accents et par mots indépendants (« alt plaque » trouve
  « Alternative : plaque de fer moulée »), filtre par machine, bascules pour les recettes
  alternatives et les objets d'événement, et le tier par défaut des nouvelles lignes.
  Glisser-déposer vers le canvas, **Entrée** pour poser au centre de la vue, et
  **double-clic pour ouvrir la fiche** de l'objet.
- **Fiche d'objet** : double-clic dans la palette, ou clic droit ▸ « Fiche de… » sur un nœud.
  Description du jeu, forme, taille de pile, points au collecteur AWESOME, puis toutes les recettes
  qui le fabriquent — machine, durée de cycle, quantités par cycle et débits par minute,
  sous-produits, électricité — puis celles qui le consomment, puis le coût en ressources brutes.
  Chaque ingrédient est un lien vers sa propre fiche, avec précédent et suivant : on suit une
  chaîne de fabrication comme on suit un wiki. Chaque recette porte un bouton « poser sur le
  canvas ». Le coût en minerai est **indicatif et le dit** : il ne suit que les recettes standard
  et ne crédite pas les sous-produits.
- **Gisements** : la pureté et le type d'extracteur se lisent sur le nœud
  (« 1 Foreuse Mk.3 — gisement pur ») et se changent par clic droit ou par les colonnes du
  tableau, sans supprimer le nœud ni ses lignes. **La pureté appartient au gisement** : elle
  multiplie tous les extracteurs du nœud. Deux gisements de puretés différentes sont deux nœuds,
  et c'est la seule façon de les représenter.
- **Cadence** : chaque extracteur et chaque machine se règle de 1 % à 250 % (clic droit ▸
  « Cadence… », ou la colonne du tableau). Un nœud dont la cadence n'est pas 100 % l'affiche en
  toutes lettres. Le débit suit la cadence exactement ; l'électricité suit une loi de puissance,
  et les éclats de charge nécessaires apparaissent dans la liste de courses.
- **Générateurs** : brûleur de biomasse, générateur à charbon et générateur à carburant. Le
  carburant se lit sur le nœud (« 1 unité(s) — Charbon — 75 MW produits ») et se change par clic
  droit ▸ « Carburant » ou par la colonne du tableau ; seuls les carburants que le bâtiment accepte
  sont proposés. **L'eau d'appoint du générateur à charbon est une vraie entrée fluide** : elle se
  raccorde par tuyauterie et subit les mêmes contraintes de capacité et de contre-pression que
  n'importe quelle autre. Les débits sont déduits de la puissance produite et de la valeur
  énergétique de l'item, jamais codés en dur.
- **Raccords** : un **répartiteur** et un **groupeur** se posent depuis la section « Raccords » de
  la palette. Un répartiteur prend une ligne et en ressort jusqu'à trois, un groupeur fait
  l'inverse ; ils ne brident rien et ne gardent rien, mais ils décident du partage. Le **mode** se
  lit sur le nœud sans un clic et se change par clic droit ou par la colonne du tableau :
  **standard**, **intelligent** (une branche réglée) ou **programmable** (toutes). Le seul réglage
  qui déplace des chiffres est **« surplus »** — cette branche ne prend que ce dont les autres
  n'ont pas voulu — parce qu'une ligne ne porte qu'un objet : filtrer une branche sur ce qu'elle
  transporte déjà ne change rien, et la filtrer sur autre chose la ferme. Les trois bâtiments ne
  coûtent pas la même chose, et les matériaux de construction suivent.
- **Générer une usine** (`Ctrl+G`) : « 2 Cadres modulaires lourds par minute » et l'usine se
  développe seule, dans un onglet neuf — machines, lignes, raccords, gisements et sorties. Deux
  variantes : **ratios exacts**, avec des machines en nombre décimal et tout à 100 %, ou **arrondi
  au bâtiment entier**, constructible tel quel, avec un conteneur là où l'arrondi crée un surplus.
  Une recette peut être **imposée par objet** — c'est là que les alternatives entrent, par choix et
  non par calcul — et ce choix est retenu d'une session à l'autre. Ce qui en sort est une usine
  ordinaire : modifiable, enregistrable, annulable. Le rapport de génération dit en tête ce qui
  reste à régler, à commencer par la pureté des gisements.
- **Édition en place** : **double-clic sur une valeur affichée sur un nœud** — nombre de machines,
  cadence, pureté, extracteur, carburant, débit d'un apport externe, stock d'un tampon — ou sur une
  ligne pour son tier. Entrée valide, Échap annule, une valeur hors domaine est refusée **sans être
  effacée**, avec la raison dans la barre d'état. Les champs à valeurs discrètes ouvrent la même
  liste déroulante que le tableau, jamais une saisie libre.
- **Copier-coller** : `Ctrl+C` / `Ctrl+X` / `Ctrl+V`, plus `Ctrl+D` pour dupliquer sans toucher au
  presse-papiers. Les lignes internes à la sélection suivent, celles qui en sortaient non — elles
  n'auraient plus rien à quoi s'accrocher. Un collage est **une seule annulation**. La sélection
  voyage dans le presse-papiers système au format code de partage, donc **entre deux onglets et
  entre deux fenêtres**. Un presse-papiers qui contient autre chose est ignoré en silence.
- **Modules** (`Ctrl+Maj+M` pour enregistrer, `Ctrl+B` pour la bibliothèque) : une sélection
  s'enregistre sous un nom — « Plaque de fer 40/min » — et se réinsère dans n'importe quel projet,
  au centre de la vue et **en une seule annulation**. La bibliothèque vit dans `%LOCALAPPDATA%`,
  un fichier par module, et se cherche par nom, par description ou par objet produit. Renommer,
  décrire, supprimer. Un module s'ouvre aussi **dans son propre onglet** pour être modifié puis
  réenregistré sous le même nom ou sous un autre, et « Nouveau depuis ce module » démarre une usine
  sur cette base.

  Deux choses sont écrites à l'écran parce qu'on les suppose à l'envers. Un module inséré est une
  **copie** : le modifier ensuite ne change pas le module, et modifier le module ne change pas les
  usines où il est déjà. Et les débits affichés sont ceux du module **seul**, calculés à
  l'enregistrement en le résolvant avec ses entrées servies et ses sorties écoulées — sans quoi un
  module pris au milieu d'une chaîne s'étiquetterait « produit zéro ». C'est une étiquette, pas une
  promesse : inséré dans une usine qui l'affame, il en fera moins.
- **Machines déployées** (`Ctrl+M`, désactivé par défaut) : une vignette par machine bâtie, en
  grille, avec une vignette partielle pour un compte fractionnaire et « … ×N » au-delà du plafond.
  Clic droit sur un nœud pour y déroger — afficher, masquer, ou suivre la préférence. **Purement
  visuel** : aucun chiffre ne change, aucun nœud ni ligne de liste de courses en plus.
- **Canvas** : une connexion se tire d'un port de sortie vers un port d'entrée. **Une liaison
  impossible est refusée pendant le tirage** — le trait devient rouge avec la raison en infobulle
  — et non signalée après coup. Clic droit sur un nœud pour l'ajuster à ses intrants ou fixer son
  nombre de machines ; clic droit sur une ligne pour changer de tier ou passer au tier suffisant.
- **Tableau** : un nœud par ligne, tri, filtre, sélection synchronisée dans les deux sens avec le
  canvas, colonne « Quantité » éditable — machines, extracteurs, débit d'un apport externe ou
  stock initial d'un tampon selon le type de nœud — plus « Cadence », « Pureté », « Extracteur »
  et « Carburant ». Les trois dernières se choisissent dans une liste, jamais en tapant du texte.
- **Totaux** : matières brutes, fluides et sous-produits, électricité, liste de courses, matériaux
  de construction. Quand l'usine vit sur un stock, un bandeau rouge et deux colonnes de chiffres —
  « avec les stocks » et « régime établi » — remplacent le silence qui laisserait croire à une
  réussite.
- **Matériaux de construction** : ce qu'il faut avoir fabriqué avant de pouvoir bâtir l'usine,
  agrégé par objet. Les coûts sont lus dans les recettes du jeu — un bâtiment se construit par une
  recette comme le reste — et multipliés par les comptes de la liste de courses. Un niveau de
  profondeur : dix fonderies coûtent cinquante barres de fer, pas le minerai qu'il faut pour les
  faire. **Les convoyeurs et les tuyaux n'y sont pas chiffrés** : leur coût se paie à la longueur,
  et l'outil ne connaît aucune distance. Le blanc est explicite et compte les lignes concernées.
- **Diagnostics** : triés par niveau, filtrables, **cliquables** — sélectionner une ligne
  sélectionne et centre le nœud ou la ligne concernée. Quand un diagnostic nomme une correction,
  un bouton l'applique.

**Aide ▸ Gestes et raccourcis** (`F1`) liste tous les gestes du canvas et tous les raccourcis. La
table des raccourcis est construite à partir des actions réelles de la fenêtre : elle ne peut pas
se désynchroniser du code.

**Fichier ▸ Préférences** (`Ctrl+,`) : tier de convoyeur et de tuyauterie par défaut, dossier
d'icônes, nombre de fichiers récents conservés, affichage par défaut des recettes alternatives et
des objets d'événement.

## Fichiers et partage

Une usine s'enregistre en `.sfp` : une archive ZIP contenant `factory.json`, un `manifest.json`
(version de l'application, version des données de jeu, date, version de schéma) et une vignette
`thumbnail.png`.

Le format est décrit champ par champ dans [`docs/format-usine.md`](docs/format-usine.md), avec un
exemple complet et fonctionnel — [`docs/exemple-usine.json`](docs/exemple-usine.json), couvrant les
**neuf** types de nœuds — que la suite de tests charge, résout et vérifie à 100 % partout, pour
qu'il ne puisse pas se périmer en silence. Le même document décrit aussi le `.sfm` de la
bibliothèque de modules, dont la charge utile est un code de partage et non un second format.

Le même graphe se partage en une ligne de texte, `SFP1:<base64url(zlib(json))>`, avec « copier le
code » et « importer depuis un code ». Un code tronqué, corrompu, mal collé ou venu d'une version
future est refusé par une phrase en français — jamais par une trace d'exécution.

**Un fichier qui référence une classe disparue s'ouvre quand même**, mais les nœuds concernés sont
retirés et nommés, et le reste de la disposition est conservé. Le document est alors marqué
« OUVERTURE PARTIELLE » dans le titre, et un `Ctrl+S` réflexe ne peut pas écraser le fichier
d'origine sans une confirmation explicite rappelant ce qui a été retiré. C'est le seul endroit de
l'application où un geste machinal pourrait détruire le travail de quelqu'un d'autre.

`satisplanner/data/factory_file.py` porte le **point d'entrée unique de migration** :
`migrate(payload, schema_version)` fait remonter un document une version à la fois. Il a servi pour
la première fois avec la cadence : un fichier de schéma 1 s'ouvre tel quel, la cadence prenant sa
valeur par défaut de 100 %, et le document est noté comme converti. Le numéro de schéma a été
incrémenté malgré l'absence de conversion à faire, pour qu'une V1 refuse un fichier V1.1 par une
phrase plutôt que par une erreur de validation.

Le schéma courant est le **6**. Le passage du 4 au 5 est le premier qui écrit quelque chose et le
premier qui puisse déplacer un chiffre : les répartiteurs qu'une disposition supposait sont
matérialisés, les lignes reprises à travers eux, et un partage en 5 ou en 7 cesse d'être égal
parce qu'il ne l'est pas dans le jeu. Le relevé des vingt-et-une usines de référence, avec
l'ancienne et la nouvelle part de chaque branche touchée, est dans
[`docs/migration-repartiteurs.md`](docs/migration-repartiteurs.md). Le passage du 5 au 6 ne fait
rien, et c'est le propos : un répartiteur écrit avant les modes est un `standard`, et un standard
est le cas du programmable où rien n'est écrit sur aucune branche.

Exports : PNG du canvas, PDF avec le canvas en première page et, au choix, les totaux et les
diagnostics en seconde.

## Journal et incidents

L'application écrit un journal dans `%LOCALAPPDATA%\SatisPlanner\logs\` :

- `satisplanner.log` — le déroulé normal, avec rotation (1 Mo, trois générations) ;
- `crash.log` — les plantages natifs, ceux qui ne laissent aucune trace Python.

Toute exception non rattrapée est journalisée avec sa trace complète, puis résumée à l'écran en
une phrase compréhensible accompagnée du chemin du journal. Le code de sortie est journalisé lui
aussi : un journal qui s'arrête n'est jamais ambigu. Le chemin du journal est rappelé dans
**Aide ▸ À propos**.

## Icônes du jeu (procédure FModel)

L'application fonctionne **sans aucune icône** : chaque classe sans fichier est dessinée par
`ui/icon_provider.py` — un carré arrondi dont la teinte vient d'un hachage stable du nom de
classe, avec les initiales du libellé français au centre. C'est le fonctionnement nominal, pas
une dégradation, et c'est ce que fait la variante distribuable de l'exécutable.

Les icônes du jeu appartiennent à Coffee Stain Studios et ne sont pas redistribuables. Pour les
avoir chez soi, il faut les extraire de son propre exemplaire du jeu :

1. Installer **FModel** (<https://fmodel.app>).
2. Ajouter le répertoire de paks :
   `…\Steam\steamapps\common\Satisfactory\FactoryGame\Content\Paks`.
3. Laisser FModel détecter la version d'Unreal Engine. S'il demande de la choisir, prendre
   l'entrée UE 5.x qui permet aux archives de se charger : c'est autovérifiant, avec une version
   fausse rien ne s'ouvre.
4. Dans l'arborescence, aller dans `FactoryGame/Content/FactoryGame`. Les icônes utiles sont les
   textures nommées `*_256` : `IconDesc_AssemblerMk1_256`, `GasMask_256`, etc.
5. Clic droit sur un dossier ▸ **Export Folder's Packages Textures (.png)**. Faire les dossiers
   `Resource`, `Buildable` et `Equipment` ; l'arborescence produite n'a pas d'importance.
6. Ouvrir **Fichier ▸ Préférences** dans SatisPlanner et désigner le dossier d'export — ou
   déposer les fichiers dans `%LOCALAPPDATA%\SatisPlanner\icons\`, qui est le dossier par défaut.

Le dossier est indexé immédiatement, récursivement, **par nom de fichier** : peu importe la façon
dont FModel a rangé son export, seul le nom compte. La barre d'état indique combien de fichiers
ont été indexés. Pour savoir lesquels manquent encore, la régénération de la base les liste :

```bash
.venv/Scripts/python.exe -m satisplanner.data.build --game-dir "C:\...\Satisfactory" --icons-dir "C:\mon\export"
```

`satisplanner/resources/icons/` est ignoré par git et n'est **jamais** versionné.

## Données du jeu

La base SQLite est un **artefact livré** : générée une fois à la conception, versionnée, embarquée
dans l'exe. Rien n'y écrit d'horodatage, donc régénérer depuis la même version du jeu produit le
même fichier et un diff vide.

Régénération (à relancer quand le jeu change de version) :

```bash
.venv/Scripts/python.exe -m satisplanner.data.build --game-dir "C:\Program Files (x86)\Steam\steamapps\common\Satisfactory"
```

Le CLI découvre seul son fichier source dans `CommunityResources/Docs` : `en-US.json` en référence
de structure, `fr.json` pour les libellés, avec repli sur une autre variante anglaise puis sur
`Docs.json`. Aucun nom de fichier n'est codé en dur, et le fichier retenu est affiché.

Fixture de test : `tools/extract_fixture.py` extrait des mêmes fichiers une tranche de quelques
centaines de kilo-octets vers `tests/fixtures/`, en conservant l'encodage UTF-16 LE et le BOM
d'origine — les tests traversent donc le même chemin de décodage que la production.

## Construction de l'exécutable

```bash
.\build_exe.ps1 -NoAssets -Clean
```

Deux variantes, et la différence est juridique avant d'être technique :

| Commande | Contenu | Usage |
|----------|---------|-------|
| `.\build_exe.ps1` | avec les icônes présentes dans `resources/icons/` | privé uniquement |
| `.\build_exe.ps1 -NoAssets` | sans aucune icône du jeu | **c'est celle qui se distribue** |

Le script vérifie **après** la construction que la base est bien dans le dossier produit et que la
variante `-NoAssets` ne contient effectivement aucune icône. Un exe parfait qui n'affiche plus
rien parce qu'une donnée n'a pas été embarquée est le piège classique de l'empaquetage, et il ne
se voit qu'en le lançant.

Le build est en `--onedir` et non `--onefile` : un `--onefile` se décompresse dans un dossier
temporaire à chaque lancement, ce qui ajoute plusieurs secondes au démarrage. Le dossier se
compresse en zip pour l'envoi ; la lenteur, elle, ne se compresse pas. (Voir aussi `NOTICE.md` :
les DLL de Qt restent des fichiers distincts et remplaçables, ce que la LGPL apprécie.)

Mesure du démarrage :

```bash
Measure-Command { Start-Process 'dist\publiable\SatisPlanner\SatisPlanner.exe' -ArgumentList '--startup-probe' -Wait }
```

L'icône de l'exécutable est dessinée par `tools/make_app_icon.py` et versionnée. Elle ne reprend
aucun élément graphique du jeu.

## Architecture

```
ui  -->  core  <--  data
```

`core/` est un domaine pur : il n'importe jamais Qt et ne lit jamais la base de données. Les
données lui parviennent par injection (`data.db.load_game_data` produit le catalogue
`core.models.GameData`). `tests/test_architecture.py` vérifie cette règle par analyse statique des
imports et échoue sinon.

À l'intérieur de `core/`, l'ordre des dépendances est
`models → formatting, graph → results → validation → engine` : les diagnostics lisent un rapport
résolu sans jamais calculer de débit, et le moteur les appelle en fin de résolution. `formatting`
tient les règles d'écriture françaises des nombres — une seule fois, pour que « 66,667 % » s'écrive
pareil dans un avertissement et sur un nœud.

`ui/` ne contient pas de logique de calcul : `document.py` porte le graphe édité et la pile
d'annulation, `commands.py` les opérations, `catalogue.py` la passerelle entre le catalogue et la
palette (sans Qt, donc testable sans fenêtre), `canvas.py` / `canvas_items.py` le rendu,
`report_html.py` le rapport en HTML — partagé par le panneau des totaux et l'export PDF, pour que
la page imprimée et le panneau à côté ne puissent pas afficher deux chiffres différents.

`core/interface.py` calcule ce qu'un morceau d'usine prend et rend **seul**, en le résolvant avec
ses ports ouverts servis d'un côté et écoulés de l'autre ; `data/module_file.py` range la
bibliothèque, dont la charge utile est **le code de partage** — pas un second format, ce qui lui
donne gratuitement la migration de schéma, le refus d'une version future, et la prise en charge
des types de nœuds qui n'existent pas encore.

`document_tab.py` réunit ce qui appartient à une usine ouverte et à elle seule — son document, sa
scène, sa vue — et `binding.py` tient les connexions du document affiché : tout ce qui est branché
est noté, et le débranchement rejoue cette note. Oublier de défaire une connexion demanderait
d'oublier de la faire, ce qui est la seule protection sérieuse contre un panneau qui se met à jour
deux fois sans que rien ne se voie. `MainWindow._activate` est le seul endroit qui sait que
l'usine regardée a changé : il y débranche, rebranche, et désigne la pile d'annulation active.

`data/factory_file.py` lit et écrit les usines : c'est de l'entrée-sortie, donc c'est dans `data/`,
au même titre que le parseur du jeu et la base SQLite.

`paths.py` répond aux deux questions que l'empaquetage sépare : où sont les ressources en lecture
seule (à côté du paquet en développement, dans `sys._MEIPASS` une fois gelé) et où écrire (sous
`%LOCALAPPDATA%`, jamais à côté de l'exécutable — un programme installé sous `Program Files` ne
peut pas y écrire, et la première chose qu'il essaierait d'y écrire serait le journal de plantage).

## Le moteur

Calcul en régime permanent. Une seule grandeur par nœud, son **taux de fonctionnement** :

```
taux = min( satisfaction de chaque entrée, absorption de chaque sortie )
```

```bash
.venv/Scripts/python.exe tools/show_report.py tests/fixtures/graphs/plastic_chain.json
```

affiche le `FactoryReport` complet en console : nœuds, lignes, bilan en trois catégories (solides,
fluides et sous-produits, électricité), liste de courses et diagnostics.

Un `solve()` enchaîne en réalité plusieurs points fixes : la réponse, sa **jumelle sans plafond de
ligne** — qui donne le débit qu'une ligne *voudrait* porter, et donc le tier à installer — et,
quand un tampon se vide, la même paire résolue une seconde fois avec les tampons ne fournissant
rien. C'est ce second jeu de chiffres que porte `FactoryReport.sustained`.

Les deux sont **conditionnels**. La jumelle n'est calculée que si un plafond de transport a
réellement changé ce qui a été livré quelque part : un gisement surdimensionné dont l'offre est
rognée par la courroie alors que la machine en aval reçoit malgré tout tout ce qu'elle peut avaler
ne coûte rien de plus. Et la seconde paire n'existe que si un tampon se vide. Une usine bien
dimensionnée n'est donc résolue qu'**une seule fois**.

## Performance

```bash
.venv/Scripts/python.exe tools/benchmark.py
.venv/Scripts/python.exe tools/benchmark.py --profile 500
```

mesure trois gestes — une résolution complète, une édition qui change les chiffres jusqu'à
l'affichage à jour, un déplacement de nœud — sur des usines générées de 50, 200 et 500 nœuds
(`tests/benchmark_graphs.py`, déterministe et versionné). `tests/test_performance.py` en tire des
seuils, et surtout des règles qui ne dépendent d'aucune machine : **un déplacement ne déclenche
jamais de résolution**, un rapport aux mêmes nœuds ne réinitialise jamais le tableau.

Une position est portée par un signal distinct de celui qui annonce un changement de *forme*
(`nodesMoved` contre `graphChanged`) : ranger son usine ne fait tourner ni le moteur, ni la
reconstruction de la scène, ni la réinitialisation du tableau. Le déplacement reste annulable.

## Décisions de conception

1. **Aucun chiffre deviné.** Les débits sont dérivés des données du jeu par des conversions
   centralisées (`satisplanner/data/conversions.py`), chacune documentant son champ source, sa
   formule et sa valeur de contrôle, et testées contre une table de référence (convoyeurs 60 → 1200
   items/min, tuyauteries 300 / 600 m³/min, foreuses 60 / 120 / 240, etc.). Si une formule dérive,
   le test échoue immédiatement au lieu de propager l'erreur. **En cas de désaccord entre une valeur
   attendue et les fichiers du jeu, les fichiers font foi** — c'est ainsi qu'a été détectée la
   recette d'Ordinateur, dont les ingrédients ont changé depuis la 1.0.
2. **Blocage des sous-produits évalué sur la topologie, pas sur le débit.** Une machine est bloquée
   (taux = 0) uniquement s'il n'existe aucune sortie pour l'un de ses produits. Si une sortie existe
   mais n'absorbe qu'une fraction du débit, on applique une contre-pression continue (taux < 1) —
   ce qui reproduit le débit moyen réel du jeu. Évaluer le blocage sur le débit rendrait le point
   fixe bistable.
3. **Le nombre de machines est une entrée**, saisie par l'utilisateur. Le moteur restitue en regard
   le nombre réellement utile compte tenu des intrants et l'écart.
4. **L'itération de point fixe part optimiste et descend.** Tous les taux valent 1 au départ, et la
   suite décroît jusqu'à se stabiliser. Partir de zéro donne une réponse dégénérée : dans une boucle
   de recyclage, « tout est arrêté » est un état parfaitement cohérent dont un solveur initialisé à
   zéro ne sort jamais. Le comportement réel est le **plus grand** état cohérent.
5. **La capacité des lignes est une contrainte.** Un convoyeur Mk.1 alimenté à 480/min en transporte
   60 et refoule le reste en amont, exactement comme dans le jeu. Le diagnostic donne les deux
   chiffres — le débit porté et le débit demandé — parce que c'est le second qui nomme le tier à
   installer. Ce débit demandé vient d'une résolution jumelle où les plafonds sont ignorés : il est
   calculé, pas estimé.
6. **L'allocation est une répartition max-min, pas une répartition proportionnelle.** Un répartiteur
   donne à chaque sortie une part égale et, quand l'une sature, partage le reste également entre les
   autres. 60 lingots pour deux consommateurs qui en demandent 30 et 60 donnent donc 30 et 30 — le
   petit est servi entièrement — et non 20 et 40, qui feraient boiter les deux.
7. **Un rapport peut mentir treize minutes.** Une usine dont les citernes se vident tourne au débit
   affiché, jusqu'à ce qu'elles soient vides. `FactoryReport.is_sustainable` passe à faux dès qu'un
   tampon a un débit net négatif, et le rapport porte alors le régime réellement établi, résolu une
   seconde fois avec les tampons ne fournissant plus rien.
8. **Un fichier qui référence une classe disparue s'ouvre quand même**, mais le nœud concerné est
   retiré et nommé. Le garder serait pire : le solveur, le canvas et le tableau cherchent tous sa
   recette dans le catalogue et sont en droit de l'y trouver. Ce que l'utilisateur risquait de
   perdre — la disposition de tout le reste — est préservé, et le document est marqué de façon que
   la perte ne puisse pas être propagée au fichier d'origine par inadvertance.
9. **Concevoir et construire ne veulent pas la même chose, donc l'usine dit laquelle.** Le mode
   des raccords est un champ du document et non une préférence : une usine partagée doit s'ouvrir
   dans le mode où elle a été pensée, sinon ses chiffres changent chez le destinataire. Ce n'est
   pas ce qu'on fait d'une palette de couleurs, et c'est justement la différence — celui-ci change
   les résultats. Les deux modes ne sont **pas deux moteurs** : un raccord est un nœud ordinaire
   doté d'une plaque signalétique, donc le mode fidèle est le même code avec des nœuds en plus et
   le mode simple le même code avec aucun. Vérifié plutôt qu'argumenté : les usines de référence
   résolues en mode simple sont comparées champ par champ à un instantané produit par la build qui
   précédait les raccords explicites (`tests/fixtures/reports_avant_lot4.json`). La seule chose qui
   diffère est **qui compte les raccords** — déduits d'un côté, comptés de l'autre — et l'écart est
   expliqué dans la page d'aide, parce que c'est la question qu'on se pose en basculant.
10. **Un port porte une ligne, et un nœud a autant de ports qu'il a de bâtiments.** `ceil(count)`
   par objet et par sens : huit fonderies alimentant huit consommateurs n'ont besoin d'aucun
   répartiteur, une seule fonderie en a besoin d'un dès la deuxième ligne. Les répartiteurs et les
   groupeurs sont donc des nœuds, posés et comptés là où ils sont, coût de construction compris. Un
   partage en arbre réel ne donne des parts égales que lorsque le nombre de lignes se ramène à des
   moitiés et des tiers : 2, 3, 4, 6, 9 oui, 5 et 7 non — et c'est le jeu qui est ainsi.
   En revanche **les convoyeurs et les tuyaux ne sont pas chiffrés** : leur coût se paie à la
   longueur, l'outil ne connaît aucune distance, et une estimation tirée d'une longueur moyenne
   serait un chiffre inventé posé au milieu de chiffres exacts. Un blanc qui se voit vaut mieux
   qu'un total qu'on croit complet.
11. **Une usine générée est une usine ordinaire.** « Deux cadres modulaires lourds par minute »
   se développe récursivement en machines, lignes et raccords, sans solveur et sans optimisation :
   recette standard partout sauf là où l'utilisateur en impose une autre, et le même résultat à
   l'octet près à chaque fois. Ce qui en sort se modifie, s'enregistre et s'annule comme le reste.
   La vérification est un **contrôle croisé** : l'usine est résolue par le moteur, et ce qu'elle
   consomme en matières premières est confronté au coût brut que la fiche de l'objet calcule par un
   tout autre chemin. Les deux tombent d'accord à la décimale sur quatre objets de profondeurs
   différentes.
12. **Le répartiteur standard est le cas particulier du programmable**, pas une seconde
   implémentation. Un programmable dont toutes les branches sont en « n'importe lequel » rend les
   mêmes chiffres au bit près, par le même code. Ce qui change les débits est le mode **surplus** :
   une branche qui ne prend que ce dont les autres n'ont pas voulu, servie en dernier par le même
   mécanisme que les puits illimités. Une ligne porte un objet, donc filtrer une branche sur autre
   chose la ferme — c'est signalé, pas deviné — et le jeu ne filtre que les convoyeurs : il n'y a
   pas de jonction de pipeline intelligente.
13. **La palette est un modèle, pas une liste d'objets.** Construire l'icône de chaque entrée à
    l'ouverture coûtait neuf millisecondes fois sept cents, soit une fenêtre figée neuf secondes.
    Qt ne demande au modèle que les lignes qu'il s'apprête à peindre.
14. **Le surcadençage est proportionnel sur le débit et exponentiel sur l'électricité.**
    L'exposant est **lu dans les données** (`mPowerConsumptionExponent`), jamais codé en dur : le
    jeu utilise 1,321929 pour tout ce qui produit et 1,6 ailleurs. À 250 %, une machine consomme
    donc environ 3,36 fois son nominal, et exactement 2,5 fois à 200 % — l'exposant vaut log₂(2,5),
    ce qui n'est pas un hasard. Le nombre d'éclats se déduit de `mExtraPotential` ; seul le nombre
    d'emplacements (trois) n'est pas exporté et vit dans `core/constants.py`, avec un test qui
    vérifie que la borne de 250 % reste égale à ce que trois éclats achètent réellement.
15. **Chaque champ modifiable n'a qu'une implémentation, pas trois qui s'accordent.**
    `ui/edits.py` porte la validation et la commande ; le menu contextuel, la cellule du tableau
    et le double-clic sur le nœud l'appellent tous les trois. La première version en avait deux,
    écrites pour donner le même résultat et vérifiées par un test — ce qui tient à trois champs
    et se casse au quatrième. Le test correspondant ne compare pas des résultats mais **les
    libellés des commandes empilées par les trois chemins** : c'est la seule chose qui puisse
    prouver qu'il n'y a bien qu'une porte.
16. **La consommation compte les machines à l'arrêt, la production ne compte que ce qui brûle.**
    L'asymétrie est volontaire. Côté consommation c'est un **dimensionnement au pire cas et non
    une mesure du jeu** : les fichiers ne déclarent qu'une consommation nominale par bâtiment et
    aucune consommation de veille — le seul second chiffre du jeu,
    `mEstimatedMininumPowerConsumption`, n'existe que sur les trois machines à puissance variable
    hors périmètre et désigne le bas de la plage d'une recette *en marche*. Inventer une valeur
    réduite serait pire que compter au maximum. Côté production, en revanche, la donnée est sans
    ambiguïté : un générateur sans carburant ne brûle rien et ne produit rien.
17. **L'électricité est comptée, jamais allouée.** C'est le seul endroit du projet où un
    diagnostic d'erreur ne se traduit pas par un taux réduit, et c'est délibéré : en jeu, un
    déficit ne ralentit pas l'usine, il déclenche une coupure générale jusqu'à intervention
    manuelle. Afficher « tout à zéro » n'apprendrait rien et un bridage partiel serait une
    invention. Le test qui compte n'est pas que les chiffres soient justes, c'est que la même
    usine résolue avec et sans assez de générateurs donne exactement les mêmes débits.
18. **L'écran de démarrage est un `QLabel`, pas un `QSplashScreen`.** Afficher un `QSplashScreen`
    coûte, mesuré, un peu plus d'une seconde sur cette plateforme, quelle que soit l'image : un
    écran d'attente qui rallonge l'attente n'est pas un écran d'attente. Un label sans cadre
    affichant la même image coûte seize millisecondes.

## Backlog V2

- Somersloop et amplification de production : autre formule, autre travail.
- Surcadençage des générateurs : l'exposant de production n'est pas celui de consommation, et la
  sémantique n'est pas la même. À traiter pour lui-même.
- Paliers supérieurs : Mélangeur, Convertisseur, Encodeur quantique, Accélérateur de particules,
  aluminium, azote, nucléaire — et le générateur géothermique, qui n'a pas d'intrant et dépend
  d'un emplacement de la carte.
- **Choix automatique des recettes alternatives** dans le mode objectif : le générateur suit la
  recette standard sauf indication contraire, et choisir la meilleure sous contrainte demande un
  programme linéaire, donc une dépendance.
- **Répartiteur intelligent à trois sorties filtrées** : le jeu en règle les trois, une par objet,
  et le programmable en accepte plusieurs par sortie. Le modèle actuel n'en règle qu'une pour
  l'intelligent et une valeur par sortie pour le programmable, ce qui est plus restrictif.
- **Groupeur prioritaire** : la réponse du jeu au répartiteur intelligent, côté groupage.
- Simulation temporelle des tampons, pour voir le film et pas seulement l'état final.
- Interopérabilité avec satisfactory-calculator.com.
- Internationalisation anglaise.

## Licence

Code sous licence MIT (voir `LICENSE`). Satisfactory, ses données et ses icônes sont la propriété
de Coffee Stain Studios et ne sont pas couverts par cette licence : voir `NOTICE.md`, qui distingue
les trois choses qui cohabitent dans ce dépôt et rappelle les obligations LGPL de Qt.
