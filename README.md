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
- **Pas de génération d'électricité.** Seule la consommation est calculée. (V2)
- **Répartiteurs, groupeurs et jonctions ne sont pas des nœuds.** Ils sont comptés dans la liste
  de courses, jamais dessinés et jamais un goulot — un répartiteur passe 2000 items/min quand le
  meilleur convoyeur plafonne à 1200.
- **Hors périmètre V1** : Mélangeur, Convertisseur, Encodeur quantique, Accélérateur de
  particules, nucléaire, extracteur de puits de ressources, Clean Pipeline.
- **Le nombre de machines est une saisie**, pas un résultat. « Je veux 5 Ordinateurs par minute,
  dimensionne l'usine » est le mode descendant, et il relève de la V2.

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

Palette à gauche, canvas au centre, trois panneaux à droite.

- **Palette** : recherche insensible aux accents et par mots indépendants (« alt plaque » trouve
  « Alternative : plaque de fer moulée »), filtre par machine, bascules pour les recettes
  alternatives et les objets d'événement, et le tier par défaut des nouvelles lignes.
  Glisser-déposer vers le canvas ; **double-clic pour ouvrir la fiche** de l'objet.
- **Fiche d'objet** : double-clic dans la palette, ou clic droit ▸ « Fiche de… » sur un nœud.
  Description du jeu, forme, taille de pile, points au collecteur AWESOME, puis toutes les recettes
  qui le fabriquent — machine, durée de cycle, quantités par cycle et débits par minute,
  sous-produits, électricité — puis celles qui le consomment, puis le coût en ressources brutes.
  Chaque ingrédient est un lien vers sa propre fiche, avec précédent et suivant : on suit une
  chaîne de fabrication comme on suit un wiki. Chaque recette porte un bouton « poser sur le
  canvas ». Le coût en minerai est **indicatif et le dit** : il ne suit que les recettes standard
  et ne crédite pas les sous-produits.
- **Cadence** : chaque extracteur et chaque machine se règle de 1 % à 250 % (clic droit ▸
  « Cadence… », ou la colonne du tableau). Un nœud dont la cadence n'est pas 100 % l'affiche en
  toutes lettres. Le débit suit la cadence exactement ; l'électricité suit une loi de puissance,
  et les éclats de charge nécessaires apparaissent dans la liste de courses.
- **Canvas** : une connexion se tire d'un port de sortie vers un port d'entrée. **Une liaison
  impossible est refusée pendant le tirage** — le trait devient rouge avec la raison en infobulle
  — et non signalée après coup. Clic droit sur un nœud pour l'ajuster à ses intrants ou fixer son
  nombre de machines ; clic droit sur une ligne pour changer de tier ou passer au tier suffisant.
- **Tableau** : un nœud par ligne, tri, filtre, sélection synchronisée dans les deux sens avec le
  canvas, colonne « Quantité » éditable — machines, extracteurs, débit d'un apport externe ou
  stock initial d'un tampon selon le type de nœud — et colonne « Cadence », éditable elle aussi.
- **Totaux** : matières brutes, fluides et sous-produits, électricité, liste de courses. Quand
  l'usine vit sur un stock, un bandeau rouge et deux colonnes de chiffres — « avec les stocks » et
  « régime établi » — remplacent le silence qui laisserait croire à une réussite.
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
9. **Répartiteurs, groupeurs et jonctions ne sont pas des nœuds.** Un nœud à trois lignes sortantes
   se dessine comme trois lignes, ce qui est la façon dont le joueur y pense. Ils restent des
   bâtiments à construire et sont comptés dans la liste de courses.
10. **La palette est un modèle, pas une liste d'objets.** Construire l'icône de chaque entrée à
    l'ouverture coûtait neuf millisecondes fois sept cents, soit une fenêtre figée neuf secondes.
    Qt ne demande au modèle que les lignes qu'il s'apprête à peindre.
11. **Le surcadençage est proportionnel sur le débit et exponentiel sur l'électricité.**
    L'exposant est **lu dans les données** (`mPowerConsumptionExponent`), jamais codé en dur : le
    jeu utilise 1,321929 pour tout ce qui produit et 1,6 ailleurs. À 250 %, une machine consomme
    donc environ 3,36 fois son nominal, et exactement 2,5 fois à 200 % — l'exposant vaut log₂(2,5),
    ce qui n'est pas un hasard. Le nombre d'éclats se déduit de `mExtraPotential` ; seul le nombre
    d'emplacements (trois) n'est pas exporté et vit dans `core/constants.py`, avec un test qui
    vérifie que la borne de 250 % reste égale à ce que trois éclats achètent réellement.
12. **L'écran de démarrage est un `QLabel`, pas un `QSplashScreen`.** Afficher un `QSplashScreen`
    coûte, mesuré, un peu plus d'une seconde sur cette plateforme, quelle que soit l'image : un
    écran d'attente qui rallonge l'attente n'est pas un écran d'attente. Un label sans cadre
    affichant la même image coûte seize millisecondes.

## Backlog V2

- Somersloop et amplification de production : autre formule, autre travail.
- Génération d'électricité, et paliers supérieurs (Mélangeur, Convertisseur, Encodeur quantique,
  Accélérateur de particules, nucléaire).
- **Mode objectif descendant** : « je veux tant d'Ordinateurs par minute », résolu par un solveur
  linéaire plutôt que par le point fixe actuel.
- Répartiteurs intelligents et programmables.
- Mode « déployer » affichant l'arbre de répartiteurs qu'un nœud à n sorties implique réellement.
- Simulation temporelle des tampons, pour voir le film et pas seulement l'état final.
- Interopérabilité avec satisfactory-calculator.com.
- Internationalisation anglaise.

## Licence

Code sous licence MIT (voir `LICENSE`). Satisfactory, ses données et ses icônes sont la propriété
de Coffee Stain Studios et ne sont pas couverts par cette licence : voir `NOTICE.md`, qui distingue
les trois choses qui cohabitent dans ce dépôt et rappelle les obligations LGPL de Qt.
