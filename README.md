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
`models → graph → results → validation → engine` : les diagnostics lisent un rapport résolu sans
jamais calculer de débit, et le moteur les appelle en fin de résolution.

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
**100 % fonctionnelle sans icônes** : un fallback génératif dessine un carré coloré avec les
initiales de l'item.

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
5. **La capacité des lignes est diagnostiquée, jamais imposée.** Un convoyeur Mk.1 traversé par
   480/min affiche 480/min et un avertissement proposant le Mk.4. Les débits restent ceux de la
   production, ce qui permet de voir *ce qu'il faudrait* transporter.

## Licence

Code sous licence à définir. Satisfactory, ses données et ses icônes sont la propriété de Coffee
Stain Studios ; aucun logo ni élément de marque n'est reproduit dans cette application.
