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

Beside it sits a **second, smaller dictionary of whole phrases**, :data:`PHRASES`, for
the cases the first one cannot take. "Surplus non consomme" is wrong and *la machine
consomme* is right; "sous-produit bloque" is wrong and *le répartiteur bloque tout* is
right. The word cannot be listed without breaking the correct sentence, so the phrase
is listed instead. It catches a repeat rather than a new mistake of the same shape,
and that limit is stated here rather than left for someone to discover.

Two kinds of string are deliberately out of scope.

**Docstrings.** They are developer documentation and this project writes them in
English, which is a decision rather than an oversight.

**Identifiers that happen to be French.** A node's identifier prefix ends up in saved
files, a diagnostic code is serialised, and the self-check names real files on disk.
Those are listed in :data:`IDENTIFIERS` with the reason, and they are the only
exceptions there are.

**The ``{fields}`` of a translatable sentence.** Since the messages go through
``_("…").format(…)``, a field name now sits inside the French text -- and a field
name is a Python identifier, written in ASCII like every other identifier in this
package. ``{iterations}`` is not the word *itérations* spelled wrong, it is the name
of an argument, so the placeholders are removed before the text is read. The word
next to them is still checked, which is what the guard is for.
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
    "batir": "bâtir",
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
    "commencant": "commençant",
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
    "decompresse": "décompresse",
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
    "definitivement": "définitivement",
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
    "echap": "échap",
    "echec": "échec",
    "echecs": "échecs",
    "echoue": "échoué",
    "echouee": "échouée",
    "eclat": "éclat",
    "eclats": "éclats",
    "ecran": "écran",
    "ecrasement": "écrasement",
    "ecraser": "écraser",
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
    "noeud": "nœud",
    "noeuds": "nœuds",
    "numero": "numéro",
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
    "precedent": "précédent",
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
    "rejete": "rejeté",
    "rejouee": "rejouée",
    "relachez": "relâchez",
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
    "verifiez": "vérifiez",
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

# The other half of the guard: the cases where the **word** is perfectly good French
# and the **phrase** is not.
#
# "consomme", "bloque", "donne", "recycle", "conserves", "indexe" and the bare "a" are
# all valid: *la machine consomme*, *le répartiteur bloque tout*, *un objectif se donne
# en objets par minute*. None of them can join :data:`ACCENTED` without the guard
# crying wolf on a correct sentence -- which is exactly the trap its docstring warns
# about. So the phrase is what is listed, and it is listed because it was actually
# written: every entry below was found in the interface and corrected.
#
# This catches a copy of the same mistake rather than a new one of the same shape. A
# word list cannot do better here, and pretending otherwise would be worse than the
# gap: a guard nobody trusts is a guard nobody reads.
PHRASES: dict[str, str] = {
    "Surplus non consomme": "Surplus non consommé",
    "minerai consomme": "minerai consommé",
    "fluide brut consomme": "fluide brut consommé",
    "sous-produit bloque": "sous-produit bloqué",
    "bloque par": "bloqué par",
    "est donne en regard": "est donné en regard",
    "recents conserves": "récents conservés",
    "recycle {": "recyclé {",
    "refoules": "refoulés",
    "ne crédité pas": "ne crédite pas",
    "A propos": "À propos",
    "Rien a exporter": "Rien à exporter",
    "indexe(s)": "indexé(s)",
}

# Case-insensitive, because a sentence starts with a capital and « Pieces » is as
# wrong as « pieces ». The capital is put back by :func:`accented`.
PATTERN = re.compile(
    r"\b(" + "|".join(sorted(ACCENTED, key=len, reverse=True)) + r")\b", re.IGNORECASE
)

# ``{item}``, ``{iterations}``, ``{ratio}``: the named fields of a sentence that goes
# through ``.format()``. Replaced by a space rather than deleted, so the words on
# either side keep their boundaries and stay checked.
PLACEHOLDER = re.compile(r"\{[^{}]*\}")


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
        prose = PLACEHOLDER.sub(" ", node.value)
        for word in PATTERN.findall(prose):
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
def test_no_string_repeats_a_phrase_we_already_corrected(path: Path) -> None:
    """The half of the guard a word list cannot cover.

    Every phrase in :data:`PHRASES` was written in this interface at some point,
    read as French, and shipped, because the word that was wrong is a word that is
    right somewhere else. Listing the phrase is the only way to make the same one
    fall the next time it is written.
    """
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(path))
    skip = docstring_nodes(tree)
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        if id(node) in skip:
            continue
        for wrong, right in PHRASES.items():
            if wrong in node.value:
                found.append(f"  ligne {node.lineno} : « {wrong} » -- écrire « {right} »")
    assert not found, "\n".join(found)


@pytest.mark.parametrize("path", source_files(), ids=lambda path: path.name)
def test_every_source_file_is_utf_8(path: Path) -> None:
    """Accents are only safe if the file that holds them can be read back.

    Checked without the tolerance the data layer needs for the game's own files: a
    source file carrying a byte-order mark is one some tools will mis-parse.
    """
    raw = path.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf"), "un fichier source ne doit pas porter de BOM"
    raw.decode("utf-8")


# The French documents, and only those. ``README.md`` is the English one and would
# fail on every second word -- *ingredients*, *reference*, *deficit*, *unite* are all
# perfectly correct English and all in the dictionary of French words that are wrong
# without their accent. The rule this file enforces is "French written in French",
# so what it must scan is the French.
DOCUMENTS = ("README.fr.md", "docs/format-usine.md")

# File names in this repository are deliberately ASCII, so a document that links to
# ``docs/migration-repartiteurs.md`` is not misspelling a French word -- it is naming
# a path. Stripping paths keeps the check on the prose, which is the whole point of
# it; anything left outside a path is still read word by word.
_PATH_LIKE = re.compile(r"[\w./-]+\.(?:md|json|py|ps1|sqlite|sfp|sfm|png|pdf)\b")

# Backticked spans are code, not prose: a field name, a class name, an identifier
# prefix. `entree-` is not the word *entrée* spelled wrong, it is the first half of
# an identifier that ends up in a saved file and is deliberately ASCII. Same
# reasoning as the ``{fields}`` of a translatable sentence, one section up.
_CODE_SPAN = re.compile(r"`[^`]*`")


def prose_of(line: str) -> str:
    """The line without the file names and the code spans in it."""
    return _PATH_LIKE.sub(" ", _CODE_SPAN.sub(" ", line))


def test_the_two_readmes_point_at_each_other() -> None:
    """Somebody landing on either one has to find the other in the first two lines.

    GitHub shows ``README.md``, so that is the English one and the one a visitor
    from elsewhere meets first. The French one is a click away and says so, and the
    English one says so too -- a project whose front page is in a language you do
    not read is a project you close.
    """
    english = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8").splitlines()
    french = (PROJECT_ROOT / "README.fr.md").read_text(encoding="utf-8").splitlines()
    assert "(README.fr.md)" in "".join(english[:4])
    assert "(README.md)" in "".join(french[:4])


def test_no_readme_still_announces_a_french_only_interface() -> None:
    """The sentence that got a post refused, and that must not come back.

    This replaces a test that asserted the opposite, and the reversal is the point.
    While the interface really was French, ``README.md`` said so above the fold and
    the check made sure it kept saying so -- somebody who reads that and walks away
    is a user who may come back, where somebody who downloads 129 MB to find an
    interface they cannot read never does.

    The day the catalogue reached 659/659 that sentence became false, and it stayed
    on the page. A moderator of r/SatisfactoryGame read the page rather than the
    application and refused the post for an interface that had been bilingual for a
    week. **A front page is read instead of the thing it describes**, so a claim
    about the state of the work has to fall the moment it stops being true -- which
    is what this now checks, in both languages.

    A presence check and not a position check: where the section sits is a matter of
    taste now that it states a fact instead of warning about a gap.

    The phrases are listed exactly as they were written, so this catches the same
    claim coming back and not a fresh wording of it -- the same limit, and the same
    reason, as :data:`PHRASES` above. What holds the positive side up is
    :func:`test_both_readmes_say_the_interface_is_bilingual`. Note that "Developer
    documentation stays French only" is **not** in this list: it is about the docs,
    it is still true, and a guard that fired on it would be a guard somebody turns
    off.
    """
    stale = (
        "The interface is still in French",
        "Menus, panels, diagnostics, node faces, help page",
        "French only, for now",
        "**Interface anglaise** : en cours",
        "The English translation is under way",
    )
    for name in ("README.md", "README.fr.md"):
        text = (PROJECT_ROOT / name).read_text(encoding="utf-8")
        for phrase in stale:
            assert phrase not in text, f"{name} annonce encore « {phrase} »"


def test_both_readmes_say_the_interface_is_bilingual() -> None:
    """And each in its own language, in a section a reader can find.

    The English page is what a visitor from Reddit meets: if it does not say the
    application speaks English, nothing else on it will be read.
    """
    # Read with the line breaks flattened: these documents wrap at 100 columns, and
    # the menu's name falls across two lines in one of them.
    english = " ".join((PROJECT_ROOT / "README.md").read_text(encoding="utf-8").split())
    assert "## Language" in english
    assert "fully bilingual" in english
    assert "Langue / Language" in english

    french = " ".join((PROJECT_ROOT / "README.fr.md").read_text(encoding="utf-8").split())
    assert "## Langue" in french
    assert "entièrement bilingue" in french
    assert "Langue / Language" in french


@pytest.mark.parametrize("name", DOCUMENTS)
def test_the_documentation_is_written_in_french_too(name: str) -> None:
    """The French README and the format guide are read by the same person as the interface."""
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
