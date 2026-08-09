"""Canvas behaviour: create, connect, refuse, delete -- and undo every one of them.

These tests drive the scene through the same entry points the widgets use, so what
they lock down is the interaction rather than the pixels. Rendering is left alone on
purpose: asserting on a painted rectangle would break on the first restyle without
ever having caught a bug.
"""

from collections.abc import Iterator

import pytest
from PySide6.QtCore import QEvent, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QGraphicsSceneMouseEvent
from pytestqt.qtbot import QtBot

from satisplanner.core.graph import (
    OVERFLOW_BRANCH,
    AttachmentMode,
    MachineNode,
    ResourceNode,
    SplitterNode,
    StorageNode,
)
from satisplanner.core.models import GameData, ItemForm, Purity, SplitterMode
from satisplanner.core.results import LimitingFactor
from satisplanner.ui import edits, theme
from satisplanner.ui.canvas import FactoryScene, snapped
from satisplanner.ui.canvas_items import ANY_ITEM
from satisplanner.ui.catalogue import EntryKind, PaletteEntry, build_entries
from satisplanner.ui.commands import can_connect
from satisplanner.ui.document import FactoryDocument
from satisplanner.ui.icon_provider import IconProvider

BELT_MK1 = "Build_ConveyorBeltMk1_C"
PIPE_MK1 = "Build_Pipeline_C"


@pytest.fixture
def scene(qtbot: QtBot, game_data: GameData) -> Iterator[FactoryScene]:
    """A scene on an empty factory, torn down explicitly.

    ``qtbot`` is only here for the QApplication. The teardown is not optional: a
    graphics item is owned by C++ once it is in a scene, and letting the garbage
    collector reach the Python wrappers after the scene has gone is how PySide
    corrupts the heap. Real windows never hit it because their scene outlives them.
    """
    del qtbot
    document = FactoryDocument(game_data)
    canvas = FactoryScene(document, IconProvider(), build_entries(game_data))
    yield canvas
    canvas.dispose()


def entry_of(scene: FactoryScene, kind: EntryKind, class_name: str) -> PaletteEntry:
    for entry in scene.entries:
        if entry.kind is kind and entry.class_name == class_name:
            return entry
    available = [e.class_name for e in scene.entries if e.kind is kind][:5]
    msg = f"{class_name} absent de la palette ({kind}) ; presents : {available}"
    raise AssertionError(msg)


def place(scene: FactoryScene, kind: EntryKind, class_name: str, x: float, y: float) -> str:

    before = {node.id for node in scene.document.graph.nodes}
    scene.add_entry(entry_of(scene, kind, class_name), QPointF(x, y))
    (created,) = {node.id for node in scene.document.graph.nodes} - before
    return created


def iron_chain(scene: FactoryScene) -> tuple[str, str]:
    """A miner and a smelter whose ingots already have somewhere to go.

    The exit matters: a machine whose product has no route out is stopped dead, by
    design, so a chain without one would test the wrong thing.
    """
    mine = place(scene, EntryKind.EXTRACTOR, "Desc_OreIron_C", 0, 0)
    smelter = place(scene, EntryKind.RECIPE, "Recipe_IngotIron_C", 400, 0)
    out = place(scene, EntryKind.OUTPUT, "Desc_IronIngot_C", 800, 0)
    assert scene.connect_nodes(smelter, out, "Desc_IronIngot_C") is None
    return mine, smelter


# --------------------------------------------------------------------------- #
# Creating
# --------------------------------------------------------------------------- #


def test_dropping_an_entry_creates_a_node_and_an_item(scene: FactoryScene) -> None:
    node_id = place(scene, EntryKind.RECIPE, "Recipe_IngotIron_C", 137, 91)
    node = scene.document.graph.node(node_id)
    assert isinstance(node, MachineNode)
    assert node.recipe_class == "Recipe_IngotIron_C"
    assert node_id in scene.nodes, "le canvas doit refleter le graphe immediatement"
    # Snapped to the grid, so hand-placed nodes still line up.
    assert node.position == (140.0, 100.0)


def test_every_palette_kind_produces_the_right_node(scene: FactoryScene) -> None:
    kinds = {
        EntryKind.RECIPE: ("Recipe_IngotIron_C", MachineNode),
        EntryKind.EXTRACTOR: ("Desc_OreIron_C", ResourceNode),
        EntryKind.STORAGE: ("Build_StorageContainerMk1_C", StorageNode),
    }
    for kind, (class_name, expected) in kinds.items():
        node_id = place(scene, kind, class_name, 0, 0)
        assert isinstance(scene.document.graph.node(node_id), expected)


def test_adding_a_node_is_undoable(scene: FactoryScene) -> None:
    node_id = place(scene, EntryKind.RECIPE, "Recipe_IngotIron_C", 0, 0)
    scene.document.undo_stack.undo()
    assert scene.document.graph.nodes == []
    assert node_id not in scene.nodes

    scene.document.undo_stack.redo()
    assert [node.id for node in scene.document.graph.nodes] == [node_id]
    assert node_id in scene.nodes


# --------------------------------------------------------------------------- #
# Connecting
# --------------------------------------------------------------------------- #


def test_a_valid_connection_is_accepted(scene: FactoryScene) -> None:
    mine, smelter = iron_chain(scene)
    assert can_connect(scene.document, mine, smelter, "Desc_OreIron_C", BELT_MK1) is None
    assert scene.connect_nodes(mine, smelter, "Desc_OreIron_C") is None

    (edge,) = [e for e in scene.document.graph.edges if e.source == mine]
    assert (edge.source, edge.target) == (mine, smelter)
    assert edge.transport_class == BELT_MK1
    assert edge.id in scene.edges


def test_a_solid_on_a_pipe_is_refused_before_the_line_exists(scene: FactoryScene) -> None:
    """The refusal is a check the drag can ask for, not a report after the fact."""
    mine, smelter = iron_chain(scene)
    before = len(scene.document.graph.edges)
    reason = can_connect(scene.document, mine, smelter, "Desc_OreIron_C", PIPE_MK1)
    assert reason is not None
    assert "convoyeur" in reason
    assert len(scene.document.graph.edges) == before, "rien ne doit avoir été créé"


def test_a_fluid_on_a_belt_is_refused(scene: FactoryScene) -> None:
    oil = place(scene, EntryKind.EXTRACTOR, "Desc_LiquidOil_C", 0, 0)
    refinery = place(scene, EntryKind.RECIPE, "Recipe_Plastic_C", 400, 0)
    reason = can_connect(scene.document, oil, refinery, "Desc_LiquidOil_C", BELT_MK1)
    assert reason is not None
    assert "tuyauterie" in reason


def test_a_consumer_that_does_not_use_the_item_is_refused(scene: FactoryScene) -> None:
    mine = place(scene, EntryKind.EXTRACTOR, "Desc_OreIron_C", 0, 0)
    wrong = place(scene, EntryKind.RECIPE, "Recipe_IngotCopper_C", 400, 0)
    reason = can_connect(scene.document, mine, wrong, "Desc_OreIron_C", BELT_MK1)
    assert reason is not None
    assert "ne consomme pas" in reason


def test_the_same_line_cannot_be_drawn_twice(scene: FactoryScene) -> None:
    mine, smelter = iron_chain(scene)
    scene.connect_nodes(mine, smelter, "Desc_OreIron_C")
    reason = can_connect(scene.document, mine, smelter, "Desc_OreIron_C", BELT_MK1)
    assert reason == "cette ligne existe déjà"


def test_connecting_is_undoable(scene: FactoryScene) -> None:
    mine, smelter = iron_chain(scene)
    scene.connect_nodes(mine, smelter, "Desc_OreIron_C")
    edge_id = next(e.id for e in scene.document.graph.edges if e.source == mine)

    scene.document.undo_stack.undo()
    assert edge_id not in {edge.id for edge in scene.document.graph.edges}
    assert edge_id not in scene.edges

    scene.document.undo_stack.redo()
    assert edge_id in {edge.id for edge in scene.document.graph.edges}
    assert edge_id in scene.edges


def test_new_lines_use_the_chosen_default_tier(scene: FactoryScene) -> None:
    scene.set_default_transports("Build_ConveyorBeltMk4_C", "Build_PipelineMK2_C")
    assert scene.default_transport_for(ItemForm.SOLID) == "Build_ConveyorBeltMk4_C"
    assert scene.default_transport_for(ItemForm.LIQUID) == "Build_PipelineMK2_C"

    mine, smelter = iron_chain(scene)
    scene.connect_nodes(mine, smelter, "Desc_OreIron_C")
    fresh = next(e for e in scene.document.graph.edges if e.source == mine)
    assert fresh.transport_class == "Build_ConveyorBeltMk4_C"


# --------------------------------------------------------------------------- #
# Deleting
# --------------------------------------------------------------------------- #


def test_deleting_a_node_takes_its_lines_with_it_and_undo_puts_them_back(
    scene: FactoryScene,
) -> None:
    mine, smelter = iron_chain(scene)
    scene.connect_nodes(mine, smelter, "Desc_OreIron_C")
    assert len(scene.document.graph.edges) == 2
    scene.clearSelection()  # dropping a node selects it; start from a clean slate
    scene.nodes[smelter].setSelected(True)
    scene.delete_selection()

    assert smelter not in {node.id for node in scene.document.graph.nodes}
    assert scene.document.graph.edges == [], "une ligne ne survit pas a son extremite"
    assert scene.edges == {}

    scene.document.undo_stack.undo()
    assert smelter in {node.id for node in scene.document.graph.nodes}
    assert len(scene.document.graph.edges) == 2, "les lignes reviennent avec le nœud"
    assert len(scene.edges) == 2, "et elles sont redessinees"


def test_deleting_only_a_line_leaves_the_nodes(scene: FactoryScene) -> None:
    mine, smelter = iron_chain(scene)
    scene.connect_nodes(mine, smelter, "Desc_OreIron_C")
    edge_id = next(e.id for e in scene.document.graph.edges if e.source == mine)
    scene.clearSelection()
    scene.edges[edge_id].setSelected(True)
    scene.delete_selection()

    assert edge_id not in {edge.id for edge in scene.document.graph.edges}
    assert len(scene.document.graph.nodes) == 3, "les nœuds restent"

    scene.document.undo_stack.undo()
    assert edge_id in {edge.id for edge in scene.document.graph.edges}


def test_deleting_nothing_pushes_nothing(scene: FactoryScene) -> None:
    place(scene, EntryKind.RECIPE, "Recipe_IngotIron_C", 0, 0)
    before = scene.document.undo_stack.count()
    scene.clearSelection()
    scene.delete_selection()
    assert scene.document.undo_stack.count() == before


# --------------------------------------------------------------------------- #
# Moving
# --------------------------------------------------------------------------- #


def test_moving_a_node_is_one_undoable_step(scene: FactoryScene) -> None:

    node_id = place(scene, EntryKind.RECIPE, "Recipe_IngotIron_C", 0, 0)
    item = scene.nodes[node_id]
    item.setSelected(True)
    scene.begin_move()
    item.setPos(QPointF(213, 187))
    scene.end_move()

    assert scene.document.graph.node(node_id).position == (220.0, 180.0)
    scene.document.undo_stack.undo()
    assert scene.document.graph.node(node_id).position == (0.0, 0.0)


def test_snapping_rounds_to_the_grid() -> None:

    assert snapped(QPointF(9, 11)) == QPointF(0, 20)
    assert snapped(QPointF(-9, -31)) == QPointF(0, -40)


# --------------------------------------------------------------------------- #
# Dragging a line
# --------------------------------------------------------------------------- #


def _mouse(kind: QEvent.Type, at: QPointF) -> QGraphicsSceneMouseEvent:
    event = QGraphicsSceneMouseEvent(kind)
    event.setScenePos(at)
    event.setButton(Qt.MouseButton.LeftButton)
    event.setButtons(Qt.MouseButton.LeftButton)
    return event


def _port(scene: FactoryScene, node_id: str, item_class: str, *, output: bool) -> QPointF:
    return scene.nodes[node_id].port_scene_position(item_class, is_output=output)


def test_dragging_from_an_output_to_an_input_draws_the_line(scene: FactoryScene) -> None:
    mine, smelter = iron_chain(scene)
    start = _port(scene, mine, "Desc_OreIron_C", output=True)
    end = _port(scene, smelter, "Desc_OreIron_C", output=False)

    scene.mousePressEvent(_mouse(QEvent.Type.GraphicsSceneMousePress, start))
    scene.mouseMoveEvent(_mouse(QEvent.Type.GraphicsSceneMouseMove, end))
    scene.mouseReleaseEvent(_mouse(QEvent.Type.GraphicsSceneMouseRelease, end))

    assert any(
        edge.source == mine and edge.target == smelter for edge in scene.document.graph.edges
    )


def test_the_band_turns_red_over_an_impossible_target(scene: FactoryScene) -> None:
    """The refusal happens while the mouse is still down, which is the whole point."""
    oil = place(scene, EntryKind.EXTRACTOR, "Desc_LiquidOil_C", 0, 400)
    mine, smelter = iron_chain(scene)
    start = _port(scene, mine, "Desc_OreIron_C", output=True)

    scene.mousePressEvent(_mouse(QEvent.Type.GraphicsSceneMousePress, start))
    hints: list[str] = []
    scene.selectionSummaryChanged.connect(hints.append)

    good = _port(scene, smelter, "Desc_OreIron_C", output=False)
    scene.mouseMoveEvent(_mouse(QEvent.Type.GraphicsSceneMouseMove, good))
    assert scene.band_colour() == QColor(theme.EDGE_VALID)

    bad = _port(scene, oil, "Desc_LiquidOil_C", output=True)
    scene.mouseMoveEvent(_mouse(QEvent.Type.GraphicsSceneMouseMove, bad))
    assert scene.band_colour() == QColor(theme.ACCENT), "un port de sortie n'est pas une cible"

    # Releasing in the void leaves the factory untouched.
    scene.mouseReleaseEvent(_mouse(QEvent.Type.GraphicsSceneMouseRelease, QPointF(-5000, -5000)))
    assert not any(edge.source == mine for edge in scene.document.graph.edges)
    assert hints, "l'utilisateur est renseigne pendant le tirage"


def test_a_buffer_with_no_content_accepts_whatever_arrives(scene: FactoryScene) -> None:
    """Its single input port is a wildcard until a line decides what it holds."""
    mine = place(scene, EntryKind.EXTRACTOR, "Desc_OreIron_C", 0, 0)
    buffer = place(scene, EntryKind.STORAGE, "Build_StorageContainerMk1_C", 400, 0)
    assert scene.nodes[buffer].content_item is None

    start = _port(scene, mine, "Desc_OreIron_C", output=True)
    end = _port(scene, buffer, ANY_ITEM, output=False)
    scene.mousePressEvent(_mouse(QEvent.Type.GraphicsSceneMousePress, start))
    scene.mouseReleaseEvent(_mouse(QEvent.Type.GraphicsSceneMouseRelease, end))

    (edge,) = scene.document.graph.edges
    assert edge.item_class == "Desc_OreIron_C"
    # And the buffer now knows what it stores, so it grows an output port.
    assert scene.nodes[buffer].content_item == "Desc_OreIron_C"


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #


def test_the_whole_canvas_paints_without_raising(scene: FactoryScene) -> None:
    """Not a pixel assertion: a smoke test that every paint path survives real data.

    It covers the states that differ visually -- nominal, throttled, blocked, a
    saturated line, a fluid and a solid -- because those are the branches in ``paint``.
    """
    mine, smelter = iron_chain(scene)
    scene.connect_nodes(mine, smelter, "Desc_OreIron_C")
    oil = place(scene, EntryKind.EXTRACTOR, "Desc_LiquidOil_C", 0, 400)
    refinery = place(scene, EntryKind.RECIPE, "Recipe_Plastic_C", 400, 400)
    scene.connect_nodes(oil, refinery, "Desc_LiquidOil_C")  # residue blocked on purpose
    buffer = place(scene, EntryKind.STORAGE, "Build_StorageContainerMk1_C", 800, 800)
    scene.document.solve_now()

    assert scene.document.report is not None
    assert scene.nodes[refinery].solution is not None
    assert scene.nodes[buffer].content_item is None

    image = QImage(1200, 1000, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    try:
        scene.render(painter, target=QRectF(0, 0, 1200, 1000))
    finally:
        painter.end()
    assert image.pixelColor(0, 0).alpha() >= 0  # reached the end without dying


def test_a_node_names_the_port_under_the_cursor(scene: FactoryScene) -> None:
    _, smelter = iron_chain(scene)
    item = scene.nodes[smelter]
    port = next(p for p in item.ports() if not p.is_output)
    assert item.port_at(port.centre) == port
    assert item.port_at(QPointF(-500, -500)) is None


# --------------------------------------------------------------------------- #
# Live recalculation
# --------------------------------------------------------------------------- #


def test_the_engine_runs_again_after_every_change(scene: FactoryScene) -> None:
    """The non-regression guard: a change must reach the report, not just the picture."""
    mine, smelter = iron_chain(scene)
    scene.connect_nodes(mine, smelter, "Desc_OreIron_C")
    report = scene.document.solve_now()

    # One smelter eats 30 ore/min, so the Mk.1 miner only sends 30 of its 60.
    assert report.node(mine).outputs == {"Desc_OreIron_C": 30.0}
    assert report.node(smelter).ratio == 1.0
    assert report.node(smelter).outputs == {"Desc_IronIngot_C": 30.0}
    # And the node items were handed the figures, which is what colours them.
    drawn = scene.nodes[smelter].solution
    assert drawn is not None
    assert drawn.ratio == 1.0


def test_undoing_a_change_recomputes_too(scene: FactoryScene) -> None:
    mine, smelter = iron_chain(scene)
    scene.connect_nodes(mine, smelter, "Desc_OreIron_C")
    assert scene.document.solve_now().node(smelter).ratio == 1.0

    scene.document.undo_stack.undo()
    report = scene.document.solve_now()
    assert report.node(smelter).ratio == 0.0, "sans minerai, la fonderie s'arrete"
    assert report.node(smelter).limiting is LimitingFactor.INPUTS


def test_adjusting_a_node_sizes_it_to_its_inputs(scene: FactoryScene) -> None:
    """The "ajuster ce noeud" action, on a smelter deliberately built too large."""
    mine, smelter = iron_chain(scene)
    scene.connect_nodes(mine, smelter, "Desc_OreIron_C")
    node = scene.document.graph.node(smelter)
    assert isinstance(node, MachineNode)
    node.machine_count = 5.0

    scene.adjust_node(smelter)
    assert node.machine_count == pytest.approx(2.0)

    scene.document.undo_stack.undo()
    assert node.machine_count == 5.0


def test_a_line_too_small_is_reported_and_upgradable(scene: FactoryScene) -> None:
    """480/min on a Mk.1 belt: the canvas can offer the tier that would do."""
    scene.set_default_transports(BELT_MK1, PIPE_MK1)
    mine = place(scene, EntryKind.EXTRACTOR, "Desc_OreIron_C", 0, 0)
    node = scene.document.graph.node(mine)
    assert isinstance(node, ResourceNode)
    node.extractor_class = "Build_MinerMk3_C"
    node.purity = Purity.PURE
    out = place(scene, EntryKind.OUTPUT, "Desc_OreIron_C", 400, 0)
    scene.connect_nodes(mine, out, "Desc_OreIron_C")

    report = scene.document.solve_now()
    edge_id = scene.document.graph.edges[0].id
    solution = report.edge(edge_id)
    assert solution.rate_per_minute == 60.0
    assert solution.demanded_rate == 480.0
    assert solution.is_saturated

    scene.upgrade_line(edge_id)
    assert scene.document.graph.edge(edge_id).transport_class == "Build_ConveyorBeltMk4_C"
    assert scene.document.solve_now().edge(edge_id).rate_per_minute == 480.0


# --------------------------------------------------------------------------- #
# Splitters and mergers on the canvas
# --------------------------------------------------------------------------- #


def test_a_splitter_is_placed_from_the_palette_and_takes_the_item_of_its_line(
    scene: FactoryScene,
) -> None:
    """The way out of the port budget, by the same door as everything else.

    A splitter placed by hand names no item and no building: it learns both from the
    first line drawn to it, which is the only reason making the budget stricter does
    not make the application harder to use.
    """
    _, smelter = iron_chain(scene)
    # The port budget is the faithful mode's rule, so the document is put in it by
    # the door the menu uses -- there is no other way to reach it.
    assert edits.set_attachment_mode(scene.document, AttachmentMode.FAITHFUL).happened
    fork = place(scene, EntryKind.SPLITTER, "Build_ConveyorAttachmentSplitter_C", 1200, 0)
    node = scene.document.graph.node(fork)
    assert isinstance(node, SplitterNode)
    assert node.item_class is None, "rien à saisir : la ligne le dira"
    assert scene.nodes[fork].subtitle() == "répartiteur — contenu indéterminé"

    # The smelter's one port is taken, so the second line has to go through it.
    second = place(scene, EntryKind.OUTPUT, "Desc_IronIngot_C", 1600, 0)
    refusal = scene.connect_nodes(smelter, second, "Desc_IronIngot_C")
    assert refusal is not None and "répartiteur" in refusal

    # Take the first line back -- the way a user would, by selecting it and pressing
    # Delete -- and route both through the splitter instead.
    scene.clearSelection()
    scene.clearSelection()
    scene.edges[scene.document.graph.outgoing(smelter)[0].id].setSelected(True)
    scene.delete_selection()
    assert scene.document.graph.outgoing(smelter) == []
    assert scene.connect_nodes(smelter, fork, "Desc_IronIngot_C") is None
    assert scene.connect_nodes(fork, second, "Desc_IronIngot_C") is None
    assert scene.nodes[fork].subtitle() == "répartiteur — Lingot de fer — 1 sortie(s) sur 3"


def test_a_merger_is_offered_too_and_names_the_two_buildings(scene: FactoryScene) -> None:
    """One entry per job, not one per building: the form decides which it is."""
    entry = entry_of(scene, EntryKind.MERGER, "Build_ConveyorAttachmentMerger_C")
    assert entry.label == "Groupeur"
    assert "Groupeur de convoyeurs" in entry.detail
    assert "Jonction de pipeline" in entry.detail
    assert entry.matches("jonction"), "un joueur cherche le nom du bâtiment"


def test_a_migration_puts_no_node_on_top_of_another(scene: FactoryScene) -> None:
    """Measured on the boxes as drawn, not on the envelope the migration assumes.

    The one damage of this lot that no figure would catch: a factory somebody had
    tidied coming back as a plate of noodles. ``core.attachments`` keeps a clearance
    without knowing how wide a box really is -- the domain layer never has -- so
    this is where the two are held against each other. It counts the overlaps the
    layout already had and insists the conversion adds none: a benchmark grid that
    was already tight is not this lot's to fix.
    """
    from satisplanner.core.attachments import materialise
    from tests.benchmark_graphs import benchmark_graph

    design = benchmark_graph(50, wired=False)
    scene.document.reset(design.model_copy(deep=True))
    before = _overlaps(scene)

    wired = design.model_copy(deep=True)
    materialise(wired)
    scene.document.reset(wired)
    assert _overlaps(scene) == before, "la conversion a posé un nœud sur un autre"
    assert len(wired.nodes) > len(design.nodes), "elle a bien inséré quelque chose"


def _overlaps(scene: FactoryScene) -> int:
    boxes = [item.sceneBoundingRect() for item in scene.nodes.values()]
    return sum(
        1
        for first in range(len(boxes))
        for second in range(first + 1, len(boxes))
        if boxes[first].intersected(boxes[second]).width() > 1
        and boxes[first].intersected(boxes[second]).height() > 1
    )


def test_the_mode_and_the_filters_are_readable_on_the_node(scene: FactoryScene) -> None:
    """A branch set to surplus moves the figures, so it is on the face of the node.

    Named after the box the branch goes to, because that is what a reader is
    looking at. Branches left on "n'importe lequel" are not listed: three lines
    saying nothing would bury the one that says something.
    """
    _, smelter = iron_chain(scene)
    fork = place(scene, EntryKind.SPLITTER, "Build_ConveyorAttachmentSplitter_C", 1200, 0)
    keep = place(scene, EntryKind.OUTPUT, "Desc_IronIngot_C", 1600, -200)
    spill = place(scene, EntryKind.OUTPUT, "Desc_IronIngot_C", 1600, 200)
    scene.clearSelection()
    scene.edges[scene.document.graph.outgoing(smelter)[0].id].setSelected(True)
    scene.delete_selection()
    assert scene.connect_nodes(smelter, fork, "Desc_IronIngot_C") is None
    assert scene.connect_nodes(fork, keep, "Desc_IronIngot_C") is None
    assert scene.connect_nodes(fork, spill, "Desc_IronIngot_C") is None

    assert "intelligent" not in scene.nodes[fork].subtitle()
    assert scene.set_splitter_mode(fork, SplitterMode.SMART)
    assert scene.set_branch_filter(fork, spill, OVERFLOW_BRANCH)

    subtitle = scene.nodes[fork].subtitle()
    assert subtitle.startswith("répartiteur intelligent — Lingot de fer")
    assert f"{spill} : surplus" in subtitle
    assert keep not in subtitle, "une branche en tout-venant n'encombre pas la ligne"


def test_a_standard_splitter_refuses_a_filter_and_says_why(scene: FactoryScene) -> None:
    """Which of the three buildings gets placed is the user's decision, and it costs."""
    _, smelter = iron_chain(scene)
    fork = place(scene, EntryKind.SPLITTER, "Build_ConveyorAttachmentSplitter_C", 1200, 0)
    out = scene.document.graph.outgoing(smelter)[0].target
    scene.clearSelection()
    scene.edges[scene.document.graph.outgoing(smelter)[0].id].setSelected(True)
    scene.delete_selection()
    assert scene.connect_nodes(smelter, fork, "Desc_IronIngot_C") is None
    assert scene.connect_nodes(fork, out, "Desc_IronIngot_C") is None

    assert not scene.set_branch_filter(fork, out, OVERFLOW_BRANCH)
    node = scene.document.graph.node(fork)
    assert isinstance(node, SplitterNode)
    assert node.filters == {}


def test_going_back_to_standard_clears_the_filters_in_one_undo(scene: FactoryScene) -> None:
    """A standard splitter shares equally whatever a branch claims, so the claim goes.

    One macro, so one Ctrl+Z puts both back: a mode changed by mistake would
    otherwise cost the user everything they had written on the branches.
    """
    _, smelter = iron_chain(scene)
    fork = place(scene, EntryKind.SPLITTER, "Build_ConveyorAttachmentSplitter_C", 1200, 0)
    out = scene.document.graph.outgoing(smelter)[0].target
    scene.clearSelection()
    scene.edges[scene.document.graph.outgoing(smelter)[0].id].setSelected(True)
    scene.delete_selection()
    scene.connect_nodes(smelter, fork, "Desc_IronIngot_C")
    scene.connect_nodes(fork, out, "Desc_IronIngot_C")
    scene.set_splitter_mode(fork, SplitterMode.PROGRAMMABLE)
    scene.set_branch_filter(fork, out, OVERFLOW_BRANCH)

    scene.set_splitter_mode(fork, SplitterMode.STANDARD)
    node = scene.document.graph.node(fork)
    assert isinstance(node, SplitterNode)
    assert (node.mode, node.filters) == (SplitterMode.STANDARD, {})

    scene.document.undo_stack.undo()
    node = scene.document.graph.node(fork)
    assert isinstance(node, SplitterNode)
    assert node.mode is SplitterMode.PROGRAMMABLE
    assert node.filters == {out: OVERFLOW_BRANCH}, "un seul undo rend les deux"
