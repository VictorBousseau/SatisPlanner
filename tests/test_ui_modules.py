"""Modules taken the way a user takes them: through the menu and the library.

The library's own file handling is tested in ``test_modules``. What is tested here
is the round trip a person actually performs -- select, save, open another factory,
insert, undo -- and the one it exists for: open a module in a tab, change it, save it
back under the same name, and find the change the next time it is inserted.

Nothing here writes into the developer's own library: the window takes the
directory, exactly as it takes its settings.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot

from satisplanner.core.graph import FactoryGraph, MachineNode, OutputNode
from satisplanner.core.models import GameData
from satisplanner.data import module_file
from satisplanner.ui.main_window import MainWindow
from satisplanner.ui.modules import SaveModuleDialog
from tests.conftest import temporary_settings

BELT = "Build_ConveyorBeltMk3_C"
INGOT = "Desc_IronIngot_C"
PLATE = "Desc_IronPlate_C"
ROD = "Desc_IronRod_C"


@pytest.fixture
def library(tmp_path: Path) -> Path:
    return tmp_path / "modules"


@pytest.fixture
def window(
    qtbot: QtBot, game_data: GameData, tmp_path: Path, library: Path
) -> Iterator[MainWindow]:
    """See ``test_ui_smoke`` for why this is not handed to ``qtbot.addWidget``."""
    del qtbot
    built = MainWindow(game_data, temporary_settings(tmp_path), library)
    yield built
    if built.library is not None:
        built.library.close()
    built.dispose()
    built.close()
    built.deleteLater()


def chain(window: MainWindow) -> None:
    """Rods and plates side by side, plus an exit the selection will leave behind."""
    graph = FactoryGraph(
        nodes=[
            MachineNode(id="tige1", recipe_class="Recipe_IronRod_C", machine_count=1),
            MachineNode(id="plaque1", recipe_class="Recipe_IronPlate_C", machine_count=2),
            OutputNode(id="sortie1", item_class=PLATE),
        ]
    )
    graph.connect("plaque1", "sortie1", PLATE, BELT, window.game_data)
    window.document.reset(graph)


def naming(monkeypatch: pytest.MonkeyPatch, name: str, description: str = "") -> None:
    """The save box, filled in and accepted, without a hand to click it."""

    def accept(dialog: SaveModuleDialog) -> int:
        dialog.name.setText(name)
        dialog.description.setPlainText(description)
        return int(SaveModuleDialog.DialogCode.Accepted)

    monkeypatch.setattr(SaveModuleDialog, "exec", accept)


def saved_module(window: MainWindow, monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    naming(monkeypatch, name)
    assert window.save_as_module()


# --------------------------------------------------------------------------- #
# Save here, insert there
# --------------------------------------------------------------------------- #


def test_a_selection_saved_as_a_module_reappears_in_another_factory(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch, library: Path
) -> None:
    """The gesture the whole feature is for, end to end through the window."""
    chain(window)
    window.scene.select_nodes(["tige1", "plaque1"])
    saved_module(window, monkeypatch, "Tiges et plaques")

    other = window.new_tab()
    (module,) = module_file.load_library(library)[0]
    assert window.insert_module(module)

    assert {node.id for node in other.document.graph.nodes} == {"tige1", "plaque1"}
    assert other.document.graph.edges == [], (
        "l'arête vers la sortie quittait la sélection : elle n'avait plus rien à quoi s'accrocher"
    )


def test_the_internal_lines_ride_along_and_the_identifiers_are_fresh(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch, library: Path
) -> None:
    """Inserted into a factory that already uses those names, nothing collides."""
    graph = FactoryGraph(
        nodes=[
            MachineNode(id="tige1", recipe_class="Recipe_IronRod_C", machine_count=1),
            MachineNode(id="vis1", recipe_class="Recipe_Screw_C", machine_count=1),
        ]
    )
    graph.connect("tige1", "vis1", ROD, BELT, window.game_data)
    window.document.reset(graph)
    window.scene.select_all()
    saved_module(window, monkeypatch, "Tiges vers vis")

    (module,) = module_file.load_library(library)[0]
    assert window.insert_module(module)

    ids = [node.id for node in window.document.graph.nodes]
    assert sorted(ids) == ["tige1", "tige2", "vis1", "vis2"], ids
    assert len(window.document.graph.edges) == 2, "la ligne interne suit le module"
    fresh = next(edge for edge in window.document.graph.edges if edge.source == "tige2")
    assert fresh.target == "vis2", "la ligne copiée relie les copies, pas les originaux"


def test_an_insertion_is_a_single_undo(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch, library: Path
) -> None:
    chain(window)
    window.scene.select_nodes(["tige1", "plaque1"])
    saved_module(window, monkeypatch, "Deux nœuds")
    window.document.undo_stack.setClean()
    before = [node.id for node in window.document.graph.nodes]

    (module,) = module_file.load_library(library)[0]
    window.insert_module(module)
    assert len(window.document.graph.nodes) == len(before) + 2

    window.undo_action.trigger()

    assert [node.id for node in window.document.graph.nodes] == before
    assert window.document.is_modified is False, "une insertion, une annulation, rien de reste"


def test_an_insertion_lands_where_the_reader_is_looking(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch, library: Path
) -> None:
    chain(window)
    window.scene.select_nodes(["tige1"])
    saved_module(window, monkeypatch, "Une tige")
    window.document.reset()
    window.view.centerOn(4000.0, 2000.0)

    (module,) = module_file.load_library(library)[0]
    window.insert_module(module)

    (node,) = window.document.graph.nodes
    assert abs(node.position[0] - 4000.0) < 200.0, node.position
    assert abs(node.position[1] - 2000.0) < 200.0, node.position


def test_the_inserted_nodes_are_selected(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch, library: Path
) -> None:
    chain(window)
    window.scene.select_nodes(["tige1", "plaque1"])
    saved_module(window, monkeypatch, "Deux nœuds")
    window.document.reset()

    (module,) = module_file.load_library(library)[0]
    window.insert_module(module)

    assert len(window.scene.selected_nodes()) == 2


def test_saving_nothing_says_so_rather_than_writing_an_empty_module(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch, library: Path
) -> None:
    told: list[str] = []
    monkeypatch.setattr(
        "satisplanner.ui.main_window.QMessageBox.information",
        staticmethod(lambda *args, **_kwargs: told.append(str(args[2]))),
    )
    chain(window)
    window.scene.clearSelection()

    assert window.save_as_module() is False
    assert told and "Sélectionnez" in told[0]
    assert module_file.load_library(library)[0] == []


# --------------------------------------------------------------------------- #
# The label, and the fact that it is one
# --------------------------------------------------------------------------- #


def test_a_saved_module_carries_the_rates_of_the_module_alone(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch, library: Path
) -> None:
    """Two constructors: 60 ingots in, 40 plates out, whatever surrounds them."""
    chain(window)
    window.scene.select_nodes(["plaque1"])
    saved_module(window, monkeypatch, "40 plaques")

    (module,) = module_file.load_library(library)[0]
    assert module.inputs == {INGOT: 60.0}
    assert module.outputs == {PLATE: 40.0}


def test_the_suggested_name_says_what_it_makes(window: MainWindow) -> None:
    chain(window)
    piece = window.document.graph.model_copy(deep=True)
    piece.nodes = [node for node in piece.nodes if node.id == "plaque1"]
    piece.edges = []

    assert window._suggested_module_name(piece) == "Plaque de fer 40/min"


def test_the_library_shows_the_two_caveats(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both are things a reader assumes the other way round if nobody says them."""
    chain(window)
    window.scene.select_nodes(["plaque1"])
    saved_module(window, monkeypatch, "40 plaques")

    dialog = window.show_module_library()
    module = dialog.current_module()
    assert module is not None

    assert "étiquette, pas une promesse" in dialog.details_html(module)
    assert "copie" in dialog.findChildren(type(dialog.problems))[-1].text()


# --------------------------------------------------------------------------- #
# The library on screen
# --------------------------------------------------------------------------- #


def test_the_search_finds_a_module_by_what_it_produces(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    chain(window)
    window.scene.select_nodes(["plaque1"])
    saved_module(window, monkeypatch, "Un nom qui ne dit rien")

    dialog = window.show_module_library()
    dialog.search.setText("plaque de fer")

    assert [module.name for module in dialog.visible_modules()] == ["Un nom qui ne dit rien"]


def test_the_search_narrows_to_nothing_rather_than_showing_everything(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    chain(window)
    window.scene.select_nodes(["plaque1"])
    saved_module(window, monkeypatch, "Plaques")

    dialog = window.show_module_library()
    dialog.search.setText("aluminium")

    assert dialog.visible_modules() == []
    assert dialog.current_module() is None


def test_an_unreadable_file_is_named_and_the_rest_still_lists(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch, library: Path
) -> None:
    chain(window)
    window.scene.select_nodes(["plaque1"])
    saved_module(window, monkeypatch, "Bon module")
    (library / f"casse{module_file.MODULE_SUFFIX}").write_text("{{{", encoding="utf-8")

    dialog = window.show_module_library()

    assert [module.name for module in dialog.visible_modules()] == ["Bon module"]
    assert dialog.problems.isVisible() or "casse" in dialog.problems.text()


# --------------------------------------------------------------------------- #
# Editing a module, which is what stops the library becoming a graveyard
# --------------------------------------------------------------------------- #


def test_a_module_opens_in_its_own_tab_named_after_it(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch, library: Path
) -> None:
    chain(window)
    window.scene.select_nodes(["plaque1"])
    saved_module(window, monkeypatch, "40 plaques")
    window.new_tab()  # so the module does not simply take over the starting tab

    (module,) = module_file.load_library(library)[0]
    tab = window.open_module_in_tab(module)

    assert tab is window.current_tab
    assert tab is not None
    assert [node.id for node in tab.document.graph.nodes] == ["plaque1"]
    assert window.tabs.tabText(window.tabs.currentIndex()) == "40 plaques"


def test_editing_a_module_and_saving_it_back_changes_what_gets_inserted(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch, library: Path
) -> None:
    """The round trip, which is the whole of "and it is not a graveyard".

    Open it, change the machine count, save it under the same name -- no selection
    needed, the tab **is** the module -- and insert it again somewhere else.
    """
    chain(window)
    window.scene.select_nodes(["plaque1"])
    saved_module(window, monkeypatch, "40 plaques")

    (module,) = module_file.load_library(library)[0]
    window.open_module_in_tab(module)
    window.scene.set_quantity("plaque1", 5.0)
    window.scene.clearSelection()
    saved_module(window, monkeypatch, "40 plaques")

    modules, _ = module_file.load_library(library)
    assert len(modules) == 1, "réenregistré sous le même nom : un seul module, pas deux"
    assert modules[0].outputs == {PLATE: 100.0}, "5 constructeurs, 100 plaques par minute"

    fresh = window.new_tab()
    window.insert_module(modules[0])
    (node,) = fresh.document.graph.nodes
    assert node.machine_count == 5.0  # type: ignore[union-attr]


def test_saving_an_edited_module_under_another_name_makes_a_second_one(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch, library: Path
) -> None:
    chain(window)
    window.scene.select_nodes(["plaque1"])
    saved_module(window, monkeypatch, "40 plaques")

    (module,) = module_file.load_library(library)[0]
    window.open_module_in_tab(module)
    window.scene.set_quantity("plaque1", 5.0)
    window.scene.clearSelection()
    saved_module(window, monkeypatch, "100 plaques")

    modules, _ = module_file.load_library(library)
    assert sorted(module.name for module in modules) == ["100 plaques", "40 plaques"]
    assert {module.name: module.outputs[PLATE] for module in modules} == {
        "40 plaques": 40.0,
        "100 plaques": 100.0,
    }


def test_new_from_a_module_starts_a_factory_with_no_link_back(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch, library: Path
) -> None:
    """A copy, and the window must not think that tab still edits the module."""
    chain(window)
    window.scene.select_nodes(["plaque1"])
    saved_module(window, monkeypatch, "40 plaques")
    window.new_tab()

    (module,) = module_file.load_library(library)[0]
    tab = window.new_from_module(module)

    assert tab is not None
    assert [node.id for node in tab.document.graph.nodes] == ["plaque1"]
    assert tab not in window.editing_module, (
        "une usine démarrée depuis un module n'est plus le module"
    )


def test_an_inserted_module_does_not_follow_its_definition(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch, library: Path
) -> None:
    """Pure copy, stated as the test that would fail if it ever became a link."""
    chain(window)
    window.scene.select_nodes(["plaque1"])
    saved_module(window, monkeypatch, "40 plaques")
    (module,) = module_file.load_library(library)[0]

    host = window.new_tab()
    window.insert_module(module)

    window.open_module_in_tab(module)
    window.scene.set_quantity("plaque1", 9.0)
    window.scene.clearSelection()
    saved_module(window, monkeypatch, "40 plaques")

    (inserted,) = host.document.graph.nodes
    assert inserted.machine_count == 2.0, (  # type: ignore[union-attr]
        "l'usine hôte garde sa copie : modifier le module ne la touche pas"
    )
