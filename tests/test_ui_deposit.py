"""A deposit's purity and its extractor: readable on the node, changeable on it.

Both have been in the model since phase 2 and reachable from nowhere, which for a
user is the same as not existing. What is asserted here is therefore the two things
that were missing: that the node says which purity it is *without a click*, and that
the two doors -- the context menu and the table -- push the same command.

The modelling rule the help states is checked too: a purity belongs to the deposit,
so it multiplies every extractor standing on it. Two purities are two nodes.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtWidgets import QMenu
from pytestqt.qtbot import QtBot

from satisplanner.core import engine
from satisplanner.core.graph import FactoryGraph, OutputNode, ResourceNode
from satisplanner.core.models import GameData, Purity
from satisplanner.ui.catalogue import EntryKind, extractor_choices
from satisplanner.ui.main_window import MainWindow
from satisplanner.ui.table_panel import (
    COLUMN_CLOCK,
    COLUMN_EXTRACTOR,
    COLUMN_PURITY,
    ChoiceDelegate,
)
from tests.conftest import temporary_settings


@pytest.fixture
def window(qtbot: QtBot, game_data: GameData, tmp_path: Path) -> Iterator[MainWindow]:
    """See ``test_ui_smoke`` for why this is not handed to ``qtbot.addWidget``."""
    del qtbot
    built = MainWindow(game_data, settings=temporary_settings(tmp_path))
    yield built
    built.dispose()
    built.close()
    built.deleteLater()


def place_deposit(window: MainWindow, item_class: str = "Desc_OreIron_C") -> str:
    before = {node.id for node in window.document.graph.nodes}
    entry = next(
        e
        for e in window.entries
        if e.kind is EntryKind.EXTRACTOR
        and e.class_name == item_class
        and e.extractor_class == "Build_MinerMk3_C"
    )
    window.palette_widget.entryActivated.emit(entry)
    (created,) = {node.id for node in window.document.graph.nodes} - before
    return created


def submenu(menu: QMenu, title: str) -> QMenu:
    """The child menu whose title starts with ``title``, or a readable failure."""
    children = [action.menu() for action in menu.actions()]
    found = [child for child in children if isinstance(child, QMenu)]
    for child in found:
        if child.title().startswith(title):
            return child
    msg = f"aucun sous-menu {title!r} : {[child.title() for child in found]}"
    raise AssertionError(msg)


# ------------------------------------------------------------------ the rates


@pytest.mark.parametrize(
    ("purity", "expected"),
    [(Purity.IMPURE, 120.0), (Purity.NORMAL, 240.0), (Purity.PURE, 480.0)],
)
def test_a_mk3_miner_follows_the_purity_of_its_node(
    game_data: GameData, purity: Purity, expected: float
) -> None:
    """The control table: 120, 240, 480 on the same miner."""
    graph = FactoryGraph()
    graph.add_node(
        ResourceNode(
            id="gisement",
            item_class="Desc_OreIron_C",
            extractor_class="Build_MinerMk3_C",
            purity=purity,
        )
    )
    graph.add_node(OutputNode(id="sortie", item_class="Desc_OreIron_C"))
    graph.connect("gisement", "sortie", "Desc_OreIron_C", "Build_ConveyorBeltMk6_C", game_data)

    report = engine.solve(graph, game_data)
    assert report.node("gisement").outputs["Desc_OreIron_C"] == pytest.approx(expected)


def test_purity_and_clock_compose(game_data: GameData) -> None:
    """480 on a pure node, 1200 at 250 %: the §3.3 figure, reached the other way."""
    graph = FactoryGraph()
    graph.add_node(
        ResourceNode(
            id="gisement",
            item_class="Desc_OreIron_C",
            extractor_class="Build_MinerMk3_C",
            purity=Purity.PURE,
            clock_speed=2.5,
        )
    )
    graph.add_node(OutputNode(id="sortie", item_class="Desc_OreIron_C"))
    graph.connect("gisement", "sortie", "Desc_OreIron_C", "Build_ConveyorBeltMk6_C", game_data)
    report = engine.solve(graph, game_data)
    assert report.node("gisement").outputs["Desc_OreIron_C"] == pytest.approx(1200.0)


def test_the_purity_multiplies_every_extractor_on_the_node(game_data: GameData) -> None:
    """A node is one deposit: three miners on a pure node each pull the pure rate.

    Two deposits of different purities are two nodes, and there is no way to say
    anything else -- which is the point, not a gap.
    """
    graph = FactoryGraph()
    graph.add_node(
        ResourceNode(
            id="gisement",
            item_class="Desc_OreIron_C",
            extractor_class="Build_MinerMk3_C",
            purity=Purity.PURE,
            count=3.0,
        )
    )
    graph.add_node(OutputNode(id="sortie", item_class="Desc_OreIron_C"))
    graph.connect("gisement", "sortie", "Desc_OreIron_C", "Build_ConveyorBeltMk6_C", game_data)
    report = engine.solve(graph, game_data)
    # 3 x 480, capped by the Mk.6 belt at 1200 -- the flow is capped, the offer is not.
    assert report.edge("e1").desired_rate_per_minute == pytest.approx(1440.0)


# -------------------------------------------------------------- on the node


def test_a_fresh_deposit_says_its_purity_without_a_click(window: MainWindow) -> None:
    node_id = place_deposit(window)
    subtitle = window.scene.nodes[node_id].subtitle()
    assert "Foreuse Mk.3" in subtitle
    assert "gisement normal" in subtitle, "la pureté par défaut est lisible d'emblee"


def test_changing_the_purity_shows_on_the_node(window: MainWindow) -> None:
    node_id = place_deposit(window)
    assert window.scene.set_purity(node_id, Purity.PURE)
    assert "gisement pur" in window.scene.nodes[node_id].subtitle()


def test_changing_the_extractor_shows_on_the_node(window: MainWindow) -> None:
    node_id = place_deposit(window)
    assert window.scene.set_extractor(node_id, "Build_MinerMk1_C")
    assert "Foreuse Mk.1" in window.scene.nodes[node_id].subtitle()


# ----------------------------------------------------------- the two doors


def test_the_context_menu_offers_the_three_purities(window: MainWindow) -> None:
    node_id = place_deposit(window)
    item = window.scene.nodes[node_id]
    menu = window.scene.build_context_menu(item.sceneBoundingRect().center(), window)
    assert menu is not None

    purities = submenu(menu, "Pureté")
    assert [action.text() for action in purities.actions()] == ["Impur", "Normal", "Pur"]
    checked = [action.text() for action in purities.actions() if action.isChecked()]
    assert checked == ["Normal"], "l'état courant se lit dans le menu"

    next(a for a in purities.actions() if a.text() == "Pur").trigger()
    node = window.document.graph.node(node_id)
    assert isinstance(node, ResourceNode)
    assert node.purity is Purity.PURE


def test_the_context_menu_swaps_the_miner_without_losing_the_lines(window: MainWindow) -> None:
    """The whole point: changing a Mk.2 for a Mk.3 must not mean rebuilding."""
    node_id = place_deposit(window)
    smelter_before = {node.id for node in window.document.graph.nodes}
    entry = next(
        e
        for e in window.entries
        if e.kind is EntryKind.RECIPE and e.class_name == "Recipe_IngotIron_C"
    )
    window.palette_widget.entryActivated.emit(entry)
    (smelter,) = {node.id for node in window.document.graph.nodes} - smelter_before
    assert window.scene.connect_nodes(node_id, smelter, "Desc_OreIron_C") is None
    # Both were dropped in the middle of the view and sit on top of each other; the
    # menu is asked for at a point, so they have to be told apart first.
    window.scene.nodes[smelter].setPos(QPointF(600.0, 0.0))

    item = window.scene.nodes[node_id]
    menu = window.scene.build_context_menu(item.sceneBoundingRect().center(), window)
    assert menu is not None
    extractors = submenu(menu, "Extracteur")
    next(a for a in extractors.actions() if a.text() == "Foreuse Mk.1").trigger()

    node = window.document.graph.node(node_id)
    assert isinstance(node, ResourceNode)
    assert node.extractor_class == "Build_MinerMk1_C"
    assert len(window.document.graph.edges) == 1, "la ligne a survecu"


def test_an_extractor_that_cannot_work_this_ore_is_refused(window: MainWindow) -> None:
    node_id = place_deposit(window)
    complaints: list[str] = []
    window.scene.selectionSummaryChanged.connect(complaints.append)

    assert window.scene.set_extractor(node_id, "Build_WaterPump_C") is False
    node = window.document.graph.node(node_id)
    assert isinstance(node, ResourceNode)
    assert node.extractor_class == "Build_MinerMk3_C", "rien n'a bouge"
    assert complaints and "ne peut pas travailler" in complaints[-1]


def test_the_table_shows_and_edits_the_purity(window: MainWindow) -> None:
    node_id = place_deposit(window)
    model = window.table_panel.model
    row = model.row_of(node_id)
    assert row is not None
    assert model.index(row, COLUMN_PURITY).data() == "Normal"

    assert model.setData(model.index(row, COLUMN_PURITY), "pure")
    node = window.document.graph.node(node_id)
    assert isinstance(node, ResourceNode)
    assert node.purity is Purity.PURE
    assert model.index(row, COLUMN_PURITY).data() == "Pur"

    window.document.undo_stack.undo()
    # Read again rather than reusing the narrowed reference: what is being asserted
    # is what the graph now holds.
    restored = window.document.graph.node(node_id)
    assert isinstance(restored, ResourceNode)
    assert restored.purity is Purity.NORMAL


def test_the_table_shows_and_edits_the_extractor(window: MainWindow) -> None:
    node_id = place_deposit(window)
    model = window.table_panel.model
    row = model.row_of(node_id)
    assert row is not None
    assert model.index(row, COLUMN_EXTRACTOR).data() == "Foreuse Mk.3"

    assert model.setData(model.index(row, COLUMN_EXTRACTOR), "Build_MinerMk2_C")
    node = window.document.graph.node(node_id)
    assert isinstance(node, ResourceNode)
    assert node.extractor_class == "Build_MinerMk2_C"


def test_the_table_refuses_a_nonsense_value_without_erasing_the_cell(window: MainWindow) -> None:
    node_id = place_deposit(window)
    model = window.table_panel.model
    row = model.row_of(node_id)
    assert row is not None

    assert model.setData(model.index(row, COLUMN_PURITY), "tres pur") is False
    assert model.setData(model.index(row, COLUMN_EXTRACTOR), "Build_WaterPump_C") is False
    node = window.document.graph.node(node_id)
    assert isinstance(node, ResourceNode)
    assert node.purity is Purity.NORMAL
    assert node.extractor_class == "Build_MinerMk3_C"


def test_the_two_doors_push_the_same_command(window: MainWindow) -> None:
    """Not two implementations agreeing: one implementation, reached two ways."""
    first = place_deposit(window)
    second = place_deposit(window)

    window.scene.set_purity(first, Purity.IMPURE)
    model = window.table_panel.model
    row = model.row_of(second)
    assert row is not None
    model.setData(model.index(row, COLUMN_PURITY), "impure")

    left, right = window.document.graph.node(first), window.document.graph.node(second)
    assert isinstance(left, ResourceNode)
    assert isinstance(right, ResourceNode)
    assert left.purity is right.purity

    canvas_step = window.document.undo_stack.command(window.document.undo_stack.count() - 2)
    table_step = window.document.undo_stack.command(window.document.undo_stack.count() - 1)
    assert canvas_step is not None
    assert table_step is not None
    assert canvas_step.text() == table_step.text(), "la même commande, au même libellé"


def test_a_machine_row_offers_no_purity(window: MainWindow) -> None:
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
    assert model.index(row, COLUMN_PURITY).data() == "—"
    assert not model.flags(model.index(row, COLUMN_PURITY)) & Qt.ItemFlag.ItemIsEditable
    # ...but it does have a clock, which is the column beside it.
    assert model.flags(model.index(row, COLUMN_CLOCK)) & Qt.ItemFlag.ItemIsEditable


def test_the_cells_are_chosen_from_a_list_not_typed(window: MainWindow) -> None:
    """A line edit expecting the reader to spell "impure" would be a trap."""
    node_id = place_deposit(window)
    view = window.table_panel.view
    assert isinstance(view.itemDelegateForColumn(COLUMN_PURITY), ChoiceDelegate)
    assert isinstance(view.itemDelegateForColumn(COLUMN_EXTRACTOR), ChoiceDelegate)

    model = window.table_panel.model
    row = model.row_of(node_id)
    assert row is not None
    choices = model.data(model.index(row, COLUMN_EXTRACTOR), _choices_role())
    assert dict(choices) == dict(extractor_choices(window.game_data, "Desc_OreIron_C"))
    assert "Pompe" not in " ".join(label for _, label in choices)


def _choices_role() -> int:
    from satisplanner.ui.table_panel import _ROLE_CHOICES

    return _ROLE_CHOICES


# ------------------------------------------------------------- the fast path


def test_enter_places_the_highlighted_entry(window: MainWindow) -> None:
    """The gesture that replaced the double-click, driven through a real key press."""
    from PySide6.QtTest import QTest

    window.show()
    palette = window.palette_widget
    palette.search.setText("Plaque de fer")
    index = next(
        palette.model.index(row, 0)
        for row in range(palette.model.rowCount())
        if palette.model.entry_at(palette.model.index(row, 0)) is not None
    )
    palette.list.setCurrentIndex(index)

    QTest.keyClick(palette.list, Qt.Key.Key_Return)

    assert len(window.document.graph.nodes) == 1
    assert window.item_card is None, "Entrée pose, elle n'ouvre pas la fiche"
