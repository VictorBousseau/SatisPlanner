"""French written in French: the strings the user reads must carry their accents.

The catalogue's own labels were always right -- they come from the game's French
locale -- but the sentences written by hand in the source drifted into a sort of
accentless French: "1 unite(s) — debit fixe", "SatisPlanner a rencontre une erreur
inattendue", "Base de recettes chargee". Nobody writes like that, and a planner that
does looks unfinished before it has said anything.

The guard is a **dictionary of words that are wrong without their accent**, checked
against every string literal in the package. A dictionary rather than a clever
heuristic: French has plenty of unaccented words that look like accented ones
(``vide``, ``recette``, ``cadence``, ``hauteur``, ``tier``, ``inconnu``), and a rule
that guessed would either miss those or cry wolf on them. Adding a word here is one
line, and it is the line that stops the same mistake coming back.

Docstrings are exempt. They are developer documentation and this project writes them
in English, which is a deliberate choice and not an oversight.
"""

import ast
import re
from pathlib import Path

import pytest

import satisplanner

PACKAGE_ROOT = Path(satisplanner.__file__).parent
PROJECT_ROOT = PACKAGE_ROOT.parent

# Unaccented spelling -> what it should be. Every entry is a word that does not
# exist in French without its accent, so a match is always a defect.
#
# Words deliberately absent because they carry no accent and would be false alarms:
# cadence, recette, vide, hauteur, inconnu, tier, produite, tenable, possible,
# impossible, machine, purge, distance, surface.
ACCENTED: dict[str, str] = {
    "apres": "après",
    "assume": "assumé",
    "assumee": "assumée",
    "cle": "clé",
    "chargee": "chargée",
    "chargees": "chargées",
    "cree": "créé",
    "creee": "créée",
    "deduit": "déduit",
    "deduite": "déduite",
    "deduits": "déduits",
    "deficit": "déficit",
    "deja": "déjà",
    "depasse": "dépassé",
    "deplace": "déplacé",
    "deplacement": "déplacement",
    "deplacements": "déplacements",
    "deploye": "déployé",
    "deployee": "déployée",
    "deployees": "déployées",
    "deployes": "déployés",
    "debit": "débit",
    "debits": "débits",
    "detaille": "détaillé",
    "detaillee": "détaillée",
    "eclat": "éclat",
    "eclats": "éclats",
    "electricite": "électricité",
    "element": "élément",
    "elements": "éléments",
    "elevation": "élévation",
    "energie": "énergie",
    "enregistre": "enregistré",
    "enregistree": "enregistrée",
    "enregistrees": "enregistrées",
    "enregistres": "enregistrés",
    "entree": "entrée",
    "entrees": "entrées",
    "epuise": "épuisé",
    "epuisee": "épuisée",
    "etat": "état",
    "etats": "états",
    "ete": "été",
    "etre": "être",
    "francais": "français",
    "francaise": "française",
    "generateur": "générateur",
    "generateurs": "générateurs",
    "inutilisee": "inutilisée",
    "melange": "mélange",
    "modifie": "modifié",
    "modifiee": "modifiée",
    "modifiees": "modifiées",
    "modifies": "modifiés",
    "necessaire": "nécessaire",
    "numero": "numéro",
    "operation": "opération",
    "operations": "opérations",
    "parametre": "paramètre",
    "parametres": "paramètres",
    "peuplee": "peuplée",
    "piece": "pièce",
    "pieces": "pièces",
    "preference": "préférence",
    "preferences": "préférences",
    "probleme": "problème",
    "problemes": "problèmes",
    "propriete": "propriété",
    "proprietes": "propriétés",
    "purete": "pureté",
    "raccorde": "raccordé",
    "raccordee": "raccordée",
    "raccordees": "raccordées",
    "raccordes": "raccordés",
    "reference": "référence",
    "referencee": "référencée",
    "refuse": "refusé",
    "refusee": "refusée",
    "refusees": "refusées",
    "refuses": "refusés",
    "reseau": "réseau",
    "resolution": "résolution",
    "resolutions": "résolutions",
    "resultat": "résultat",
    "resultats": "résultats",
    "selectionne": "sélectionné",
    "selectionnee": "sélectionnée",
    "selectionnees": "sélectionnées",
    "selectionnes": "sélectionnés",
    "separe": "séparé",
    "serie": "série",
    "supprimee": "supprimée",
    "systeme": "système",
    "unite": "unité",
    "unites": "unités",
    "verification": "vérification",
    "verifications": "vérifications",
    "verifie": "vérifié",
    "verifiee": "vérifiée",
    "abandonne": "abandonné",
    "abime": "abîmé",
    "absorbee": "absorbée",
    "acceptee": "acceptée",
    "acceptes": "acceptés",
    "affichee": "affichée",
    "affichees": "affichées",
    "affiches": "affichés",
    "aligne": "aligné",
    "arete": "arête",
    "aretes": "arêtes",
    "arret": "arrêt",
    "arriere": "arrière",
    "assumes": "assumés",
    "batie": "bâtie",
    "baties": "bâties",
    "batiment": "bâtiment",
    "batiments": "bâtiments",
    "bloquee": "bloquée",
    "bouclee": "bouclée",
    "branchees": "branchées",
    "brule": "brûle",
    "brulent": "brûlent",
    "capacite": "capacité",
    "capacites": "capacités",
    "caracteres": "caractères",
    "codee": "codée",
    "compressee": "compressée",
    "compressees": "compressées",
    "concernee": "concernée",
    "condense": "condensé",
    "convergee": "convergée",
    "copiee": "copiée",
    "cosmetique": "cosmétique",
    "cout": "coût",
    "couts": "coûts",
    "credite": "crédité",
    "decimales": "décimales",
    "declaree": "déclarée",
    "declarees": "déclarées",
    "decoder": "décoder",
    "decompressee": "décompressée",
    "decompressees": "décompressées",
    "decrit": "décrit",
    "decrite": "décrite",
    "deduction": "déduction",
    "defaut": "défaut",
    "dela": "delà",
    "demarrage": "démarrage",
    "deposer": "déposer",
    "derniere": "dernière",
    "dernieres": "dernières",
    "deroger": "déroger",
    "derouler": "dérouler",
    "dessinee": "dessinée",
    "dessinees": "dessinées",
    "details": "détails",
    "duree": "durée",
    "durees": "durées",
    "echec": "échec",
    "echecs": "échecs",
    "echoue": "échoué",
    "ecran": "écran",
    "ecrasement": "écrasement",
    "ecrit": "écrit",
    "ecrite": "écrite",
    "ecriture": "écriture",
    "edite": "édite",
    "edition": "édition",
    "effacee": "effacée",
    "electrique": "électrique",
    "elevations": "élévations",
    "embarquee": "embarquée",
    "embarquees": "embarquées",
    "endommage": "endommagé",
    "entierement": "entièrement",
    "epuises": "épuisés",
    "equilibre": "équilibre",
    "etabli": "établi",
    "etablie": "établie",
    "etait": "était",
    "evenement": "événement",
    "evenements": "événements",
    "executable": "exécutable",
    "exploitee": "exploitée",
    "exportee": "exportée",
    "facon": "façon",
    "fenetre": "fenêtre",
    "fenetres": "fenêtres",
    "francaises": "françaises",
    "generee": "générée",
    "generees": "générées",
    "geometrie": "géométrie",
    "ignoree": "ignorée",
    "incoherente": "incohérente",
    "indetermine": "indéterminé",
    "indeterminee": "indéterminée",
    "indexee": "indexée",
    "indexees": "indexées",
    "integrite": "intégrité",
    "intermediaire": "intermédiaire",
    "intermediaires": "intermédiaires",
    "iteration": "itération",
    "iterations": "itérations",
    "libelle": "libellé",
    "libelles": "libellés",
    "marquee": "marquée",
    "marquees": "marquées",
    "masques": "masqués",
    "meme": "même",
    "memes": "mêmes",
    "metres": "mètres",
    "operationnel": "opérationnel",
    "passees": "passées",
    "perimetre": "périmètre",
    "prete": "prête",
    "puretes": "puretés",
    "rattrapee": "rattrapée",
    "recent": "récent",
    "recente": "récente",
    "recentes": "récentes",
    "recents": "récents",
    "reciproquement": "réciproquement",
    "reduisez": "réduisez",
    "reduite": "réduite",
    "reellement": "réellement",
    "referentielle": "référentielle",
    "regime": "régime",
    "regimes": "régimes",
    "rejouee": "rejouée",
    "repartiteur": "répartiteur",
    "repartiteurs": "répartiteurs",
    "reserves": "réserves",
    "resumer": "résumer",
    "retabli": "rétabli",
    "reussie": "réussie",
    "reussies": "réussies",
    "saturee": "saturée",
    "saturees": "saturées",
    "schema": "schéma",
    "selection": "sélection",
    "selectionner": "sélectionner",
    "surcadencage": "surcadençage",
    "surcadencee": "surcadencée",
    "surcadencees": "surcadencées",
    "surlignee": "surlignée",
    "theorique": "théorique",
    "theoriques": "théoriques",
    "torchere": "torchère",
    "tronque": "tronqué",
    "violee": "violée",
    "zero": "zéro",
}

# Case-insensitive, because a sentence starts with a capital and « Pieces » is as
# wrong as « pieces ». The capital is put back by :func:`accented`.
PATTERN = re.compile(
    r"b(" + "|".join(sorted(ACCENTED, key=len, reverse=True)) + r")b", re.IGNORECASE
)


def accented(word: str) -> str:
    """The correct spelling of ``word``, keeping the capital it was written with."""
    fixed = ACCENTED[word.lower()]
    return fixed[0].upper() + fixed[1:] if word[0].isupper() else fixed


def source_files() -> list[Path]:
    return sorted(PACKAGE_ROOT.rglob("*.py"))


def docstring_nodes(tree: ast.AST) -> set[int]:
    """Identity of every string node that is a docstring, so it can be skipped."""
    found: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        body = getattr(node, "body", [])
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            found.add(id(body[0].value))
    return found


def offences_in(path: Path) -> list[tuple[int, str, str]]:
    """``(line, word, text)`` for every unaccented French word in a real string."""
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(path))
    skip = docstring_nodes(tree)
    found: list[tuple[int, str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        if id(node) in skip:
            continue
        for word in PATTERN.findall(node.value):
            found.append((node.lineno, word, node.value[:80].replace("n", " ")))
    return found


@pytest.mark.parametrize("path", source_files(), ids=lambda path: path.name)
def test_no_string_is_missing_its_accents(path: Path) -> None:
    """Every hand-written French string in the package, checked word by word."""
    offences = offences_in(path)
    assert not offences, "n".join(
        f"  ligne {line} : « {word} » devrait s'ecrire « {accented(word)} » -- {text}"
        for line, word, text in offences
    )


@pytest.mark.parametrize("path", source_files(), ids=lambda path: path.name)
def test_every_source_file_is_utf_8(path: Path) -> None:
    """Accents are only safe if the file that holds them can be read back.

    Checked without the BOM tolerance the data layer needs: a source file with a
    byte-order mark is a source file some tools will mis-parse.
    """
    raw = path.read_bytes()
    assert not raw.startswith(b"xefxbbxbf"), "un fichier source ne doit pas porter de BOM"
    raw.decode("utf-8")


DOCUMENTS = ("README.md", "docs/format-usine.md")


@pytest.mark.parametrize("name", DOCUMENTS)
def test_the_documentation_is_written_in_french_too(name: str) -> None:
    """The README and the format guide are read by the same person as the interface."""
    path = PROJECT_ROOT / name
    if not path.is_file():
        pytest.skip(f"{name} absent")
    offences = [
        (number, word)
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        for word in PATTERN.findall(line)
    ]
    assert not offences, "n".join(
        f"  ligne {number} : « {word} » devrait s'ecrire « {accented(word)} »"
        for number, word in offences
    )
