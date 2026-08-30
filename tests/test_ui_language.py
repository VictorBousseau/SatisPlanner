"""Switching language in a real window, and what that must leave alone.

The switch is immediate rather than "restart to apply", for the same reason the icon
folder is: being told to restart is being told to find out later. That promise has a
price, and the price is what this file checks. Everything that *displays* a factory
is rebuilt -- the palette entries, the node items, the reports whose labels the
engine writes at solve time -- and everything that *is* a factory is not touched at
all: the graph, the undo stack, the selection.

Getting that wrong would be invisible in the obvious test and expensive in use.
A rebuild that went through the document would clear the undo history, and the
person who noticed would be someone three hours into a factory who wanted to see
what a machine was called in English.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot

from satisplanner.core import i18n
from satisplanner.core.graph import MachineNode, ResourceNode
from satisplanner.core.i18n import Language
from satisplanner.core.models import GameData, Purity
from satisplanner.core.results import FactoryReport, NodeSolution
from satisplanner.data import db
from satisplanner.ui import edits
from satisplanner.ui.main_window import MainWindow
from tests.conftest import temporary_settings


@pytest.fixture(scope="module")
def catalogue() -> GameData:
    """The shipped database: the fixture slice has no English blurbs to switch to."""
    return db.load_game_data_from_file(db.default_database_path())


@pytest.fixture
def window(qtbot: QtBot, catalogue: GameData, tmp_path: Path) -> Iterator[MainWindow]:
    """See ``test_ui_smoke`` for why this is not handed to ``qtbot.addWidget``."""
    del qtbot
    built = MainWindow(catalogue, settings=temporary_settings(tmp_path))
    built.new_tab()
    yield built
    i18n.reset_for_tests()
    built.dispose()
    built.close()
    built.deleteLater()


def a_factory(window: MainWindow) -> None:
    """One smelter fed by one deposit, edited once so the undo stack is not empty."""
    graph = window.document.graph
    graph.add_node(
        ResourceNode(
            id="gisement", item_class="Desc_OreIron_C", extractor_class="Build_MinerMk2_C"
        )
    )
    graph.add_node(MachineNode(id="four", recipe_class="Recipe_IngotIron_C", machine_count=2))
    graph.connect("gisement", "four", "Desc_OreIron_C", "Build_ConveyorBeltMk3_C",
                  game_data=window.game_data)
    window.document.touch()
    edits.set_quantity(window.document, "four", 8)
    # An edit schedules the solve on a timer; the switch forces one. Without this
    # the "before" report would be the one from before the edit, and the comparison
    # would be measuring the timer rather than the language.
    window.document.solve_now()


def report_of(window: MainWindow) -> FactoryReport:
    """The report the window is showing, which is never absent once solved."""
    report = window.document.report
    assert report is not None
    return report


def node_solution(window: MainWindow, node_id: str) -> NodeSolution:
    return next(node for node in report_of(window).nodes if node.node_id == node_id)


# --------------------------------------------------------------------------- #
# What changes
# --------------------------------------------------------------------------- #


def test_the_node_face_changes_language(window: MainWindow) -> None:
    """Title and subtitle both: the item's name and the machine's, from the game."""
    a_factory(window)
    item = window.scene.nodes["four"]
    assert item.title() == "Lingot de fer"
    assert "Fonderie" in item.subtitle()

    window.set_language(Language.ENGLISH)
    item = window.scene.nodes["four"]
    assert item.title() == "Iron Ingot"
    assert "Smelter" in item.subtitle()


def test_the_report_is_solved_again_so_its_labels_follow(window: MainWindow) -> None:
    """A report is a value computed in a language.

    Its node labels are written by the engine at solve time, so keeping the old
    report would leave French labels under an English interface -- in the table, in
    the totals and in every diagnostic.
    """
    a_factory(window)
    assert node_solution(window, "four").label == "Lingot de fer"
    window.set_language(Language.ENGLISH)
    assert node_solution(window, "four").label == "Iron Ingot"


def test_the_palette_is_rebuilt(window: MainWindow) -> None:
    """And rebuilt rather than relabelled: the same list decodes a drop on the canvas."""
    labels = {entry.label for entry in window.entries}
    assert "Lingot de fer" in labels
    window.set_language(Language.ENGLISH)
    labels = {entry.label for entry in window.entries}
    assert "Iron Ingot" in labels
    assert "Lingot de fer" not in labels
    assert {entry.label for entry in window.scene.entries} == labels


def test_the_numbers_change_with_the_sentences(window: MainWindow) -> None:
    """One switch, not two: a French comma under English words would be worse."""
    from satisplanner.core import formatting

    a_factory(window)
    assert formatting.percent(0.818) == "81,8 %"
    window.set_language(Language.ENGLISH)
    assert formatting.percent(0.818) == "81.8%"


def test_the_preference_is_stored_so_the_next_launch_remembers(window: MainWindow) -> None:
    window.set_language(Language.ENGLISH)
    assert window.preferences.language is Language.ENGLISH


def test_the_menu_ticks_the_language_in_force(window: MainWindow) -> None:
    assert window.language_actions[Language.FRENCH].isChecked()
    window.set_language(Language.ENGLISH)
    assert window.language_actions[Language.ENGLISH].isChecked()
    assert not window.language_actions[Language.FRENCH].isChecked()


def test_the_menu_bar_follows_the_language(window: MainWindow) -> None:
    """The half of the interface that is written once and never recomputed.

    A palette entry and a diagnostic are rebuilt on a switch, so they followed the
    language from the first day. A menu title was set from a string when the window
    was built and nothing read that string again -- so the catalogue could be
    complete, the nodes could read *Iron Ingot*, and the menu bar still say
    « Fichier ». That is a translated catalogue, not a translated application, and
    it went unnoticed because nothing here looked at a menu.
    """
    titles = [action.text() for action in window.menuBar().actions()]
    assert "&Fichier" in titles
    window.set_language(Language.ENGLISH)
    titles = [action.text() for action in window.menuBar().actions()]
    assert "&File" in titles
    assert "&Fichier" not in titles
    # The language menu names both languages on purpose: somebody who cannot read
    # the interface has to be able to find the way out of it.
    assert "&Langue / Language" in titles


def test_every_label_written_once_at_start_up_follows_too(window: MainWindow) -> None:
    """The rest of the chrome, which had exactly the same defect.

    Actions, dock titles, the filters of the findings panel, the palette's own
    labels: each was set in a constructor. They are checked together because they
    failed together, and because a switch that fixes four of the five is worse than
    one that fixes none -- it looks finished.
    """
    a_factory(window)
    window.set_language(Language.ENGLISH)
    assert window.new_action.text() == "New tab"
    # "Undo" and then what is being undone. The second half stays in the language
    # the edit was made in, and that is right: an undo entry names a thing that
    # happened, and it happened in French.
    assert window.undo_action.text().startswith("Undo")
    assert window.table_dock.windowTitle() == "Table"
    assert window.diagnostics_panel.errors.text() == "Errors"
    assert window.palette_widget.machine.itemText(0) == "All machines"
    assert window.table_panel.filter.placeholderText() == "Filter the nodes..."


def test_rebuilding_the_menus_keeps_what_the_actions_were_saying(
    window: MainWindow,
) -> None:
    """The price of rebuilding rather than relabelling, paid where it is due.

    Every action is a new object after a switch, so anything an old one carried has
    to be put back: which mode the factory is in, whether deployed rendering is on,
    which language is ticked. A shortcut must fire once and not twice, which is why
    the previous actions are deleted rather than left as orphaned children.
    """
    a_factory(window)
    mode = window.document.graph.attachment_mode
    deployed = window.deployed_action.isChecked()

    window.set_language(Language.ENGLISH)

    assert window.attachment_mode_actions[mode].isChecked()
    assert window.deployed_action.isChecked() is deployed
    assert window.language_actions[Language.ENGLISH].isChecked()
    # One action per shortcut, or Qt refuses to deliver the key at all.
    saves = [
        action
        for action in window.findChildren(type(window.new_action))
        if action.shortcut().toString() == "Ctrl+S"
    ]
    assert len(saves) == 1


def test_the_help_page_is_written_in_the_language_in_force(window: MainWindow) -> None:
    """The page a newcomer reads to decide whether to trust the numbers."""
    from satisplanner.ui import help_dialog

    page = help_dialog.help_html(help_dialog.shortcut_rows(window.documented_actions()))
    assert "Gestes du canvas" in page
    assert "Un sous-produit sans issue arrête la machine" in page

    window.set_language(Language.ENGLISH)
    page = help_dialog.help_html(help_dialog.shortcut_rows(window.documented_actions()))
    assert "Canvas gestures" in page
    assert "A byproduct with nowhere to go stops the machine" in page
    assert "Gestes du canvas" not in page


def test_the_english_entry_announces_an_unfinished_translation(window: MainWindow) -> None:
    """The state of the work said in the very place you switch it.

    Somebody who switches and finds French sentences under English item names must
    have been told first. An announced gap is a state of the work; an unannounced
    one looks like a defect, and the difference is what decides whether they come
    back.
    """
    translated, translatable = i18n.coverage()
    entry = window.language_actions[Language.ENGLISH]
    if translatable and translated == translatable:
        assert entry.text() == "English"
    else:
        assert "traduction en cours" in entry.text()


# --------------------------------------------------------------------------- #
# What must not change
# --------------------------------------------------------------------------- #


def test_switching_language_loses_neither_the_document_nor_the_undo_stack(
    window: MainWindow,
) -> None:
    """The promise that makes an immediate switch worth having.

    A rebuild that went through the document would clear the history, and the person
    who found out would be three hours into a factory.
    """
    a_factory(window)
    before = window.document.graph.model_dump_json()
    depth = window.document.undo_stack.count()
    assert depth > 0, "le test doit avoir quelque chose à perdre"

    window.set_language(Language.ENGLISH)

    assert window.document.graph.model_dump_json() == before
    assert window.document.undo_stack.count() == depth
    assert window.document.undo_stack.canUndo()
    window.document.undo_stack.undo()
    node = window.document.graph.node("four")
    assert isinstance(node, MachineNode)
    assert node.machine_count == 2


def test_switching_language_keeps_the_selection(window: MainWindow) -> None:
    a_factory(window)
    window.scene.nodes["four"].setSelected(True)
    selected = {item.node.id for item in window.scene.selected_nodes()}
    assert selected == {"four"}

    window.set_language(Language.ENGLISH)
    assert {item.node.id for item in window.scene.selected_nodes()} == selected


def test_switching_language_moves_no_figure(window: MainWindow) -> None:
    """The headline promise of the whole translation work, checked in a real window."""
    a_factory(window)
    french = report_of(window)
    window.set_language(Language.ENGLISH)
    english = report_of(window)

    assert french.power_total_mw == english.power_total_mw
    assert [node.outputs for node in french.nodes] == [node.outputs for node in english.nodes]
    assert french.shopping_list.buildings == english.shopping_list.buildings
    assert len(french.diagnostics) == len(english.diagnostics)


def test_switching_back_and_forth_returns_exactly_where_it_started(
    window: MainWindow,
) -> None:
    """Including Qt's own buttons: a translator that is never removed is never undone."""
    a_factory(window)
    before = window.scene.nodes["four"].title()
    window.set_language(Language.ENGLISH)
    window.set_language(Language.FRENCH)
    assert window.scene.nodes["four"].title() == before
    assert node_solution(window, "four").label == before


def test_switching_to_the_language_already_in_force_does_nothing(
    window: MainWindow,
) -> None:
    """Cheap, and it keeps a menu click from re-solving every open factory."""
    a_factory(window)
    report = report_of(window)
    window.set_language(Language.FRENCH)
    assert report_of(window) is report


def test_every_open_factory_follows_and_not_only_the_one_in_front(
    window: MainWindow, catalogue: GameData
) -> None:
    """A language is a property of the application, like the colours are."""
    del catalogue
    a_factory(window)
    window.new_tab()
    window.document.graph.add_node(
        ResourceNode(
            id="cuivre",
            item_class="Desc_OreCopper_C",
            extractor_class="Build_MinerMk1_C",
            purity=Purity.PURE,
        )
    )
    window.document.touch()
    window.document.solve_now()

    window.set_language(Language.ENGLISH)
    for tab in window.open_tabs():
        report = tab.document.report
        assert report is not None
        for solution in report.nodes:
            assert "Minerai" not in solution.label
