"""Generators in the interface: placed, read without a click, and refuelled.

The fuel is the third field to go through :mod:`satisplanner.ui.edits`, after the
clock and the purity, and it is checked the same way: the node says what it burns
without being asked, the context menu and the table both reach the one
implementation, and a fuel the building refuses comes back as a sentence rather
than as a silent correction.

The report block is checked here too, because "two numbers side by side" is a
statement about the screen and not about the solver.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMenu
from pytestqt.qtbot import QtBot

from satisplanner.core.graph import GeneratorNode
from satisplanner.core.models import GameData
from satisplanner.ui.catalogue import EntryKind, fuel_choices
from satisplanner.ui.help_dialog import MODELLING_NOTES
from satisplanner.ui.main_window import MainWindow
from satisplanner.ui.table_panel import COLUMN_FUEL, COLUMN_QUANTITY, ChoiceDelegate
from tests.conftest import temporary_settings

COAL = "Build_GeneratorCoal_C"
FUEL = "Build_GeneratorFuel_C"


@pytest.fixture
def window(qtbot: QtBot, game_data: GameData, tmp_path: Path) -> Iterator[MainWindow]:
    """See ``test_ui_smoke`` for why this is not handed to ``qtbot.addWidget``."""
    del qtbot
    built = MainWindow(game_data, settings=temporary_settings(tmp_path))
    yield built
    built.document.undo_stack.setClean()
    built.scene.dispose()
    built.close()
    built.deleteLater()


def place_generator(window: MainWindow, generator_class: str = COAL) -> str:
    """Through the palette, the way a user places one."""
    before = {node.id for node in window.document.graph.nodes}
    entry = next(
        e
        for e in window.entries
        if e.kind is EntryKind.GENERATOR and e.class_name == generator_class
    )
    window.palette_widget.entryActivated.emit(entry)
    (created,) = {node.id for node in window.document.graph.nodes} - before
    return created


def submenu(menu: QMenu, title: str) -> QMenu:
    children = [action.menu() for action in menu.actions()]
    found = [child for child in children if isinstance(child, QMenu)]
    for child in found:
        if child.title().startswith(title):
            return child
    msg = f"aucun sous-menu {title!r} : {[child.title() for child in found]}"
    raise AssertionError(msg)


# ----------------------------------------------------------------- the palette


def test_the_palette_offers_one_entry_per_generator(window: MainWindow) -> None:
    """One per building, not one per fuel: five entries for one fuel generator
    would be five ways to place the same thing."""
    entries = [e for e in window.entries if e.kind is EntryKind.GENERATOR]
    assert {e.class_name for e in entries} == {
        COAL,
        FUEL,
        "Build_GeneratorBiomass_Automated_C",
    }
    coal = next(e for e in entries if e.class_name == COAL)
    assert coal.label == "Générateur à charbon"
    assert "75 MW" in coal.detail
    assert coal.fuel_class == "Desc_Coal_C", "le premier carburant du jeu"


def test_a_placed_generator_starts_on_the_games_own_fuel(window: MainWindow) -> None:
    node_id = place_generator(window)
    node = window.document.graph.node(node_id)
    assert isinstance(node, GeneratorNode)
    assert node.generator_class == COAL
    assert node.fuel_class == "Desc_Coal_C"
    assert node.count == 1.0


# ------------------------------------------------------------------ on the node


def test_the_node_says_what_it_burns_without_a_click(window: MainWindow) -> None:
    node_id = place_generator(window)
    subtitle = window.scene.nodes[node_id].subtitle()
    assert "Charbon" in subtitle
    assert "75 MW produits" in subtitle


def test_the_node_shows_the_fuel_and_the_water_as_two_input_ports(
    window: MainWindow,
) -> None:
    """Make-up water is a real input on a pipe, so it is a port like any other."""
    node_id = place_generator(window)
    item = window.scene.nodes[node_id]
    ports = item.ports()
    assert [port.item_class for port in ports] == ["Desc_Coal_C", "Desc_Water_C"]
    assert not any(port.is_output for port in ports), "l'electricite n'est pas une ligne"


def test_a_fuel_generator_has_no_water_port(window: MainWindow) -> None:
    node_id = place_generator(window, FUEL)
    item = window.scene.nodes[node_id]
    assert [port.item_class for port in item.ports()] == ["Desc_LiquidFuel_C"]


def test_changing_the_fuel_shows_on_the_node(window: MainWindow) -> None:
    node_id = place_generator(window, FUEL)
    assert window.scene.set_fuel(node_id, "Desc_LiquidTurboFuel_C")
    assert "Turbocarburant" in window.scene.nodes[node_id].subtitle()
    # ...and the port follows, because the appetite did.
    item = window.scene.nodes[node_id]
    assert [port.item_class for port in item.ports()] == ["Desc_LiquidTurboFuel_C"]


# ------------------------------------------------------------- the two doors


def test_the_context_menu_offers_only_the_fuels_the_building_burns(
    window: MainWindow,
) -> None:
    node_id = place_generator(window)
    item = window.scene.nodes[node_id]
    menu = window.scene.build_context_menu(item.sceneBoundingRect().center(), window)
    assert menu is not None

    fuels = submenu(menu, "Carburant")
    labels = [action.text() for action in fuels.actions()]
    assert labels == ["Charbon", "Charbon compacté", "Coke de pétrole"]
    assert [a.text() for a in fuels.actions() if a.isChecked()] == ["Charbon"]

    next(a for a in fuels.actions() if a.text() == "Coke de pétrole").trigger()
    node = window.document.graph.node(node_id)
    assert isinstance(node, GeneratorNode)
    assert node.fuel_class == "Desc_PetroleumCoke_C"


def test_a_fuel_the_building_refuses_is_refused_out_loud(window: MainWindow) -> None:
    node_id = place_generator(window)
    complaints: list[str] = []
    window.scene.selectionSummaryChanged.connect(complaints.append)

    assert window.scene.set_fuel(node_id, "Desc_LiquidFuel_C") is False
    node = window.document.graph.node(node_id)
    assert isinstance(node, GeneratorNode)
    assert node.fuel_class == "Desc_Coal_C", "rien n'a bouge"
    assert complaints and "ne brule pas ce carburant" in complaints[-1]


def test_the_table_shows_and_edits_the_fuel(window: MainWindow) -> None:
    node_id = place_generator(window)
    model = window.table_panel.model
    row = model.row_of(node_id)
    assert row is not None
    assert model.index(row, COLUMN_FUEL).data() == "Charbon"
    assert model.index(row, COLUMN_QUANTITY).data() == "1 generateur(s)"

    assert model.setData(model.index(row, COLUMN_FUEL), "Desc_CompactedCoal_C")
    assert model.index(row, COLUMN_FUEL).data() == "Charbon compacté"

    window.document.undo_stack.undo()
    restored = window.document.graph.node(node_id)
    assert isinstance(restored, GeneratorNode)
    assert restored.fuel_class == "Desc_Coal_C"


def test_the_table_refuses_a_fuel_the_building_does_not_burn(window: MainWindow) -> None:
    node_id = place_generator(window)
    model = window.table_panel.model
    row = model.row_of(node_id)
    assert row is not None
    assert model.setData(model.index(row, COLUMN_FUEL), "Desc_LiquidFuel_C") is False
    node = window.document.graph.node(node_id)
    assert isinstance(node, GeneratorNode)
    assert node.fuel_class == "Desc_Coal_C"


def test_the_fuel_cell_is_chosen_from_a_list_not_typed(window: MainWindow) -> None:
    node_id = place_generator(window, FUEL)
    view = window.table_panel.view
    assert isinstance(view.itemDelegateForColumn(COLUMN_FUEL), ChoiceDelegate)

    model = window.table_panel.model
    row = model.row_of(node_id)
    assert row is not None
    choices = model.data(model.index(row, COLUMN_FUEL), _choices_role())
    assert list(choices) == fuel_choices(window.game_data, FUEL)


def test_a_machine_row_offers_no_fuel(window: MainWindow) -> None:
    entry = next(
        e
        for e in window.entries
        if e.kind is EntryKind.RECIPE and e.class_name == "Recipe_IngotIron_C"
    )
    window.palette_widget.entryActivated.emit(entry)
    node_id = window.document.graph.sorted_nodes()[-1].id
    model = window.table_panel.model
    row = model.row_of(node_id)
    assert row is not None
    assert model.index(row, COLUMN_FUEL).data() == "—"
    assert not model.flags(model.index(row, COLUMN_FUEL)) & Qt.ItemFlag.ItemIsEditable


def test_the_two_doors_push_the_same_command(window: MainWindow) -> None:
    """One implementation reached two ways, checked on the commands themselves."""
    first = place_generator(window)
    second = place_generator(window)

    window.scene.set_fuel(first, "Desc_PetroleumCoke_C")
    model = window.table_panel.model
    row = model.row_of(second)
    assert row is not None
    model.setData(model.index(row, COLUMN_FUEL), "Desc_PetroleumCoke_C")

    left, right = window.document.graph.node(first), window.document.graph.node(second)
    assert isinstance(left, GeneratorNode)
    assert isinstance(right, GeneratorNode)
    assert left.fuel_class == right.fuel_class

    stack = window.document.undo_stack
    canvas_step = stack.command(stack.count() - 2)
    table_step = stack.command(stack.count() - 1)
    assert canvas_step is not None
    assert table_step is not None
    assert canvas_step.text() == table_step.text()


def _choices_role() -> int:
    from satisplanner.ui.table_panel import _ROLE_CHOICES

    return _ROLE_CHOICES


# ------------------------------------------------------------------ the report


def test_the_report_shows_both_numbers_side_by_side(window: MainWindow) -> None:
    place_generator(window)
    window.document.solve_now()
    html = window.totals_panel.html()
    assert "MW consommes" in html
    assert "MW produits" in html


def test_the_report_names_the_deficit_and_says_it_brides_nothing(
    window: MainWindow,
) -> None:
    """A lone generator with no fuel: production zero, and the mine still mining."""
    place_generator(window)
    entry = next(
        e
        for e in window.entries
        if e.kind is EntryKind.EXTRACTOR
        and e.class_name == "Desc_OreIron_C"
        and e.extractor_class == "Build_MinerMk3_C"
    )
    window.palette_widget.entryActivated.emit(entry)
    window.document.solve_now()

    report = window.document.report
    assert report is not None
    assert report.has_power_deficit
    html = window.totals_panel.html()
    assert "MW manquants" in html
    assert "ne bride aucun debit" in html
    assert "coupe tout le reseau" in html


def test_the_help_states_that_electricity_is_a_counter(window: MainWindow) -> None:
    """The rule a user would otherwise discover by being surprised."""
    del window
    joined = " ".join(MODELLING_NOTES)
    assert "compteur, pas une contrainte" in joined
    assert "disjoncte" in joined or "reseau" in joined
