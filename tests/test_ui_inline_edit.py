"""Editing a value on the node itself, by double-clicking it.

The test that carries the whole point is :func:`test_the_three_doors_push_the_same_command`.
There are now three ways to change a field -- the context menu, the table cell and a
double-click -- and what makes that safe is not three careful implementations but one
implementation reached three ways. The test compares the **labels of the commands
that landed on the undo stack**, which is the only thing that can prove it.

Everything else here is the contract the editor promises: Enter validates, Escape
cancels, an out-of-domain value is refused *without erasing what was typed*, and a
discrete field opens the same list the table's delegate does rather than a free-text
box.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QComboBox, QLineEdit
from pytestqt.qtbot import QtBot

from satisplanner.core.graph import (
    ExternalSourceNode,
    GeneratorNode,
    MachineNode,
    ResourceNode,
    StorageNode,
)
from satisplanner.core.models import GameData, Purity
from satisplanner.ui.canvas_items import Field
from satisplanner.ui.catalogue import EntryKind, extractor_choices
from satisplanner.ui.main_window import MainWindow
from satisplanner.ui.table_panel import COLUMN_PURITY, COLUMN_QUANTITY
from tests.conftest import temporary_settings


@pytest.fixture
def window(qtbot: QtBot, game_data: GameData, tmp_path: Path) -> Iterator[MainWindow]:
    """See ``test_ui_smoke`` for why this is not handed to ``qtbot.addWidget``."""
    del qtbot
    built = MainWindow(game_data, settings=temporary_settings(tmp_path))
    built.show()
    yield built
    built.view.inline.close()
    built.document.undo_stack.setClean()
    built.scene.dispose()
    built.close()
    built.deleteLater()


def place(window: MainWindow, kind: EntryKind, class_name: str, **extra: str) -> str:
    """Drop an entry and move it clear of the others.

    Everything the palette places lands at the centre of the view, so two nodes sit
    on top of each other and a point hits whichever happens to be on top. Hit-testing
    is the whole subject here, so each node is given a place of its own.
    """
    before = {node.id for node in window.document.graph.nodes}
    entry = next(
        e
        for e in window.entries
        if e.kind is kind
        and e.class_name == class_name
        and all(getattr(e, key) == value for key, value in extra.items())
    )
    window.palette_widget.entryActivated.emit(entry)
    (created,) = {node.id for node in window.document.graph.nodes} - before
    window.document.graph.node(created).position = (0.0, 400.0 * len(before))
    window.scene.rebuild()
    return created


def open_editor(window: MainWindow, node_id: str, field: Field) -> None:
    """Through the scene, on the point where the value is actually drawn.

    Not by calling the editor with a field name: the thing being checked is that a
    double-click *on that text* finds *that field*.
    """
    item = window.scene.nodes[node_id]
    rect = item.field_rect(field)
    at = item.mapToScene(rect.center())
    request = window.scene.edit_request_at(at)
    assert request is not None, f"rien a editer sous {field}"
    assert request[0] == node_id
    assert request[1] is field, f"double-clic sur {field} : {request[1]} trouve"
    window.scene.inlineEditRequested.emit(*request)


# ------------------------------------------------------- what the cursor finds


def test_a_double_click_finds_the_value_under_it(window: MainWindow) -> None:
    """A deposit shows four values on one line; each has to answer for itself."""
    node_id = place(
        window, EntryKind.EXTRACTOR, "Desc_OreIron_C", extractor_class="Build_MinerMk3_C"
    )
    assert window.scene.set_clock_speed(node_id, 2.5)
    item = window.scene.nodes[node_id]

    for field in (Field.QUANTITY, Field.EXTRACTOR, Field.PURITY, Field.CLOCK):
        centre = item.field_rect(field).center()
        assert item.field_at(centre) is field, field


def test_the_title_is_not_a_value(window: MainWindow) -> None:
    """Double-clicking the recipe name does nothing, because nothing is editable there."""
    node_id = place(window, EntryKind.RECIPE, "Recipe_IngotIron_C")
    item = window.scene.nodes[node_id]
    assert item.field_at(QPointF(60.0, 12.0)) is None
    assert window.scene.edit_request_at(item.mapToScene(QPointF(60.0, 12.0))) is None


def test_the_subtitle_still_reads_as_one_sentence(window: MainWindow) -> None:
    """Cutting it into segments must not change a character of what is shown."""
    node_id = place(
        window, EntryKind.EXTRACTOR, "Desc_OreIron_C", extractor_class="Build_MinerMk3_C"
    )
    assert window.scene.set_purity(node_id, Purity.PURE)
    assert window.scene.set_clock_speed(node_id, 2.5)
    item = window.scene.nodes[node_id]
    assert item.subtitle() == "1 Foreuse Mk.3 — gisement pur — cadence 250 %"
    assert "".join(s.text for s in item.subtitle_segments()) == item.subtitle()


def test_a_clock_at_one_hundred_percent_has_nothing_to_click(window: MainWindow) -> None:
    """Consistent rather than a gap: an unshown value is not a shown value."""
    node_id = place(window, EntryKind.RECIPE, "Recipe_IngotIron_C")
    item = window.scene.nodes[node_id]
    assert Field.CLOCK not in {s.field for s in item.subtitle_segments()}
    # ...and the menu and the table still reach it.
    assert window.scene.set_clock_speed(node_id, 0.5)
    assert Field.CLOCK in {s.field for s in window.scene.nodes[node_id].subtitle_segments()}


# ------------------------------------------------------------- typing a number


def test_enter_validates_a_typed_number(window: MainWindow) -> None:
    node_id = place(window, EntryKind.RECIPE, "Recipe_IngotIron_C")
    open_editor(window, node_id, Field.QUANTITY)

    editor = window.view.inline.widget
    assert isinstance(editor, QLineEdit)
    editor.setText("4,5")
    QTest.keyClick(editor, Qt.Key.Key_Return)

    node = window.document.graph.node(node_id)
    assert isinstance(node, MachineNode)
    assert node.machine_count == 4.5
    assert not window.view.inline.is_open(), "l'editeur se referme quand c'est accepte"


def test_a_french_comma_is_a_decimal_point(window: MainWindow) -> None:
    """The whole interface writes 4,33; refusing to read it back would be absurd."""
    node_id = place(window, EntryKind.RECIPE, "Recipe_IngotIron_C")
    open_editor(window, node_id, Field.QUANTITY)
    editor = window.view.inline.widget
    assert isinstance(editor, QLineEdit)
    editor.setText("4,33")
    QTest.keyClick(editor, Qt.Key.Key_Return)
    node = window.document.graph.node(node_id)
    assert isinstance(node, MachineNode)
    assert node.machine_count == pytest.approx(4.33)


def test_escape_cancels_and_writes_nothing(window: MainWindow) -> None:
    node_id = place(window, EntryKind.RECIPE, "Recipe_IngotIron_C")
    depth = window.document.undo_stack.count()
    open_editor(window, node_id, Field.QUANTITY)

    editor = window.view.inline.widget
    assert isinstance(editor, QLineEdit)
    editor.setText("99")
    QTest.keyClick(editor, Qt.Key.Key_Escape)

    node = window.document.graph.node(node_id)
    assert isinstance(node, MachineNode)
    assert node.machine_count == 1.0
    assert window.document.undo_stack.count() == depth, "rien n'a été empile"
    assert not window.view.inline.is_open()


def test_an_out_of_domain_value_is_refused_without_erasing_it(window: MainWindow) -> None:
    """400 % is refused, and 400 stays in the box so it can be seen and corrected.

    Clearing the field would lose both the value and any chance of understanding
    what was wrong with it.
    """
    node_id = place(window, EntryKind.RECIPE, "Recipe_IngotIron_C")
    assert window.scene.set_clock_speed(node_id, 2.0)
    complaints: list[str] = []
    window.scene.selectionSummaryChanged.connect(complaints.append)
    open_editor(window, node_id, Field.CLOCK)

    editor = window.view.inline.widget
    assert isinstance(editor, QLineEdit)
    editor.setText("400")
    QTest.keyClick(editor, Qt.Key.Key_Return)

    assert window.view.inline.is_open(), "l'editeur reste ouvert"
    assert editor.text() == "400", "ce qui a été tape est toujours la"
    node = window.document.graph.node(node_id)
    assert isinstance(node, MachineNode)
    assert node.clock_speed == 2.0, "la valeur precedente n'a pas bouge"
    assert complaints and "hors domaine" in complaints[-1]


def test_text_that_is_not_a_number_is_refused_the_same_way(window: MainWindow) -> None:
    node_id = place(window, EntryKind.RECIPE, "Recipe_IngotIron_C")
    open_editor(window, node_id, Field.QUANTITY)
    editor = window.view.inline.widget
    assert isinstance(editor, QLineEdit)
    editor.setText("beaucoup")
    QTest.keyClick(editor, Qt.Key.Key_Return)

    assert window.view.inline.is_open()
    node = window.document.graph.node(node_id)
    assert isinstance(node, MachineNode)
    assert node.machine_count == 1.0


# ---------------------------------------------------------- discrete fields


def test_a_discrete_field_opens_a_list_not_a_text_box(window: MainWindow) -> None:
    """Expecting "impure" to be typed was a trap; it would be the same trap here."""
    node_id = place(
        window, EntryKind.EXTRACTOR, "Desc_OreIron_C", extractor_class="Build_MinerMk3_C"
    )
    open_editor(window, node_id, Field.PURITY)
    combo = window.view.inline.widget
    assert isinstance(combo, QComboBox)
    assert [combo.itemText(index) for index in range(combo.count())] == ["Impur", "Normal", "Pur"]
    assert combo.currentData() == "normal", "l'état courant est selectionne"


def test_the_extractor_list_is_the_one_the_table_offers(window: MainWindow) -> None:
    node_id = place(
        window, EntryKind.EXTRACTOR, "Desc_OreIron_C", extractor_class="Build_MinerMk3_C"
    )
    open_editor(window, node_id, Field.EXTRACTOR)
    combo = window.view.inline.widget
    assert isinstance(combo, QComboBox)
    offered = [(combo.itemData(i), combo.itemText(i)) for i in range(combo.count())]
    assert offered == extractor_choices(window.game_data, "Desc_OreIron_C")


def test_choosing_from_the_list_applies_it(window: MainWindow) -> None:
    node_id = place(
        window, EntryKind.EXTRACTOR, "Desc_OreIron_C", extractor_class="Build_MinerMk3_C"
    )
    open_editor(window, node_id, Field.PURITY)
    combo = window.view.inline.widget
    assert isinstance(combo, QComboBox)
    combo.setCurrentIndex(combo.findData("pure"))
    combo.activated.emit(combo.currentIndex())

    node = window.document.graph.node(node_id)
    assert isinstance(node, ResourceNode)
    assert node.purity is Purity.PURE
    assert not window.view.inline.is_open()


def test_a_generator_fuel_is_editable_where_it_is_drawn(window: MainWindow) -> None:
    node_id = place(window, EntryKind.GENERATOR, "Build_GeneratorCoal_C")
    open_editor(window, node_id, Field.FUEL)
    combo = window.view.inline.widget
    assert isinstance(combo, QComboBox)
    combo.setCurrentIndex(combo.findData("Desc_PetroleumCoke_C"))
    combo.activated.emit(combo.currentIndex())

    node = window.document.graph.node(node_id)
    assert isinstance(node, GeneratorNode)
    assert node.fuel_class == "Desc_PetroleumCoke_C"


# ----------------------------------------------------------- the other fields


def test_an_external_rate_is_editable_on_the_node(window: MainWindow) -> None:
    node_id = place(window, EntryKind.EXTERNAL, "Desc_OreIron_C")
    open_editor(window, node_id, Field.QUANTITY)
    editor = window.view.inline.widget
    assert isinstance(editor, QLineEdit)
    editor.setText("120")
    QTest.keyClick(editor, Qt.Key.Key_Return)

    node = window.document.graph.node(node_id)
    assert isinstance(node, ExternalSourceNode)
    assert node.rate_per_minute == 120.0


def test_a_buffer_stock_is_editable_even_when_it_is_zero(window: MainWindow) -> None:
    """It used to be hidden at zero, which made it the one value with nothing to click."""
    node_id = place(window, EntryKind.STORAGE, "Build_StorageContainerMk1_C")
    deposit = place(
        window, EntryKind.EXTRACTOR, "Desc_OreIron_C", extractor_class="Build_MinerMk3_C"
    )
    assert window.scene.connect_nodes(deposit, node_id, "Desc_OreIron_C") is None

    assert "stock 0" in window.scene.nodes[node_id].subtitle()
    open_editor(window, node_id, Field.QUANTITY)
    editor = window.view.inline.widget
    assert isinstance(editor, QLineEdit)
    editor.setText("500")
    QTest.keyClick(editor, Qt.Key.Key_Return)

    node = window.document.graph.node(node_id)
    assert isinstance(node, StorageNode)
    assert node.initial_content == 500.0


def test_a_line_tier_is_editable_by_double_clicking_the_line(window: MainWindow) -> None:
    deposit = place(
        window, EntryKind.EXTRACTOR, "Desc_OreIron_C", extractor_class="Build_MinerMk3_C"
    )
    smelter = place(window, EntryKind.RECIPE, "Recipe_IngotIron_C")
    assert window.scene.connect_nodes(deposit, smelter, "Desc_OreIron_C") is None
    window.scene.refresh_edge_geometry()

    edge_item = window.scene.edges["e1"]
    at = edge_item.path().pointAtPercent(0.5)
    request = window.scene.edit_request_at(at)
    assert request is not None
    assert request[:2] == ("e1", Field.TRANSPORT)

    window.scene.inlineEditRequested.emit(*request)
    combo = window.view.inline.widget
    assert isinstance(combo, QComboBox)
    combo.setCurrentIndex(combo.findData("Build_ConveyorBeltMk6_C"))
    combo.activated.emit(combo.currentIndex())
    assert window.document.graph.edge("e1").transport_class == "Build_ConveyorBeltMk6_C"


# ------------------------------------------------------------ the three doors


def test_the_three_doors_push_the_same_command(window: MainWindow) -> None:
    """One implementation reached three ways, checked on the commands themselves.

    Three deposits, the same change made from the context menu, from the table and
    from a double-click. If the three labels match, they went through one function.
    """
    first = place(window, EntryKind.EXTRACTOR, "Desc_OreIron_C", extractor_class="Build_MinerMk3_C")
    second = place(
        window, EntryKind.EXTRACTOR, "Desc_OreIron_C", extractor_class="Build_MinerMk3_C"
    )
    third = place(window, EntryKind.EXTRACTOR, "Desc_OreIron_C", extractor_class="Build_MinerMk3_C")

    # Door one: the canvas menu.
    window.scene.set_purity(first, Purity.PURE)
    # Door two: the table cell.
    model = window.table_panel.model
    row = model.row_of(second)
    assert row is not None
    model.setData(model.index(row, COLUMN_PURITY), "pure")
    # Door three: a double-click on the word "normal" written on the node.
    open_editor(window, third, Field.PURITY)
    combo = window.view.inline.widget
    assert isinstance(combo, QComboBox)
    combo.setCurrentIndex(combo.findData("pure"))
    combo.activated.emit(combo.currentIndex())

    stack = window.document.undo_stack
    labels = [stack.command(stack.count() - index).text() for index in (3, 2, 1)]
    assert len(set(labels)) == 1, f"trois libellés différents : {labels}"
    for node_id in (first, second, third):
        node = window.document.graph.node(node_id)
        assert isinstance(node, ResourceNode)
        assert node.purity is Purity.PURE


def test_the_three_doors_agree_on_a_number_too(window: MainWindow) -> None:
    """The quantity used to be the table's own command; now it is everyone's."""
    first = place(window, EntryKind.RECIPE, "Recipe_IngotIron_C")
    second = place(window, EntryKind.RECIPE, "Recipe_IngotIron_C")
    third = place(window, EntryKind.RECIPE, "Recipe_IngotIron_C")

    window.scene.set_quantity(first, 6.0)
    model = window.table_panel.model
    row = model.row_of(second)
    assert row is not None
    model.setData(model.index(row, COLUMN_QUANTITY), 6.0)
    open_editor(window, third, Field.QUANTITY)
    editor = window.view.inline.widget
    assert isinstance(editor, QLineEdit)
    editor.setText("6")
    QTest.keyClick(editor, Qt.Key.Key_Return)

    stack = window.document.undo_stack
    labels = [stack.command(stack.count() - index).text() for index in (3, 2, 1)]
    # The identifiers differ, so the labels do; what must match is everything else.
    suffixes = {label.split(" : ", 1)[1] for label in labels}
    assert suffixes == {"6 machine(s)"}, labels


def test_an_inline_edit_is_one_undo_step(window: MainWindow) -> None:
    node_id = place(window, EntryKind.RECIPE, "Recipe_IngotIron_C")
    depth = window.document.undo_stack.count()
    open_editor(window, node_id, Field.QUANTITY)
    editor = window.view.inline.widget
    assert isinstance(editor, QLineEdit)
    editor.setText("7")
    QTest.keyClick(editor, Qt.Key.Key_Return)

    assert window.document.undo_stack.count() == depth + 1
    window.document.undo_stack.undo()
    node = window.document.graph.node(node_id)
    assert isinstance(node, MachineNode)
    assert node.machine_count == 1.0


def test_an_edit_triggers_the_recomputation(window: MainWindow) -> None:
    """The point of editing on the node: the figures follow immediately."""
    deposit = place(
        window, EntryKind.EXTRACTOR, "Desc_OreIron_C", extractor_class="Build_MinerMk3_C"
    )
    exit_node = place(window, EntryKind.OUTPUT, "Desc_OreIron_C")
    assert window.scene.connect_nodes(deposit, exit_node, "Desc_OreIron_C") is None
    # Read on the line's uncapped rate: the default belt is a Mk.1, and what is being
    # checked here is the extraction, not the conveyor.
    assert window.document.solve_now().edge("e1").desired_rate_per_minute == 240.0

    open_editor(window, deposit, Field.PURITY)
    combo = window.view.inline.widget
    assert isinstance(combo, QComboBox)
    combo.setCurrentIndex(combo.findData("pure"))
    combo.activated.emit(combo.currentIndex())

    assert window.document.solve_now().edge("e1").desired_rate_per_minute == 480.0


def test_a_click_elsewhere_abandons_an_open_edit(window: MainWindow) -> None:
    node_id = place(window, EntryKind.RECIPE, "Recipe_IngotIron_C")
    open_editor(window, node_id, Field.QUANTITY)
    assert window.view.inline.is_open()

    QTest.mouseClick(window.view.viewport(), Qt.MouseButton.LeftButton)
    assert not window.view.inline.is_open()
    node = window.document.graph.node(node_id)
    assert isinstance(node, MachineNode)
    assert node.machine_count == 1.0
