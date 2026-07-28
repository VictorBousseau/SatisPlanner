"""Deployed rendering: a way of looking at a factory, and nothing more.

The arithmetic of the thumbnail row is a pure function and is checked as one. What
the window adds is the resolution of three states -- follow the preference, always,
never -- and the property that matters most: **turning it on changes no number**.
The report of a factory drawn deployed is byte-identical to the report of the same
factory drawn plainly, and that is what the last test here asserts.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot

from satisplanner.core.graph import GeneratorNode, MachineNode, ResourceNode
from satisplanner.core.models import GameData
from satisplanner.ui.canvas_items import DEPLOYED_MIN_FRACTION, deployed_layout
from satisplanner.ui.catalogue import EntryKind
from satisplanner.ui.main_window import MainWindow
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


def place(window: MainWindow, kind: EntryKind, class_name: str, **extra: str) -> str:
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
    return created


# ---------------------------------------------------------------- the arithmetic


def test_a_whole_bank_is_one_thumbnail_each() -> None:
    plan = deployed_layout(4.0, ceiling=12)
    assert (plan.full, plan.fraction, plan.truncated) == (4, 0.0, False)
    assert plan.drawn == 4


def test_a_fractional_machine_is_a_fraction_of_a_thumbnail() -> None:
    """4,33 assembleuses, ce sont quatre et un tiers -- pas cinq."""
    plan = deployed_layout(4.33, ceiling=12)
    assert plan.full == 4
    assert plan.fraction == pytest.approx(0.33)
    assert plan.drawn == 5


def test_a_sliver_is_not_drawn_at_all() -> None:
    """Two pixels of an icon read as a glitch, not as a hundredth of a machine."""
    plan = deployed_layout(4 + DEPLOYED_MIN_FRACTION / 2, ceiling=12)
    assert plan.full == 4
    assert plan.fraction == 0.0


def test_past_the_ceiling_the_number_replaces_the_picture() -> None:
    plan = deployed_layout(43.0, ceiling=12)
    assert plan.full == 12
    assert plan.truncated
    assert plan.total == 43.0


def test_the_ceiling_is_a_ceiling_and_not_a_rounding() -> None:
    assert deployed_layout(12.0, ceiling=12).truncated
    assert not deployed_layout(11.5, ceiling=12).truncated


def test_the_thumbnails_wrap_into_a_grid() -> None:
    """A ceiling above what fits across the node has to wrap, not run off it."""
    plan = deployed_layout(20.0, ceiling=24, per_row=12)
    assert plan.rows == 2
    assert plan.cell(0) == (0, 0)
    assert plan.cell(11) == (11, 0)
    assert plan.cell(12) == (0, 1)


def test_a_full_last_row_pushes_the_count_onto_its_own_line() -> None:
    """Twelve thumbnails leave no room for "... x43", so it gets a line.

    Found by looking: the first version wrote the count into the two pixels left at
    the end of a full row, which rendered as an ellipsis and nothing else.
    """
    plan = deployed_layout(43.0, ceiling=12, per_row=12)
    assert plan.drawn == 12
    assert plan.rows == 2
    assert plan.cell(plan.drawn) == (0, 1)


def test_a_partly_filled_last_row_keeps_the_count_beside_it() -> None:
    plan = deployed_layout(43.0, ceiling=10, per_row=12)
    assert plan.rows == 1
    assert plan.cell(plan.drawn) == (10, 0)


# ------------------------------------------------------------------- the window


def test_the_option_is_off_by_default(window: MainWindow) -> None:
    node_id = place(window, EntryKind.RECIPE, "Recipe_IngotIron_C")
    assert not window.preferences.deployed_rendering
    assert not window.scene.nodes[node_id].deployed
    assert window.scene.nodes[node_id].deployed_plan() is None


def test_turning_it_on_deploys_every_bank(window: MainWindow) -> None:
    machine = place(window, EntryKind.RECIPE, "Recipe_IngotIron_C")
    deposit = place(
        window, EntryKind.EXTRACTOR, "Desc_OreIron_C", extractor_class="Build_MinerMk3_C"
    )
    generator = place(window, EntryKind.GENERATOR, "Build_GeneratorCoal_C")

    window.deployed_action.setChecked(True)
    window.toggle_deployed_rendering()

    for node_id in (machine, deposit, generator):
        assert window.scene.nodes[node_id].deployed_plan() is not None, node_id
    # ...and it survives to the next run.
    assert window.preferences.deployed_rendering


def test_a_bank_of_eight_generators_is_eight_thumbnails(window: MainWindow) -> None:
    node_id = place(window, EntryKind.GENERATOR, "Build_GeneratorCoal_C")
    node = window.document.graph.node(node_id)
    assert isinstance(node, GeneratorNode)
    node.count = 8.0
    window.deployed_action.setChecked(True)
    window.toggle_deployed_rendering()

    plan = window.scene.nodes[node_id].deployed_plan()
    assert plan is not None
    assert plan.full == 8


def test_a_node_that_is_not_a_bank_is_never_deployed(window: MainWindow) -> None:
    """A buffer is one thing, not a count of things."""
    node_id = place(window, EntryKind.STORAGE, "Build_StorageContainerMk1_C")
    window.deployed_action.setChecked(True)
    window.toggle_deployed_rendering()
    assert window.scene.nodes[node_id].deployed_plan() is None


def test_the_node_keeps_its_text(window: MainWindow) -> None:
    """The thumbnails are added to the subtitle, never in place of it."""
    node_id = place(
        window, EntryKind.EXTRACTOR, "Desc_OreIron_C", extractor_class="Build_MinerMk3_C"
    )
    window.scene.set_clock_speed(node_id, 2.5)
    window.deployed_action.setChecked(True)
    window.toggle_deployed_rendering()

    subtitle = window.scene.nodes[node_id].subtitle()
    assert "gisement normal" in subtitle
    assert "cadence 250 %" in subtitle


def test_deploying_makes_the_node_taller_and_moves_its_ports(window: MainWindow) -> None:
    """The ports must follow, or every line would land in the middle of the box."""
    node_id = place(window, EntryKind.RECIPE, "Recipe_IngotIron_C")
    item = window.scene.nodes[node_id]
    before = [port.centre.y() for port in item.ports()]
    height_before = item.boundingRect().height()

    window.deployed_action.setChecked(True)
    window.toggle_deployed_rendering()

    item = window.scene.nodes[node_id]
    assert item.boundingRect().height() > height_before
    assert [port.centre.y() for port in item.ports()] != before


# -------------------------------------------------------------- the three states


def test_a_node_can_be_deployed_on_its_own(window: MainWindow) -> None:
    first = place(window, EntryKind.RECIPE, "Recipe_IngotIron_C")
    second = place(window, EntryKind.RECIPE, "Recipe_IronPlate_C")

    window.scene.set_deployed(first, True)
    assert window.scene.nodes[first].deployed_plan() is not None
    assert window.scene.nodes[second].deployed_plan() is None


def test_a_node_can_opt_out_when_everything_else_is_deployed(window: MainWindow) -> None:
    node_id = place(window, EntryKind.RECIPE, "Recipe_IngotIron_C")
    window.deployed_action.setChecked(True)
    window.toggle_deployed_rendering()
    assert window.scene.nodes[node_id].deployed_plan() is not None

    window.scene.set_deployed(node_id, False)
    assert window.scene.nodes[node_id].deployed_plan() is None


def test_a_node_can_go_back_to_following_the_preference(window: MainWindow) -> None:
    """A plain checkbox would have no third state, and no way back."""
    node_id = place(window, EntryKind.RECIPE, "Recipe_IngotIron_C")
    window.scene.set_deployed(node_id, False)
    window.deployed_action.setChecked(True)
    window.toggle_deployed_rendering()
    assert window.scene.nodes[node_id].deployed_plan() is None

    window.scene.set_deployed(node_id, None)
    assert window.document.graph.node(node_id).show_deployed is None
    assert window.scene.nodes[node_id].deployed_plan() is not None


def test_the_override_is_undoable_like_everything_else(window: MainWindow) -> None:
    node_id = place(window, EntryKind.RECIPE, "Recipe_IngotIron_C")
    window.scene.set_deployed(node_id, True)
    window.document.undo_stack.undo()
    assert window.document.graph.node(node_id).show_deployed is None


def test_the_context_menu_offers_the_three_states(window: MainWindow) -> None:
    from PySide6.QtWidgets import QMenu

    node_id = place(window, EntryKind.RECIPE, "Recipe_IngotIron_C")
    item = window.scene.nodes[node_id]
    menu = window.scene.build_context_menu(item.sceneBoundingRect().center(), window)
    assert menu is not None
    children = [action.menu() for action in menu.actions()]
    deployed = next(
        child
        for child in children
        if isinstance(child, QMenu) and child.title().startswith("Rendu déployé")
    )
    labels = [action.text() for action in deployed.actions()]
    assert labels == ["Suivre la préférence", "Afficher les machines", "Masquer les machines"]
    assert [a.text() for a in deployed.actions() if a.isChecked()] == ["Suivre la préférence"]


# ------------------------------------------------------- and it changes no number


def test_deploying_changes_absolutely_nothing_in_the_report(window: MainWindow) -> None:
    """The whole promise of the feature, asserted on the report itself."""
    deposit = place(
        window, EntryKind.EXTRACTOR, "Desc_OreIron_C", extractor_class="Build_MinerMk3_C"
    )
    smelter = place(window, EntryKind.RECIPE, "Recipe_IngotIron_C")
    assert window.scene.connect_nodes(deposit, smelter, "Desc_OreIron_C") is None
    node = window.document.graph.node(smelter)
    assert isinstance(node, MachineNode)
    node.machine_count = 4.0

    plain = window.document.solve_now()
    window.deployed_action.setChecked(True)
    window.toggle_deployed_rendering()
    window.scene.set_deployed(deposit, True)
    deployed = window.document.solve_now()

    assert deployed.model_dump() == plain.model_dump()
    assert deployed.shopping_list == plain.shopping_list
    assert isinstance(window.document.graph.node(deposit), ResourceNode)
