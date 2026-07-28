"""The clock, seen and changed the way a user sees and changes it.

The specification is explicit that an overclocked node must be visible without a
click, so what is asserted here is the text on the node, not the field behind it.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtWidgets import QInputDialog, QMenu
from pytestqt.qtbot import QtBot

from satisplanner.core.graph import MachineNode, ResourceNode
from satisplanner.core.models import GameData
from satisplanner.ui.catalogue import EntryKind
from satisplanner.ui.main_window import MainWindow
from satisplanner.ui.table_panel import COLUMN_CLOCK, COLUMN_QUANTITY
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


def place(window: MainWindow, kind: EntryKind, class_name: str) -> str:
    before = {node.id for node in window.document.graph.nodes}
    entry = next(e for e in window.entries if e.kind is kind and e.class_name == class_name)
    window.palette_widget.entryActivated.emit(entry)
    (created,) = {node.id for node in window.document.graph.nodes} - before
    return created


def action_named(menu: QMenu, start: str) -> object:
    for action in menu.actions():
        if action.text().startswith(start):
            return action
    labels = [action.text() for action in menu.actions()]
    msg = f"aucune action ne commence par {start!r} : {labels}"
    raise AssertionError(msg)


# ----------------------------------------------------------------- the node


def test_a_fresh_node_says_nothing_about_its_clock(window: MainWindow) -> None:
    """100 % is the normal case and must not add noise to every node."""
    node_id = place(window, EntryKind.RECIPE, "Recipe_IngotIron_C")
    item = window.scene.nodes[node_id]
    assert "cadence" not in item.subtitle()
    assert item.clock_badge() == ""


def test_an_overclocked_node_says_so_on_its_face(window: MainWindow) -> None:
    node_id = place(window, EntryKind.RECIPE, "Recipe_IngotIron_C")
    assert window.scene.set_clock_speed(node_id, 2.5)

    item = window.scene.nodes[node_id]
    assert "cadence 250 %" in item.subtitle()
    assert item.clock_badge() == "250 %"


def test_a_deposit_shows_its_clock_next_to_its_purity(window: MainWindow) -> None:
    node_id = place(window, EntryKind.EXTRACTOR, "Desc_OreIron_C")
    window.scene.set_clock_speed(node_id, 1.5)
    subtitle = window.scene.nodes[node_id].subtitle()
    assert "gisement" in subtitle
    assert "cadence 150 %" in subtitle


# -------------------------------------------------------------- the gesture


def test_the_context_menu_offers_the_clock_and_sets_it(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The real path: right-click, "Cadence...", type a number."""
    node_id = place(window, EntryKind.RECIPE, "Recipe_IngotIron_C")
    item = window.scene.nodes[node_id]
    monkeypatch.setattr(QInputDialog, "getDouble", lambda *_a, **_k: (180.0, True))

    menu = window.scene.build_context_menu(item.sceneBoundingRect().center(), window)
    assert menu is not None
    action_named(menu, "Cadence").trigger()  # type: ignore[attr-defined]

    node = window.document.graph.node(node_id)
    assert isinstance(node, MachineNode)
    assert node.clock_speed == pytest.approx(1.8)


def test_cancelling_the_dialog_changes_nothing(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    node_id = place(window, EntryKind.RECIPE, "Recipe_IngotIron_C")
    item = window.scene.nodes[node_id]
    monkeypatch.setattr(QInputDialog, "getDouble", lambda *_a, **_k: (180.0, False))
    steps = window.document.undo_stack.count()

    menu = window.scene.build_context_menu(item.sceneBoundingRect().center(), window)
    assert menu is not None
    action_named(menu, "Cadence").trigger()  # type: ignore[attr-defined]

    assert window.document.undo_stack.count() == steps


def test_a_buffer_is_not_offered_a_clock(window: MainWindow) -> None:
    """Nothing throttles a container, and offering the setting would suggest it does."""
    node_id = place(window, EntryKind.STORAGE, "Build_StorageContainerMk1_C")
    item = window.scene.nodes[node_id]
    menu = window.scene.build_context_menu(item.sceneBoundingRect().center(), window)
    assert menu is not None
    assert not any(action.text().startswith("Cadence") for action in menu.actions())


def test_a_clock_change_is_one_undo_step(window: MainWindow) -> None:
    node_id = place(window, EntryKind.RECIPE, "Recipe_IngotIron_C")
    steps = window.document.undo_stack.count()
    assert window.scene.set_clock_speed(node_id, 2.0)
    assert window.document.undo_stack.count() == steps + 1

    window.document.undo_stack.undo()
    node = window.document.graph.node(node_id)
    assert isinstance(node, MachineNode)
    assert node.clock_speed == 1.0


def test_a_clock_outside_the_range_is_refused_on_the_canvas(window: MainWindow) -> None:
    node_id = place(window, EntryKind.RECIPE, "Recipe_IngotIron_C")
    complaints: list[str] = []
    window.scene.selectionSummaryChanged.connect(complaints.append)

    assert window.scene.set_clock_speed(node_id, 4.0) is False
    node = window.document.graph.node(node_id)
    assert isinstance(node, MachineNode)
    assert node.clock_speed == 1.0, "la valeur en place n'est pas effacée"
    assert complaints and "hors domaine" in complaints[-1]


# --------------------------------------------------------------- the table


def test_the_table_shows_the_clock(window: MainWindow) -> None:
    node_id = place(window, EntryKind.RECIPE, "Recipe_IngotIron_C")
    window.scene.set_clock_speed(node_id, 2.5)

    model = window.table_panel.model
    row = model.row_of(node_id)
    assert row is not None
    assert model.index(row, COLUMN_CLOCK).data() == "250 %"


def test_a_node_without_a_clock_shows_a_dash(window: MainWindow) -> None:
    node_id = place(window, EntryKind.OUTPUT, "Desc_IronIngot_C")
    model = window.table_panel.model
    row = model.row_of(node_id)
    assert row is not None
    assert model.index(row, COLUMN_CLOCK).data() == "—"
    flags = model.flags(model.index(row, COLUMN_CLOCK))
    assert not flags & Qt.ItemFlag.ItemIsEditable


def test_editing_the_clock_in_the_table_goes_through_the_undo_stack(window: MainWindow) -> None:
    node_id = place(window, EntryKind.EXTRACTOR, "Desc_OreIron_C")
    model = window.table_panel.model
    row = model.row_of(node_id)
    assert row is not None

    assert model.setData(model.index(row, COLUMN_CLOCK), 250.0)
    node = window.document.graph.node(node_id)
    assert isinstance(node, ResourceNode)
    assert node.clock_speed == pytest.approx(2.5)

    window.document.undo_stack.undo()
    assert node.clock_speed == pytest.approx(1.0)


def test_the_table_refuses_a_clock_out_of_range_without_erasing_it(window: MainWindow) -> None:
    node_id = place(window, EntryKind.RECIPE, "Recipe_IngotIron_C")
    model = window.table_panel.model
    row = model.row_of(node_id)
    assert row is not None
    model.setData(model.index(row, COLUMN_CLOCK), 150.0)

    for refused in (400.0, 0.0, "beaucoup"):
        assert model.setData(model.index(row, COLUMN_CLOCK), refused) is False
    node = window.document.graph.node(node_id)
    assert isinstance(node, MachineNode)
    assert node.clock_speed == pytest.approx(1.5), "la valeur precedente survit"


def test_the_two_paths_produce_the_same_thing(window: MainWindow) -> None:
    """The table and the canvas must not be two different ways of being right."""
    first = place(window, EntryKind.RECIPE, "Recipe_IngotIron_C")
    second = place(window, EntryKind.RECIPE, "Recipe_IngotCopper_C")

    window.scene.set_clock_speed(first, 1.75)
    model = window.table_panel.model
    row = model.row_of(second)
    assert row is not None
    model.setData(model.index(row, COLUMN_CLOCK), 175.0)

    left, right = window.document.graph.node(first), window.document.graph.node(second)
    assert isinstance(left, MachineNode)
    assert isinstance(right, MachineNode)
    assert left.clock_speed == right.clock_speed


def test_the_quantity_column_still_edits_the_machine_count(window: MainWindow) -> None:
    """The clock is a new column, not a replacement for the one beside it."""
    node_id = place(window, EntryKind.RECIPE, "Recipe_IngotIron_C")
    model = window.table_panel.model
    row = model.row_of(node_id)
    assert row is not None
    assert model.setData(model.index(row, COLUMN_QUANTITY), 3.0)
    node = window.document.graph.node(node_id)
    assert isinstance(node, MachineNode)
    assert node.machine_count == 3.0
    assert node.clock_speed == 1.0


# --------------------------------------------------------------- the totals


def test_the_totals_panel_lists_the_shards(window: MainWindow) -> None:
    node_id = place(window, EntryKind.EXTRACTOR, "Desc_OreIron_C")
    window.scene.set_clock_speed(node_id, 2.5)
    window.document.solve_now()

    html = window.totals_panel.html()
    assert "Éclat de charge" in html or "clat de charge" in html
    assert "Liste de courses" in html


def test_the_canvas_drops_a_node_at_full_speed(window: MainWindow) -> None:
    """A node placed from the palette starts at 100 %, whatever else is on screen."""
    window.scene.add_entry(
        next(e for e in window.entries if e.kind is EntryKind.RECIPE), QPointF(0.0, 0.0)
    )
    placed = window.document.graph.sorted_nodes()[-1]
    assert getattr(placed, "clock_speed", None) == 1.0
