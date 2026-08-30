"""One door for every sentence this application writes itself.

Not for the game's own words. Item names, recipe names and building names come from
``fr.json`` and ``en-US.json`` and are chosen in :mod:`satisplanner.data.db`; nothing
here ever translates a term of Satisfactory. "Façonneuse" becomes *Manufacturer*
because the game says so, never because it is the translation.

Why a dictionary rather than Qt Linguist
----------------------------------------
``core`` never imports Qt. That is the rule the whole layering rests on, and it is
enforced by an ``ast`` test rather than by good intentions. But ``core`` is where
diagnostics, the planner's report and every number are written -- a good half of what
a user reads. ``QObject.tr`` cannot go there, so Qt Linguist would have meant **two**
mechanisms: ``.ts`` files for the interface and something else for the domain. One
door is worth more than the standard tooling, and this is the door.

What it costs is a build step nobody has to run and a ``.qm`` nobody has to embed.
What it costs *instead* is that the plural rules and the context disambiguation Qt
gives for free are not here. Two languages and no plural-sensitive sentence make that
a fair trade; a third language, or a Slavic one, would be the moment to reconsider.

Why the French sentence is the key
----------------------------------
``_("Aucune recette posable dans cette version.")`` rather than
``_("card.no_placeable_recipe")``. The code stays readable in the language it was
written in, a diff still shows what a message says, and there is no second name to
keep in step with the first.

The usual objection to source-string keys is that a typo in the source silently
un-translates a sentence. That does not apply here: :mod:`tests.test_i18n` walks the
package with ``ast``, collects every literal handed to :func:`_`, and fails when one
of them is missing from the catalogue. A typo breaks the suite instead of shipping.

The second consequence is the one that made the accent guard easy. **English is data,
never code**: it lives in ``resources/en.json`` and no English sentence is ever
written in a ``.py`` file. The guard that checks French accents scans Python string
literals, so it keeps scanning exactly what it always did -- French -- and needed no
exemption list at all.

Interpolation
-------------
No f-strings. A key has to be a literal, so a sentence with a value in it is written
``_("Déficit de {item}").format(item=name)`` with **named** fields, never positional
ones -- English and French do not always want the values in the same order. The
``ast`` test enforces both: the argument of :func:`_` must be a constant, and the
fields of a translated sentence must match between the two languages.
"""

import ast
import json
import logging
from enum import StrEnum
from pathlib import Path
from typing import Final

logger = logging.getLogger(__name__)


class Language(StrEnum):
    """The two languages the interface speaks.

    French is the source: every sentence is written in it, and English is a
    translation held beside it. That is why :data:`Language.FRENCH` needs no
    catalogue and can never fail to load.
    """

    FRENCH = "fr"
    ENGLISH = "en"

    @property
    def english_name(self) -> str:
        """The language's own name, which is the same in both languages."""
        return "Français" if self is Language.FRENCH else "English"


DEFAULT_LANGUAGE: Final = Language.FRENCH

# The English catalogue: French sentence -> English sentence. Loaded once, on demand,
# and never for a French session -- a French run must not pay for a file it will not
# read, and must not fail because that file is missing.
_catalogue: dict[str, str] | None = None
_current: Language = DEFAULT_LANGUAGE


def catalogue_path() -> Path:
    """Where the English catalogue is shipped, beside the game database."""
    from satisplanner import paths

    return paths.resource_directory() / "en.json"


def load_catalogue(path: Path | None = None) -> dict[str, str]:
    """Read the English catalogue, or return an empty one and say so.

    An empty catalogue is not a crash: every lookup then falls back to French, which
    is a readable interface in the wrong language rather than a broken one. It is
    logged as a warning because it means the packaged build lost a resource, and
    ``--self-check`` reports it for the same reason it reports the icons.
    """
    target = catalogue_path() if path is None else path
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        logger.warning("catalogue anglais illisible (%s) : %s", target, error)
        return {}
    if not isinstance(raw, dict):
        logger.warning("catalogue anglais mal formé (%s) : un objet est attendu", target)
        return {}
    return {str(key): str(value) for key, value in raw.items()}


def set_language(language: Language | str) -> None:
    """Switch the language every later call to :func:`_` answers in."""
    global _current, _catalogue
    wanted = Language(language)
    if wanted is Language.ENGLISH and _catalogue is None:
        _catalogue = load_catalogue()
    _current = wanted


def language() -> Language:
    return _current


def is_english() -> bool:
    """For the handful of places that must branch on more than a sentence."""
    return _current is Language.ENGLISH


def catalogue() -> dict[str, str]:
    """The loaded English catalogue, loading it if it has not been read yet."""
    global _catalogue
    if _catalogue is None:
        _catalogue = load_catalogue()
    return _catalogue


def reset_for_tests() -> None:
    """Forget the language and the catalogue. Only a test has any business here."""
    global _current, _catalogue
    _current = DEFAULT_LANGUAGE
    _catalogue = None


def translatable_strings(root: Path | None = None) -> set[str]:
    """Every literal the package hands to :func:`_`, found by reading the source.

    Static analysis rather than a registry filled at import time: a sentence in a
    branch nobody took would be missing from a registry, and the whole point of the
    count is to be able to say a build is **completely** translated.

    Two rules are enforced here rather than left to review. The argument of ``_``
    must be a **literal** -- ``_(f"...")`` or ``_(variable)`` cannot be found, so
    they cannot be counted, so they must not exist. And a sentence with a value in
    it uses named ``{fields}``, never positional ones, because the two languages do
    not always want the values in the same order.

    Returns an empty set rather than raising when the sources are not there: a
    frozen build has no ``.py`` files beside it, and a coverage figure is worth less
    than the application starting.
    """
    package = Path(__file__).resolve().parent.parent if root is None else root
    found: set[str] = set()
    for path in sorted(package.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not (isinstance(node.func, ast.Name) and node.func.id == "_"):
                continue
            first = node.args[0] if node.args else None
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                found.add(first.value)
    return found


def coverage(root: Path | None = None) -> tuple[int, int]:
    """``(translated, translatable)``, for a build that must not ship half-done.

    ``--self-check`` prints it for the same reason it prints the icon count: a
    packaged build that lost ``en.json``, or one shipped before the catalogue was
    finished, looks exactly like a healthy one until somebody switches language.
    """
    wanted = translatable_strings(root)
    have = catalogue()
    return sum(1 for key in wanted if key in have), len(wanted)


def _(text: str) -> str:
    """The sentence, in the language the interface is set to.

    The French text is the argument and the key at once. In a French session this is
    the identity function and costs one comparison; in an English one it is a
    dictionary lookup that falls back to the French when the catalogue has no entry
    -- a missing sentence shows in the wrong language rather than as an empty label.
    """
    if _current is Language.FRENCH:
        return text
    if _catalogue is None:
        return catalogue().get(text, text)
    return _catalogue.get(text, text)
