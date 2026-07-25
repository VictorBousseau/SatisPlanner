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
lui parviennent par injection. `tests/test_architecture.py` vérifie cette règle par analyse statique
des imports et échoue sinon.

## Données du jeu

Les données de jeu et les icônes appartiennent à Coffee Stain Studios et **ne sont jamais
versionnées**. Elles sont extraites localement depuis l'installation du joueur.

À venir en phase 1 : `python -m satisplanner.data.build --game-dir "<installation>"`, qui génère
`satisplanner/resources/game_1.2.sqlite` depuis le dossier `CommunityResources/Docs`.

Les icônes vont dans `satisplanner/resources/icons/` (dossier ignoré par git). La procédure
d'extraction FModel sera documentée en phase 5. L'application reste **100 % fonctionnelle sans
icônes** : un fallback génératif dessine un carré coloré avec les initiales de l'item.

## Décisions de conception

Trois points ont été arbitrés au démarrage et gouvernent le moteur :

1. **Aucun chiffre deviné.** Les débits sont dérivés des données du jeu par des conversions
   centralisées, testées contre une table de valeurs de référence (convoyeurs 60 → 1200 items/min,
   tuyauteries 300 / 600 m³/min, foreuses 60 / 120 / 240, etc.). Si une formule dérive, le test
   échoue immédiatement au lieu de propager l'erreur.
2. **Blocage des sous-produits évalué sur la topologie, pas sur le débit.** Une machine est bloquée
   (taux = 0) uniquement s'il n'existe aucune sortie pour l'un de ses produits. Si une sortie existe
   mais n'absorbe qu'une fraction du débit, on applique une contre-pression continue (taux < 1) —
   ce qui reproduit le débit moyen réel du jeu. Le ratio de satisfaction est donc un `min()` sur les
   entrées **et** les sorties. Évaluer le blocage sur le débit rendrait le point fixe bistable.
3. **Le nombre de machines est une entrée**, saisie par l'utilisateur. Le moteur restitue en regard
   le nombre réellement utile compte tenu des intrants et l'écart. Le calcul descendant
   (« je veux 5 Ordinateurs/min ») relève de la V2.

## Licence

Code sous licence à définir. Satisfactory, ses données et ses icônes sont la propriété de Coffee
Stain Studios ; aucun logo ni élément de marque n'est reproduit dans cette application.
