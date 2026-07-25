# SatisPlanner

Planificateur d'usines **théoriques** pour Satisfactory 1.2. On pose des nœuds sur un canvas
(gisement → fonderie → constructeur → assembleuse, pétrole → raffinerie…), on les relie par des
convoyeurs et des tuyauteries, et l'application calcule en régime permanent : débits, nombre de
machines, goulots d'étranglement, saturation des lignes, consommation électrique et liste des
bâtiments à construire.

Ce n'est **ni un mod ni un lecteur de sauvegarde**. Aucune interaction avec le jeu en cours
d'exécution, aucun accès réseau au runtime.

## Ce que l'outil ne fait pas

- **Il raisonne en débits, pas en géométrie 3D.** Aucune notion de distance, d'élévation, ni de
  hauteur de refoulement des pompes. Un tuyau dont le débit théorique passe sera déclaré valide même
  si, en jeu, il faudrait une pompe.
- Pas de surcadençage / sous-cadençage, pas de Somersloop (prévu en V2).
- Pas de génération d'électricité : seule la consommation est calculée (V2).
- Hors périmètre V1 : Mélangeur, Convertisseur, Encodeur quantique, Accélérateur de particules,
  nucléaire.

## Installation (développement)

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

## Architecture

```
ui  -->  core  <--  data
```

`core/` est un domaine pur : il n'importe jamais Qt et ne lit jamais la base de données. Les données
lui parviennent par injection (`data.db.load_game_data` produit le catalogue `core.models.GameData`).
`tests/test_architecture.py` vérifie cette règle par analyse statique des imports et échoue sinon.

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
ligne** — qui donne le débit qu'une ligne *voudrait* porter, et donc le tier à installer — et, quand
un tampon se vide, la même paire résolue une seconde fois avec les tampons ne fournissant rien.
C'est ce second jeu de chiffres que porte `FactoryReport.sustained`.

## L'interface

Palette à gauche, canvas au centre, emplacements réservés à droite pour les panneaux de la phase 4.

- **Palette** : recherche insensible aux accents, filtre par machine, bascules pour les recettes
  alternatives et pour les objets d'événement (masqués par défaut), et le tier par défaut des
  nouvelles lignes. Glisser-déposer vers le canvas, ou double-clic pour poser au centre de la vue.
- **Canvas** : zoom molette, pan au clic milieu, grille magnétique. Une connexion se tire d'un port
  de sortie vers un port d'entrée ; **une liaison impossible est refusée pendant le tirage**, le
  trait devenant rouge avec la raison en infobulle, et non signalée après coup.
- **Nœuds** : icône, libellé français, recette, nombre de machines, débits d'entrée et de sortie, et
  un liseré vert / orange / rouge. Clic droit : « ajuster ce nœud », qui appelle
  `engine.suggest_machine_count` — un calcul local sur un nœud, jamais une optimisation globale.
- **Lignes** : les tuyauteries sont plus épaisses et bleues, les convoyeurs fins et pâles. Une ligne
  saturée passe en rouge pointillé et propose son tier suffisant au clic droit.
- **Annulation** : toutes les opérations sans exception passent par une `QUndoCommand`, y compris
  les déplacements. Le moteur est relancé après chaque changement, avec un regroupement de 120 ms
  pour ne pas résoudre le graphe à chaque pixel d'un glissement.

Trois panneaux à droite, alimentés par le même rapport que le canvas :

- **Tableau** : un nœud par ligne, tri et filtre, sélection synchronisée dans les deux sens avec le
  canvas, et une colonne de quantité éditable — dont l'édition passe par la pile d'annulation
  comme le reste.
- **Totaux** : matières brutes, fluides et sous-produits, électricité, puis liste de courses.
  Quand l'usine vit sur un stock, un bandeau rouge et deux colonnes de chiffres — « avec les
  stocks » et « régime établi » — remplacent le silence qui laisserait croire à une réussite.
- **Diagnostics** : triés par niveau, filtrables, et **cliquables** : sélectionner une ligne
  sélectionne et centre le nœud ou la ligne concernée. Quand un diagnostic nomme une correction,
  un bouton l'applique depuis la ligne même.

## Fichiers et partage

Une usine s'enregistre en `.sfp` : une archive ZIP contenant `factory.json`, un `manifest.json`
(version de l'application, version des données de jeu, date, version de schéma) et une vignette
`thumbnail.png`. `Ctrl+S`, `Ctrl+O`, `Ctrl+N`, liste des fichiers récents, indicateur de
modification dans le titre et confirmation avant de perdre du travail.

Le même graphe se partage en une ligne de texte, `SFP1:<base64url(zlib(json))>`, avec « copier le
code » et « importer depuis un code ». Un code tronqué, corrompu, mal collé ou venu d'une version
future est refusé par une phrase en français — jamais par une trace d'exécution.

`satisplanner/data/factory_file.py` porte aussi le **point d'entrée unique de migration** :
`migrate(payload, schema_version)` fait remonter un document une version à la fois jusqu'à la
version courante. Il n'a rien à faire aujourd'hui ; il existe pour que le jour où il aura quelque
chose à faire, il n'y ait qu'un endroit où l'écrire et qu'un endroit où le tester.

Exports : PNG du canvas, PDF avec le canvas en première page et, au choix, les totaux et les
diagnostics en seconde.

## Données du jeu

**L'application est autonome.** La base SQLite est générée une fois à la conception, versionnée, et
embarquée dans l'exe : elle fonctionne sur une machine où Satisfactory n'est pas installé, sans
configuration.

Régénération (outil de maintenance, à relancer quand le jeu change de version) :

```bash
.venv/Scripts/python.exe -m satisplanner.data.build --game-dir "C:\Program Files (x86)\Steam\steamapps\common\Satisfactory"
```

Le CLI découvre seul son fichier source dans `CommunityResources/Docs` : `en-US.json` en référence
de structure, `fr.json` pour les libellés, avec repli sur une autre variante anglaise puis sur
`Docs.json`. Aucun nom de fichier n'est codé en dur, et le fichier retenu est affiché.

Fixture de test : `tools/extract_fixture.py` extrait des mêmes fichiers une tranche de quelques
centaines de kilo-octets vers `tests/fixtures/`, en conservant l'encodage UTF-16 LE et le BOM
d'origine — les tests traversent donc le même chemin de décodage que la production.

Les icônes appartiennent à Coffee Stain Studios : `satisplanner/resources/icons/` est ignoré par git
et reconstituable par extraction FModel (procédure documentée en phase 5). L'application reste
**100 % fonctionnelle sans icônes** : `ui/icon_provider.py` essaie le dossier embarqué, puis un
dossier utilisateur, puis dessine lui-même un carré arrondi dont la teinte vient d'un hachage stable
du nom de classe, avec les initiales du libellé français au centre. Ce troisième chemin est le
fonctionnement nominal — l'exe distribué ne contient aucune icône de bâtiment — et non une
dégradation.

## Décisions de conception

Trois points ont été arbitrés au démarrage et gouvernent le moteur :

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
   ce qui reproduit le débit moyen réel du jeu. Le ratio de satisfaction est donc un `min()` sur les
   entrées **et** les sorties. Évaluer le blocage sur le débit rendrait le point fixe bistable.
3. **Le nombre de machines est une entrée**, saisie par l'utilisateur. Le moteur restitue en regard
   le nombre réellement utile compte tenu des intrants et l'écart. Le calcul descendant
   (« je veux 5 Ordinateurs/min ») relève de la V2.
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
   recette dans le catalogue et sont en droit de l'y trouver, et affaiblir cette garantie partout
   pour accommoder un fichier d'une autre version du jeu coûterait plus qu'elle ne rapporte. Ce
   que l'utilisateur risquait de perdre — la disposition de tout le reste — est préservé.
9. **Répartiteurs, groupeurs et jonctions ne sont pas des nœuds.** Un nœud à trois lignes sortantes
   se dessine comme trois lignes, ce qui est la façon dont le joueur y pense. Ils restent des
   bâtiments à construire et sont comptés dans la liste de courses, déduits du nombre de lignes qui
   partagent un nœud. Leur débit n'est pas modélisé : un répartiteur passe 2000 items/min quand le
   meilleur convoyeur plafonne à 1200, il ne peut donc jamais être le goulot.

## Licence

Code sous licence à définir. Satisfactory, ses données et ses icônes sont la propriété de Coffee
Stain Studios ; aucun logo ni élément de marque n'est reproduit dans cette application.
