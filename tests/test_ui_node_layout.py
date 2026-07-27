"""What a node makes room for, and what it gives up when it runs out.

Two defects found while looking at Lot 2's screenshots, and the rules that replace
them. Both are about the same thing: a node that shortens the wrong end of a line
tells the reader less than one that is a little wider.

The assertions are on measured text rather than on pixel counts wherever they can
be, because the point is not "the box is 340 wide" but "the name is written in full".
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtGui import QFontMetricsF
from pytestqt.qtbot import QtBot

from satisplanner.core.graph import (
    ExternalSourceNode,
    FactoryGraph,
    MachineNode,
    OutputNode,
    StorageNode,
)
from satisplanner.core.models import GameData
from satisplanner.ui.canvas_items import (
    MAX_NODE_WIDTH,
    NODE_WIDTH,
    ROW_GAP,
    ROW_MARGIN,
    Field,
    NodeItem,
)
from satisplanner.ui.main_window import MainWindow
from tests.conftest import temporary_settings


@pytest.fixture
def window(qtbot: QtBot, game_data: GameData, tmp_path: Path) -> Iterator[MainWindow]:
    del qtbot
    built = MainWindow(game_data, settings=temporary_settings(tmp_path))
    yield built
    built.document.undo_stack.setClean()
    built.scene.dispose()
    built.close()
    built.deleteLater()


def smelter(window: MainWindow, supplied: float) -> NodeItem:
    """Source -> three smelters -> exit, fed at ``supplied`` ore a minute.

    The exit matters: a machine whose product has no route out is blocked entirely,
    every rate falls to zero, and the node would be wide for the wrong reason.
    """
    graph = FactoryGraph(
        nodes=[
            ExternalSourceNode(id="entree1", item_class="Desc_OreIron_C", rate_per_minute=supplied),
            MachineNode(id="machine1", recipe_class="Recipe_IngotIron_C", machine_count=3),
            OutputNode(id="sortie1", item_class="Desc_IronIngot_C"),
        ]
    )
    belt = "Build_ConveyorBeltMk3_C"
    graph.connect("entree1", "machine1", "Desc_OreIron_C", belt, window.game_data)
    graph.connect("machine1", "sortie1", "Desc_IronIngot_C", belt, window.game_data)
    window.document.reset(graph)
    window.document.solve_now()
    return window.scene.nodes["machine1"]


def name_fits(item: NodeItem, item_class: str, *, is_output: bool, shared: bool) -> bool:
    """True when the row has room for the whole name beside the whole rate.

    The same arithmetic ``_paint_rows`` uses to decide how much room a cell gets, so
    a test that passes here really does mean nothing is elided on screen.
    """
    metrics = QFontMetricsF(item._row_font())
    cell = item.width() / 2 - ROW_MARGIN - ROW_GAP if shared else item.width() - 2 * ROW_MARGIN
    name = item.game_data.item(item_class).display_name_fr
    rate = item.port_rate(item_class, is_output=is_output)
    return metrics.horizontalAdvance(name) + ROW_GAP + metrics.horizontalAdvance(rate) <= cell


# --------------------------------------------------------------------------- #
# The node grows rather than eliding the name
# --------------------------------------------------------------------------- #


def test_a_node_whose_rows_fit_keeps_the_standard_width(window: MainWindow) -> None:
    """Nothing grows for the sake of it: a plain node is exactly as wide as before."""
    assert smelter(window, supplied=90.0).width() == NODE_WIDTH


def test_a_shared_row_with_a_pair_keeps_the_item_name_readable(window: MainWindow) -> None:
    """The defect: "Minerai de ..." on a starved smelter says nothing about the item.

    A shared row gives each side half the box, and writing the nominal rate beside
    the real one took the rest. The name is what a reader needs to know which port
    they are looking at, so the box grows instead.
    """
    item = smelter(window, supplied=60.0)

    assert item.port_rate("Desc_OreIron_C", is_output=False) == "60 / 90/min", (
        "le noeud doit bien être sous-alimente, sinon le test ne teste rien"
    )
    assert item.width() > NODE_WIDTH, "le noeud doit s'elargir pour contenir la ligne"
    assert name_fits(item, "Desc_OreIron_C", is_output=False, shared=True)
    assert name_fits(item, "Desc_IronIngot_C", is_output=True, shared=True)


def test_a_node_never_grows_past_the_ceiling(window: MainWindow) -> None:
    """Past a point a wider box is worse than an elided name, and eliding resumes."""
    graph = FactoryGraph(
        nodes=[MachineNode(id="machine1", recipe_class="Recipe_Plastic_C", machine_count=3)]
    )
    window.document.reset(graph)
    window.document.solve_now()

    for item in window.scene.nodes.values():
        assert NODE_WIDTH <= item.width() <= MAX_NODE_WIDTH


def test_the_ports_follow_the_width(window: MainWindow) -> None:
    """A wider box must not leave its output ports floating inside it."""
    item = smelter(window, supplied=60.0)

    outputs = [port for port in item.ports() if port.is_output]
    assert outputs, "la fonderie a bien une sortie"
    for port in outputs:
        assert port.centre.x() == item.width()
    assert item.boundingRect().width() >= item.width()


def test_a_report_that_widens_a_node_relays_it_out(window: MainWindow) -> None:
    """The width depends on the rates, so it has to follow a new report."""
    item = smelter(window, supplied=90.0)
    assert item.width() == NODE_WIDTH, "alimente a 100 %, une seule valeur par port"

    # Starve it: the ports now carry a pair, and the node has to make room.
    from satisplanner.ui import edits

    assert edits.set_quantity(window.document, "entree1", 60.0) is None
    window.document.solve_now()

    assert item.port_rate("Desc_OreIron_C", is_output=False) == "60 / 90/min"
    assert item.width() > NODE_WIDTH


# --------------------------------------------------------------------------- #
# The buffer subtitle
# --------------------------------------------------------------------------- #


def buffer_with(window: MainWindow, item_class: str, transport: str) -> NodeItem:
    graph = FactoryGraph(
        nodes=[
            ExternalSourceNode(id="entree1", item_class=item_class, rate_per_minute=60),
            StorageNode(id="tampon1", storage_class="Build_IndustrialTank_C"),
        ]
    )
    graph.connect("entree1", "tampon1", item_class, transport, window.game_data)
    window.document.reset(graph)
    window.document.solve_now()
    return window.scene.nodes["tampon1"]


def test_a_buffer_subtitle_is_written_in_full(window: MainWindow) -> None:
    """The defect: "…), stc" with the stock stranded on the line below.

    Heavy oil residue is the longest item name that can end up in a tank, and it is
    the one that used to overflow.
    """
    item = buffer_with(window, "Desc_HeavyOilResidue_C", "Build_PipelineMK2_C")

    subtitle = item.subtitle()
    assert "Résidus de pétrole lourd" in subtitle, subtitle
    assert subtitle.endswith("stock 0"), subtitle
    assert "stc" not in subtitle


def test_a_buffer_subtitle_is_cut_into_runs_that_can_wrap(window: MainWindow) -> None:
    """One long run cannot be broken, which is what caused the clipping."""
    item = buffer_with(window, "Desc_HeavyOilResidue_C", "Build_PipelineMK2_C")

    segments = item.subtitle_segments()
    assert len(segments) > 1, "un sous-titre de tampon doit être decoupe comme les autres"
    longest = max(len(segment.text) for segment in segments)
    assert longest < 30, f"un segment de {longest} caractères ne pourra pas passer a la ligne"


def test_every_line_of_a_buffer_subtitle_fits_across_the_node(window: MainWindow) -> None:
    """Nothing is clipped: each wrapped line is measured against the box it sits in."""
    item = buffer_with(window, "Desc_HeavyOilResidue_C", "Build_PipelineMK2_C")

    layout = item.subtitle_layout()
    for line in layout.advances:
        assert sum(line) <= layout.width, "une ligne du sous-titre depasse la boite"


def test_the_stock_of_a_buffer_stays_double_clickable(window: MainWindow) -> None:
    """Shortening the wording must not take the editable value off the node."""
    item = buffer_with(window, "Desc_HeavyOilResidue_C", "Build_PipelineMK2_C")

    box = item.field_rect(Field.QUANTITY)
    assert box.width() > 0
    assert item.field_at(box.center()) is Field.QUANTITY
