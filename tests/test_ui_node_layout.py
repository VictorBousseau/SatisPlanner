"""What a node makes room for, and what it gives up when it runs out.

Two defects found while looking at Lot 2's screenshots, and the rules that replace
them. Both are about the same thing: a node that shortens the wrong end of a line
tells the reader less than one that is a little wider.

**Nothing here is asserted in pixels.** Three of these tests used to be, and they
went red without a line of the code changing: PySide6 stopped shipping fonts, the
offscreen fallback is far wider than the one they were written against, and a node
that fitted in 260 now reaches its ceiling. The rule had not changed -- the
measuring stick had. So each test now measures with the stick the painter itself
uses, ``QFontMetricsF(item._row_font())``, and states the property:

* a node is exactly as wide as its rows require, within its two bounds;
* under the ceiling, the item's name is written in full;
* at the ceiling, eliding resumes.

Those hold on any machine and on any font, which is the same lesson as the
millisecond ceilings of Lot 1: a rule that does not depend on the environment
guards better than a number measured once.

Pinning a font of our own in the tests would also have worked, and was rejected:
it means shipping a font file, which is one more dependency for the whole project
in exchange for a constant in one test module.
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
    built.dispose()
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


def cell_width(item: NodeItem, item_class: str, *, is_output: bool) -> float:
    """Room one row asks for: its name, the gutter, and its rate."""
    metrics = QFontMetricsF(item._row_font())
    name = item.game_data.item(item_class).display_name_fr
    rate = item.port_rate(item_class, is_output=is_output)
    return metrics.horizontalAdvance(name) + ROW_GAP + metrics.horizontalAdvance(rate)


def required_width(item: NodeItem, left: str, right: str) -> float:
    """How wide a node with **one shared row** has to be, on the font in use.

    Written out for the shape these tests use -- one input facing one output --
    rather than as a copy of the general routine: what is being checked is that
    the drawn box matches the room its two cells ask for, and spelling that out
    for two cells says it more plainly than reproducing the loop.
    """
    half = max(cell_width(item, left, is_output=False), cell_width(item, right, is_output=True))
    needed = 2 * (half + ROW_MARGIN + ROW_GAP)
    return min(max(needed, NODE_WIDTH), MAX_NODE_WIDTH)


def smelter_width(item: NodeItem) -> float:
    return required_width(item, "Desc_OreIron_C", "Desc_IronIngot_C")


def name_fits(item: NodeItem, item_class: str, *, is_output: bool, shared: bool) -> bool:
    """True when the row has room for the whole name beside the whole rate.

    The same arithmetic ``_paint_rows`` uses to decide how much room a cell gets, so
    a test that passes here really does mean nothing is elided on screen.
    """
    cell = item.width() / 2 - ROW_MARGIN - ROW_GAP if shared else item.width() - 2 * ROW_MARGIN
    return cell_width(item, item_class, is_output=is_output) <= cell


# --------------------------------------------------------------------------- #
# The node grows rather than eliding the name
# --------------------------------------------------------------------------- #


def test_a_node_is_exactly_as_wide_as_its_rows_ask_for(window: MainWindow) -> None:
    """Wide enough to be read, and not one pixel wider.

    The rule replaces the constant this used to assert. On a narrow font the two
    cells fit and the answer is the standard width -- nothing grows for the sake
    of it; on a wide one the box grows and, past a point, stops. It is one rule
    either way, and it is the rule the painter obeys.
    """
    item = smelter(window, supplied=90.0)

    assert item.width() == smelter_width(item)
    assert NODE_WIDTH <= item.width() <= MAX_NODE_WIDTH


def test_a_shared_row_with_a_pair_keeps_the_item_name_readable(window: MainWindow) -> None:
    """The defect: "Minerai de ..." on a starved smelter says nothing about the item.

    A shared row gives each side half the box, and writing the nominal rate beside
    the real one took the rest. The name is what a reader needs to know which port
    they are looking at, so the box grows instead -- until the ceiling, past which
    a wider box is worse than a shortened name and eliding is the right answer.

    Stated as one assertion covering both, so it is never vacuous: a name that
    does not fit inside a node that had room left to grow is the defect, and
    nothing else is.
    """
    item = smelter(window, supplied=60.0)

    assert item.port_rate("Desc_OreIron_C", is_output=False) == "60 / 90/min", (
        "le nœud doit bien être sous-alimente, sinon le test ne teste rien"
    )
    assert item.width() == smelter_width(item)
    for item_class, is_output in (("Desc_OreIron_C", False), ("Desc_IronIngot_C", True)):
        assert name_fits(item, item_class, is_output=is_output, shared=True) or (
            item.width() == MAX_NODE_WIDTH
        ), "un nom élidé alors que le nœud pouvait encore s'élargir"


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
    """The width depends on the rates, so it has to follow a new report.

    The assertion is that the box matches what the **new** texts ask for. A node
    that failed to relayout would still be showing what the old ones asked for,
    and the two differ by exactly as much as the rates do -- except where both
    land on the ceiling, and there the two drawings are the same picture, so
    there is nothing left to catch.
    """
    item = smelter(window, supplied=90.0)
    assert item.width() == smelter_width(item), "alimente a 100 %, une seule valeur par port"
    fed = item.port_rate("Desc_OreIron_C", is_output=False)

    # Starve it: the ports now carry a pair, and the node has to make room.
    from satisplanner.ui import edits

    assert edits.set_quantity(window.document, "entree1", 60.0) is None
    window.document.solve_now()

    starved = item.port_rate("Desc_OreIron_C", is_output=False)
    assert starved == "60 / 90/min" != fed, "le rapport doit vraiment avoir change la ligne"
    assert item.width() == smelter_width(item), "la largeur doit suivre le nouveau rapport"


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
