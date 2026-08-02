"""French written in French: the strings the user reads must carry their accents.

The catalogue's own labels were always right -- they come from the game's French
locale -- but the sentences written by hand in the source had drifted into a sort of
accentless French: "1 unite(s) — debit fixe", "SatisPlanner a rencontre une erreur
inattendue", "Base de recettes chargee". Nobody writes like that, and a planner that
does looks unfinished before it has said anything.

The guard is a **dictionary of words that are wrong without their accent**, checked
against every string literal in the package. A dictionary rather than a clever
heuristic: French is full of unaccented words that look like accented ones --
``vide``, ``recette``, ``cadence``, ``hauteur``, ``inconnu``, ``palier`` -- and a rule
that guessed would either miss those or cry wolf on them. Adding a word is one line,
and it is the line that stops the same mistake coming back.

Two kinds of string are deliberately out of scope.

**Docstrings.** They are developer documentation and this project writes them in
English, which is a decision rather than an oversight.

**Identifiers that happen to be French.** A node's identifier prefix ends up in saved
files, a diagnostic code is serialised, and the self-check names real files on disk.
Those are listed in :data:`IDENTIFIERS` with the reason, and they are the only
exceptions there are.
"""

import ast
import re
from pathlib import Path

import pytest

import satisplanner

PACKAGE_ROOT = Path(satisplanner.__file__).parent
PROJECT_ROOT = PACKAGE_ROOT.parent

# Unaccented spelling -> the correct one. Every entry is a word that does not exist
# in French without its accent, so a match is always a defect. Words that are valid
# both ways -- "rencontre" the noun against "rencontré" the participle, "affiche",
# "masque", "bride" -- are absent on purpose and were read one by one instead.
ACCENTED: dict[str, str] = {
    "abime": "abîmé",
    "absorbee": "absorbée",
    "acceptee": "acceptée",
    "acceptes": "acceptés",
    "achevee": "achevée",
    "affichee": "affichée",
    "affichees": "affichées",
    "affiches": "affichés",
    "apres": "après",
    "arete": "arête",
    "aretes": "arêtes",
    "arret": "arrêt",
    "arriere": "arrière",
    "assumee": "assumée",
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
    "chargee": "chargée",
    "chargees": "chargées",
    "cle": "clé",
    "codee": "codée",
    "compressee": "compressée",
    "compressees": "compressées",
    "concernee": "concernée",
    "convergee": "convergée",
    "copiee": "copiée",
    "cosmetique": "cosmétique",
    "cout": "coût",
    "couts": "coûts",
    "cree": "créé",
    "creee": "créée",
    "debit": "débit",
    "debits": "débits",
    "decimales": "décimales",
    "declaree": "déclarée",
    "declarees": "déclarées",
    "declarent": "déclarent",
    "decoder": "décoder",
    "decompressee": "décompressée",
    "decompressees": "décompressées",
    "decrit": "décrit",
    "decrite": "décrite",
    "deduction": "déduction",
    "deduit": "déduit",
    "deduite": "déduite",
    "deduits": "déduits",
    "defaut": "défaut",
    "deficit": "déficit",
    "deja": "déjà",
    "dela": "delà",
    "demarrage": "démarrage",
    "deplacement": "déplacement",
    "deplacements": "déplacements",
    "deploye": "déployé",
    "deployee": "déployée",
    "deployees": "déployées",
    "deployes": "déployés",
    "deposer": "déposer",
    "derniere": "dernière",
    "dernieres": "dernières",
    "deroger": "déroger",
    "derouler": "dérouler",
    "dessinee": "dessinée",
    "dessinees": "dessinées",
    "detaille": "détaillé",
    "detaillee": "détaillée",
    "details": "détails",
    "different": "différent",
    "differente": "différente",
    "differentes": "différentes",
    "differents": "différents",
    "donnee": "donnée",
    "donnees": "données",
    "duree": "durée",
    "durees": "durées",
    "echec": "échec",
    "echecs": "échecs",
    "echoue": "échoué",
    "echouee": "échouée",
    "eclat": "éclat",
    "eclats": "éclats",
    "ecran": "écran",
    "ecrasement": "écrasement",
    "ecrit": "écrit",
    "ecrite": "écrite",
    "ecriture": "écriture",
    "edite": "édite",
    "edition": "édition",
    "effacee": "effacée",
    "electricite": "électricité",
    "electrique": "électrique",
    "element": "élément",
    "elements": "éléments",
    "elevation": "élévation",
    "elevations": "élévations",
    "embarquee": "embarquée",
    "embarquees": "embarquées",
    "energie": "énergie",
    "enregistree": "enregistrée",
    "enregistrees": "enregistrées",
    "enregistres": "enregistrés",
    "entierement": "entièrement",
    "entree": "entrée",
    "entrees": "entrées",
    "epuise": "épuisé",
    "epuisee": "épuisée",
    "epuises": "épuisés",
    "equilibre": "équilibre",
    "etabli": "établi",
    "etablie": "établie",
    "etait": "était",
    "etat": "état",
    "etats": "états",
    "ete": "été",
    "etendue": "étendue",
    "etre": "être",
    "evenement": "événement",
    "evenements": "événements",
    "executable": "exécutable",
    "exploitee": "exploitée",
    "exportee": "exportée",
    "facon": "façon",
    "fenetre": "fenêtre",
    "fenetres": "fenêtres",
    "francais": "français",
    "francaise": "française",
    "francaises": "françaises",
    "generateur": "générateur",
    "generateurs": "générateurs",
    "generee": "générée",
    "generees": "générées",
    "geometrie": "géométrie",
    "icone": "icône",
    "icones": "icônes",
    "ignoree": "ignorée",
    "incoherente": "incohérente",
    "incomplete": "incomplète",
    "incompletes": "incomplètes",
    "indetermine": "indéterminé",
    "indeterminee": "indéterminée",
    "indexee": "indexée",
    "indexees": "indexées",
    "integrite": "intégrité",
    "intermediaire": "intermédiaire",
    "intermediaires": "intermédiaires",
    "inutilisee": "inutilisée",
    "iteration": "itération",
    "iterations": "itérations",
    "libelle": "libellé",
    "libelles": "libellés",
    "marquee": "marquée",
    "marquees": "marquées",
    "masques": "masqués",
    "melange": "mélange",
    "meme": "même",
    "memes": "mêmes",
    "metres": "mètres",
    "modelisation": "modélisation",
    "modelise": "modélise",
    "modifiee": "modifiée",
    "modifiees": "modifiées",
    "modifies": "modifiés",
    "necessaire": "nécessaire",
    "numero": "numéro",
    "noeud": "nœud",
    "noeuds": "nœuds",
    "operation": "opération",
    "operationnel": "opérationnel",
    "operations": "opérations",
    "parametre": "paramètre",
    "parametres": "paramètres",
    "passees": "passées",
    "perimetre": "périmètre",
    "peuplee": "peuplée",
    "piece": "pièce",
    "pieces": "pièces",
    "prefere": "préféré",
    "preference": "préférence",
    "preferences": "préférences",
    "prete": "prête",
    "probleme": "problème",
    "problemes": "problèmes",
    "propriete": "propriété",
    "proprietes": "propriétés",
    "purete": "pureté",
    "puretes": "puretés",
    "quantite": "quantité",
    "quantites": "quantités",
    "raccordee": "raccordée",
    "raccordees": "raccordées",
    "raccordes": "raccordés",
    "rattrapee": "rattrapée",
    "recent": "récent",
    "recente": "récente",
    "recentes": "récentes",
    "recents": "récents",
    "reciproquement": "réciproquement",
    "reduisez": "réduisez",
    "reduite": "réduite",
    "reellement": "réellement",
    "reference": "référence",
    "referencee": "référencée",
    "referentielle": "référentielle",
    "refusee": "refusée",
    "refusees": "refusées",
    "refuses": "refusés",
    "regime": "régime",
    "regimes": "régimes",
    "rejouee": "rejouée",
    "renomme": "renommé",
    "reordonne": "réordonné",
    "reouverture": "réouverture",
    "repartiteur": "répartiteur",
    "repartiteurs": "répartiteurs",
    "representee": "représentée",
    "representer": "représenter",
    "reseau": "réseau",
    "reserves": "réserves",
    "resolution": "résolution",
    "resolutions": "résolutions",
    "resultat": "résultat",
    "resultats": "résultats",
    "resumer": "résumer",
    "retabli": "rétabli",
    "reussi": "réussi",
    "reussie": "réussie",
    "reussies": "réussies",
    "saturee": "saturée",
    "saturees": "saturées",
    "schema": "schéma",
    "selection": "sélection",
    "selectionnee": "sélectionnée",
    "selectionnees": "sélectionnées",
    "selectionner": "sélectionner",
    "selectionnes": "sélectionnés",
    "serie": "série",
    "supprimee": "supprimée",
    "surcadencage": "surcadençage",
    "surcadencee": "surcadencée",
    "surcadencees": "surcadencées",
    "surlignee": "surlignée",
    "systeme": "système",
    "theorique": "théorique",
    "theoriques": "théoriques",
    "torchere": "torchère",
    "unite": "unité",
    "unites": "unités",
    "verification": "vérification",
    "verifications": "vérifications",
    "verifiee": "vérifiée",
    "violee": "violée",
}

# Strings that are identifiers rather than prose, and must keep their spelling.
IDENTIFIERS: dict[str, frozenset[str]] = {
    # Node identifier prefixes: they are written into every saved factory, and the
    # table shows them. Renaming them would change what a new document looks like on
    # disk for no reader's benefit.
    "canvas.py": frozenset({"generateur", "entree", "repartiteur"}),
    # The generator writes the same prefixes into the factories it produces.
    "planner.py": frozenset({"entree", "entree-"}),
    # A serialised diagnostic code, read back from reports.
    "results.py": frozenset({"deficit"}),
    # Real files the self-check writes next to the executable.
    "self_check.py": frozenset(
        {"verification", "verification.sfp", "verification.png", "verification.pdf"}
    ),
}

# Case-insensitive, because a sentence starts with a capital and « Pieces » is as
# wrong as « pieces ». The capital is put back by :func:`accented`.
PATTERN = re.compile(
    r"\b(" + "|".join(sorted(ACCENTED, key=len, reverse=True)) + r")\b", re.IGNORECASE
)


def accented(word: str) -> str:
    """The correct spelling of ``word``, written the way the original was.

    Three cases, and the third is the one that bites: a banner shouting « CES DÉBITS
    NE SONT PAS TENABLES » must not come back as « CES Débits NE SONT PAS TENABLES ».
    """
    fixed = ACCENTED[word.lower()]
    if word.isupper():
        return fixed.upper()
    return fixed[0].upper() + fixed[1:] if word[0].isupper() else fixed


def source_files() -> list[Path]:
    """The package, and the command-line tools beside it.

    The tools are developer-facing, but they speak French on a console and there is
    no reason for that French to be worse than the application's.
    """
    return sorted(PACKAGE_ROOT.rglob("*.py")) + sorted((PROJECT_ROOT / "tools").rglob("*.py"))


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
    exempt = IDENTIFIERS.get(path.name, frozenset())
    found: list[tuple[int, str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        if id(node) in skip or node.value in exempt:
            continue
        for word in PATTERN.findall(node.value):
            found.append((node.lineno, word, node.value[:80].replace("\n", " ")))
    return found


@pytest.mark.parametrize("path", source_files(), ids=lambda path: path.name)
def test_no_string_is_missing_its_accents(path: Path) -> None:
    """Every hand-written French string in the package, checked word by word."""
    offences = offences_in(path)
    assert not offences, "\n".join(
        f"  ligne {line} : « {word} » devrait s'écrire « {accented(word)} » -- {text}"
        for line, word, text in offences
    )


@pytest.mark.parametrize("path", source_files(), ids=lambda path: path.name)
def test_every_source_file_is_utf_8(path: Path) -> None:
    """Accents are only safe if the file that holds them can be read back.

    Checked without the tolerance the data layer needs for the game's own files: a
    source file carrying a byte-order mark is one some tools will mis-parse.
    """
    raw = path.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf"), "un fichier source ne doit pas porter de BOM"
    raw.decode("utf-8")


DOCUMENTS = ("README.md", "docs/format-usine.md")

# File names in this repository are deliberately ASCII, so a document that links to
# ``docs/migration-repartiteurs.md`` is not misspelling a French word -- it is naming
# a path. Stripping paths keeps the check on the prose, which is the whole point of
# it; anything left outside a path is still read word by word.
_PATH_LIKE = re.compile(r"[\w./-]+\.(?:md|json|py|ps1|sqlite|sfp|sfm|png|pdf)\b")


def prose_of(line: str) -> str:
    """The line without the file names in it."""
    return _PATH_LIKE.sub(" ", line)


@pytest.mark.parametrize("name", DOCUMENTS)
def test_the_documentation_is_written_in_french_too(name: str) -> None:
    """The README and the format guide are read by the same person as the interface."""
    path = PROJECT_ROOT / name
    if not path.is_file():
        pytest.skip(f"{name} absent")
    offences = [
        (number, word)
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        for word in PATTERN.findall(prose_of(line))
    ]
    assert not offences, "\n".join(
        f"  ligne {number} : « {word} » devrait s'écrire « {accented(word)} »"
        for number, word in offences
    )


def test_the_dictionary_itself_is_spelled_correctly() -> None:
    """Every replacement must actually carry an accent, or it fixes nothing."""
    for wrong, right in ACCENTED.items():
        assert wrong != right, wrong
        assert any(letter in right for letter in "éèêëàâçùûôîïœ"), (
            f"« {right} » ne porte ni accent ni ligature"
        )
        assert not right.isascii(), (
            f"« {right} » ne sort pas de l'ASCII : il n'a rien a faire dans le dictionnaire"
        )
