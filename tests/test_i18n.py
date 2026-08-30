"""The language switch, and above all the things it must **not** touch.

A translation lot is measured by what stays put. Two files leave this application
and are read by someone else's copy of it -- the ``.sfp`` and the share code -- and
neither may carry a trace of the language that wrote it. A factory designed in French
and opened in English has to be the same factory, node for node and figure for
figure, or the sharing this application is built around is worth nothing.

The rest of the file holds the mechanism itself: a French sentence is its own key,
English is data and never code, and a key that has no translation falls back to
French rather than to an empty label.
"""

import json

import pytest

from satisplanner.core import formatting, i18n
from satisplanner.core.graph import (
    ExternalSourceNode,
    FactoryGraph,
    MachineNode,
    OutputNode,
    ResourceNode,
)
from satisplanner.core.i18n import Language, _
from satisplanner.core.models import GameData, Purity


@pytest.fixture(autouse=True)
def french_again() -> object:
    """Every test here leaves the interpreter in French, whatever it did.

    The language is process-wide state, which is what makes it cheap to read from
    ``core``; the price is that a test which forgets to put it back poisons every
    test that runs after it.
    """
    yield None
    i18n.reset_for_tests()


def in_english() -> None:
    i18n.set_language(Language.ENGLISH)


# --------------------------------------------------------------------------- #
# The mechanism
# --------------------------------------------------------------------------- #


def test_french_is_the_identity_and_needs_no_catalogue() -> None:
    """The source language costs one comparison and cannot fail to load."""
    i18n.set_language(Language.FRENCH)
    assert _("Aucune ligne n'apporte cet objet.") == "Aucune ligne n'apporte cet objet."


def test_a_sentence_with_no_translation_stays_french(tmp_path: object) -> None:
    """A missing entry shows in the wrong language, never as an empty label.

    Which is the only sane failure mode: an interface with one French sentence in
    it is readable, and one with a blank where a warning should be is not.
    """
    del tmp_path
    in_english()
    assert _("Une phrase que personne n'a traduite.") == "Une phrase que personne n'a traduite."


def test_an_unreadable_catalogue_is_a_warning_and_not_a_crash(tmp_path: object) -> None:
    from pathlib import Path

    assert isinstance(tmp_path, Path)
    assert i18n.load_catalogue(tmp_path / "absent.json") == {}
    broken = tmp_path / "cassé.json"
    broken.write_text("[]", encoding="utf-8")
    assert i18n.load_catalogue(broken) == {}


def test_the_catalogue_is_data_and_never_code() -> None:
    """Which is why the accent guard needed no exemption list at all.

    ``tests/test_accents.py`` scans the Python string literals of the package and
    would cry wolf on *ingredients*, *reference* or *deficit* if English sentences
    lived in ``.py`` files. They do not: the French sentence is the key, the English
    one is a value in ``resources/en.json``, and the guard keeps checking exactly
    what it always checked.
    """
    path = i18n.catalogue_path()
    assert path.suffix == ".json"
    assert path.parent.name == "resources"


# --------------------------------------------------------------------------- #
# Numbers, without Qt
# --------------------------------------------------------------------------- #


def test_the_decimal_separator_follows_the_language() -> None:
    """A comma read as a thousands separator turns 24 into 24 000."""
    i18n.set_language(Language.FRENCH)
    assert formatting.number(24.5487) == "24,549"
    in_english()
    assert formatting.number(24.5487) == "24.549"


def test_the_space_before_the_percent_sign_is_french_typography() -> None:
    i18n.set_language(Language.FRENCH)
    assert formatting.percent(0.818) == "81,8 %"
    in_english()
    assert formatting.percent(0.818) == "81.8%"


def test_the_elision_is_french_and_english_has_none() -> None:
    i18n.set_language(Language.FRENCH)
    assert formatting.of("Ordinateur") == "d'Ordinateur"
    assert formatting.of("Plaque de fer") == "de Plaque de fer"
    in_english()
    assert formatting.of("Computer") == "of Computer"


def test_a_whole_number_reads_the_same_in_both() -> None:
    """Nothing is grouped in thousands, so an integer has no separator to differ on."""
    for language in Language:
        i18n.set_language(language)
        assert formatting.number(1200) == "1200"


# --------------------------------------------------------------------------- #
# The game's own words, which are never translated here
# --------------------------------------------------------------------------- #


def test_the_vocabulary_comes_from_the_game_and_not_from_this_repository(
    game_data: GameData,
) -> None:
    """A Manufacturer is a Façonneuse because ``fr.json`` says so.

    Both halves are read from the game's own locale files, which is why this lot
    could not get a term wrong: there was nothing to get wrong, only a column to
    choose.
    """
    plate = game_data.items["Desc_IronPlate_C"]
    i18n.set_language(Language.FRENCH)
    assert plate.name == "Plaque de fer"
    in_english()
    assert plate.name == "Iron Plate"
    assert plate.name == plate.display_name


def test_the_blurb_follows_the_language_too() -> None:
    """Read from the shipped catalogue: the test fixture carries no French blurbs."""
    from satisplanner.data import db

    plate = db.load_game_data_from_file(db.default_database_path()).items["Desc_IronPlate_C"]
    i18n.set_language(Language.FRENCH)
    assert plate.description.startswith("Matériau de fabrication")
    in_english()
    assert plate.description.startswith("Used for crafting")


# --------------------------------------------------------------------------- #
# What the language must not touch
# --------------------------------------------------------------------------- #


def sample_factory(game_data: GameData) -> FactoryGraph:
    """A factory with a source, a machine, an import and an exit."""
    graph = FactoryGraph()
    graph.add_node(
        ResourceNode(
            id="gisement",
            item_class="Desc_OreIron_C",
            extractor_class="Build_MinerMk2_C",
            purity=Purity.PURE,
            count=1,
            clock_speed=2.5,
        )
    )
    graph.add_node(MachineNode(id="four", recipe_class="Recipe_IngotIron_C", machine_count=8))
    graph.add_node(ExternalSourceNode(id="entree", item_class="Desc_Coal_C", rate_per_minute=30))
    graph.add_node(OutputNode(id="sortie", item_class="Desc_IronIngot_C"))
    belt = "Build_ConveyorBeltMk3_C"
    graph.connect("gisement", "four", "Desc_OreIron_C", belt, game_data=game_data)
    graph.connect("four", "sortie", "Desc_IronIngot_C", belt, game_data=game_data)
    return graph


def test_a_saved_factory_is_byte_for_byte_the_same_in_both_languages(
    game_data: GameData, tmp_path: object
) -> None:
    """The regression nobody would see on their own machine.

    A decimal comma leaking into a clock speed, or a French label written into a
    node, would open as a different factory on someone else's copy -- and the person
    who wrote the file would never know, because it reads back correctly on theirs.
    """
    from pathlib import Path

    from satisplanner.data import factory_file

    assert isinstance(tmp_path, Path)
    graph = sample_factory(game_data)

    i18n.set_language(Language.FRENCH)
    french_path = tmp_path / "française.sfp"
    factory_file.save(french_path, graph)
    french_json = factory_file.load(french_path).graph.model_dump_json()

    in_english()
    english_path = tmp_path / "english.sfp"
    factory_file.save(english_path, graph)
    english_json = factory_file.load(english_path).graph.model_dump_json()

    assert french_json == english_json


def test_a_share_code_written_in_one_language_reads_in_the_other(
    game_data: GameData,
) -> None:
    """The same promise for the line of text people actually paste to each other."""
    from satisplanner.data import factory_file

    graph = sample_factory(game_data)
    i18n.set_language(Language.FRENCH)
    code = factory_file.encode_share_code(graph)

    in_english()
    assert factory_file.encode_share_code(graph) == code
    loaded = factory_file.decode_share_code(code)
    assert loaded.is_clean
    assert loaded.graph.model_dump_json() == graph.model_dump_json()


def test_the_figures_of_a_factory_do_not_depend_on_the_language(
    game_data: GameData,
) -> None:
    """The lot's headline promise: a translation moves no number.

    Compared on the *values* and not on their formatting, because the formatting is
    exactly what is allowed to differ.
    """
    from satisplanner.core import engine

    graph = sample_factory(game_data)
    i18n.set_language(Language.FRENCH)
    french = engine.solve(graph, game_data)
    in_english()
    english = engine.solve(graph, game_data)

    assert french.power_total_mw == english.power_total_mw
    assert [node.outputs for node in french.nodes] == [node.outputs for node in english.nodes]
    assert french.shopping_list.buildings == english.shopping_list.buildings


def test_the_catalogue_file_is_json_and_flat_when_it_exists() -> None:
    """A shape check, so a malformed catalogue fails here and not on a user's screen."""
    path = i18n.catalogue_path()
    if not path.is_file():
        pytest.skip("catalogue anglais pas encore écrit")
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(raw, dict)
    assert all(isinstance(key, str) and isinstance(value, str) for key, value in raw.items())


# --------------------------------------------------------------------------- #
# The catalogue against the source
# --------------------------------------------------------------------------- #


def test_every_sentence_of_the_package_is_in_the_catalogue() -> None:
    """The gate that makes a source string safe to use as a key.

    The usual objection to keying on the source text is that a typo silently
    un-translates a sentence. It cannot here: a literal handed to ``_`` that the
    catalogue does not know breaks this test rather than shipping.
    """
    missing = sorted(i18n.translatable_strings() - set(i18n.catalogue()))
    assert not missing, "phrases sans traduction :\n  " + "\n  ".join(missing)


def test_the_catalogue_holds_nothing_the_package_no_longer_says() -> None:
    """A sentence deleted from the code has to leave the catalogue with it.

    Not a cosmetic tidy-up: a stale entry is an entry nobody will ever see again,
    and it inflates the coverage figure ``--self-check`` prints, which is the one
    number this whole mechanism exists to make trustworthy.
    """
    stale = sorted(set(i18n.catalogue()) - i18n.translatable_strings())
    assert not stale, "traductions orphelines :\n  " + "\n  ".join(stale)


def test_the_named_fields_are_the_same_on_both_sides() -> None:
    """``{item}`` in French has to be ``{item}`` in English, in any order.

    The order is allowed to differ -- that is exactly why the fields are named --
    but a field the translation forgot raises ``KeyError`` at the moment the message
    is shown, which is to say in front of the user and never in a test.
    """
    import re

    fields = lambda text: set(re.findall(r"\{(\w+)\}", text))  # noqa: E731
    for french, english in sorted(i18n.catalogue().items()):
        assert fields(french) == fields(english), f"champs différents : {french!r}"


def test_no_translation_is_empty_and_none_is_left_in_french() -> None:
    """An empty value would blank a label; a copied one would be a missed sentence.

    Some entries are legitimately identical in both languages -- *Standard*,
    *Machine*, *Type*, *Production*, the language menu that names both languages on
    purpose -- so the check is a ceiling rather than an absence, and it is low
    enough that a page of sentences left in French would break it.
    """
    catalogue = i18n.catalogue()
    assert all(value.strip() for value in catalogue.values())
    identical = sorted(key for key, value in catalogue.items() if key == value)
    assert len(identical) <= 20, f"trop de phrases non traduites : {identical}"


def test_a_line_break_survives_the_translation() -> None:
    """Three messages are written on two lines and have to stay that way."""
    for french, english in i18n.catalogue().items():
        assert french.count("\n") == english.count("\n"), f"saut de ligne perdu : {french!r}"


def test_the_spaces_at_the_edges_survive_the_translation() -> None:
    """The one mistake a reader sees immediately and a diff does not.

    A node's subtitle is assembled from runs, several of which are written with
    their own spaces -- ``" — gisement "`` sits between a building and a purity.
    A translation that trims them renders "Miner Mk.2 — depositnormal", which was
    exactly what the first draft of the catalogue did.
    """
    import re

    edges = re.compile(r"^(\s*).*?(\s*)$", re.S)
    for french, english in sorted(i18n.catalogue().items()):
        wanted = edges.match(french)
        got = edges.match(english)
        assert wanted is not None and got is not None
        assert wanted.groups() == got.groups(), f"espaces de bord perdus : {french!r}"


def test_the_argument_of_the_translation_function_is_always_a_literal() -> None:
    """What makes the count exact rather than approximate.

    ``_(f"...")`` and ``_(variable)`` cannot be found by reading the source, so they
    cannot be counted, so a build could be announced complete while a sentence went
    through in French. The rule is therefore absolute, which is also why the label
    tables became functions: a table cannot be read by ``ast``, and a table built at
    import would have frozen the language of the first launch.
    """
    import ast
    from pathlib import Path

    import satisplanner

    offences: list[str] = []
    for path in sorted(Path(satisplanner.__file__).parent.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not (isinstance(node.func, ast.Name) and node.func.id == "_"):
                continue
            first = node.args[0] if node.args else None
            if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
                offences.append(f"{path.name}:{node.lineno}")
    assert not offences, "argument non littéral : " + ", ".join(offences)


def test_a_diagnostic_comes_out_in_english(game_data: GameData) -> None:
    """The end of the chain, on the sentence a user reads most.

    Checked on a real solve rather than on the catalogue, because a message only
    reaches the catalogue if the code actually asked for it: this is the difference
    between a translated string and a translated application.
    """
    from satisplanner.core import engine, validation

    graph = FactoryGraph()
    graph.add_node(MachineNode(id="four", recipe_class="Recipe_IngotIron_C", machine_count=2))

    i18n.set_language(Language.FRENCH)
    report = engine.solve(graph, game_data)
    french = validation.diagnose(graph, game_data, report)
    assert any("Aucune ligne n'apporte" in item.message for item in french)

    in_english()
    report = engine.solve(graph, game_data)
    english = validation.diagnose(graph, game_data, report)
    assert any("No line brings" in item.message for item in english)
    assert len(french) == len(english)


# --------------------------------------------------------------------------- #
# The switch
# --------------------------------------------------------------------------- #


def test_the_stored_language_wins_and_the_system_decides_the_first_launch(
    tmp_path: object,
) -> None:
    """Someone who picked a language meant it, including a French speaker who picked
    English. The system is consulted once, when nothing has been stored, so an
    English speaker opening this for the first time is not left guessing."""
    from pathlib import Path

    from satisplanner.ui.localisation import system_language
    from satisplanner.ui.preferences import Preferences
    from tests.conftest import temporary_settings

    assert isinstance(tmp_path, Path)
    preferences = Preferences(temporary_settings(tmp_path))
    assert preferences.language is system_language()
    preferences.language = Language.ENGLISH
    assert preferences.language is Language.ENGLISH
    preferences.language = Language.FRENCH
    assert preferences.language is Language.FRENCH


def test_a_stored_nonsense_language_falls_back_to_the_system() -> None:
    """A settings file edited by hand must not stop the application starting."""
    from pathlib import Path
    from tempfile import mkdtemp

    from satisplanner.ui.localisation import system_language
    from satisplanner.ui.preferences import KEY_LANGUAGE, Preferences
    from tests.conftest import temporary_settings

    preferences = Preferences(temporary_settings(Path(mkdtemp())))
    preferences.settings.setValue(KEY_LANGUAGE, "klingon")
    assert preferences.language is system_language()
