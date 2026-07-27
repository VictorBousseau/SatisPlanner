"""What the application must not do, and how long it may take doing what it must.

Two kinds of test live here, and they are not the same kind of promise.

The first kind is **structural** and does not measure anything: a node that changes
place must not make the engine run, a report whose figures changed must not make the
table throw its rows away, and a factory whose lines are all big enough must not be
solved twice to be told so. These hold on any machine, and they are the tests that
actually stop the work of this lot from being undone.

The second kind is a **ceiling in milliseconds**, and it is honest about being a
weaker promise. The figures come from the measurements taken once the corrections
were in, on one Windows machine, with the window rendered offscreen -- see
``tools/benchmark.py`` -- and they carry a factor of three so that a slower machine,
a busy one, or a coverage run does not turn a green suite red. They are there to
catch a regression of the kind this lot was about, where an edit went from tenths of
a second to whole seconds; they are not there to certify a frame rate.
"""

import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import QPointF
from pytestqt.qtbot import QtBot

from satisplanner.core import engine
from satisplanner.core.graph import FactoryGraph, MachineNode, OutputNode, ResourceNode
from satisplanner.core.models import GameData
from satisplanner.ui import edits
from satisplanner.ui.document import SOLVE_DELAY_MS
from satisplanner.ui.main_window import MainWindow
from tests.benchmark_graphs import BENCHMARK_VERSION, benchmark_graph
from tests.conftest import load_graph, temporary_settings

# Measured on 2026-07-26 with ``tools/benchmark.py`` on the reference machine, then
# tripled. The margin is deliberately generous: these are a guard against an order
# of magnitude, not a specification. Bump them only together with
# ``BENCHMARK_VERSION``, because a threshold quoted against one generated factory
# says nothing about another.
MARGIN = 3.0
MEASURED_SOLVE_MS = {50: 21.0, 200: 80.0, 500: 205.0}
MEASURED_EDIT_MS = {50: 79.0, 200: 175.0, 500: 360.0}
MEASURED_MOVE_MS = {50: 3.7, 200: 3.6, 500: 3.8}


def _is_traced() -> bool:
    """True when something is instrumenting this interpreter.

    Coverage, a profiler and a debugger all slow Python down several times over --
    a coverage run alone takes an edit past its ceiling. Timing anything under one
    of them measures the instrument.

    Coverage is asked whether it is *running*, not merely imported: ``pytest-cov``
    imports it on every run, so testing ``sys.modules`` would quietly disable every
    ceiling below and leave a green suite promising nothing.
    """
    if sys.gettrace() is not None:
        return True
    module = sys.modules.get("coverage")
    measuring = getattr(module, "Coverage", None)
    return measuring is not None and measuring.current() is not None


# The structural tests above keep running under instrumentation: they are the ones
# that matter, and they do not care how fast anything is.
untraced_only = pytest.mark.skipif(
    _is_traced(), reason="chronometrage sous instrumentation : on mesurerait l'instrument"
)


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


def _loaded(window: MainWindow, size: int) -> MainWindow:
    window.document.reset(benchmark_graph(size))
    return window


def _a_machine(graph: FactoryGraph) -> str:
    return next(node.id for node in graph.sorted_nodes() if isinstance(node, MachineNode))


def _median(action: object, repeats: int = 5) -> float:
    """Milliseconds, median of a few runs. ``action`` is called for its effect."""
    assert callable(action)
    samples = []
    for _ in range(repeats):
        start = time.perf_counter()
        action()
        samples.append((time.perf_counter() - start) * 1000.0)
    return sorted(samples)[len(samples) // 2]


# --------------------------------------------------------------------------- #
# A position is not a figure
# --------------------------------------------------------------------------- #


def test_moving_a_node_never_runs_the_engine(window: MainWindow, qtbot: QtBot) -> None:
    """The correction this lot exists for, stated as a rule rather than a duration.

    Dragging a box changes where it is drawn. Nothing else. The wait is longer than
    the quiet period before a recomputation, so a resolution merely deferred would
    still be caught.
    """
    _loaded(window, 50)
    reports: list[object] = []
    window.document.reportChanged.connect(reports.append)

    item = window.scene.nodes[sorted(window.scene.nodes)[0]]
    item.setSelected(True)
    window.scene.begin_move()
    item.setPos(QPointF(1234.0, 567.0))
    window.scene.end_move()
    qtbot.wait(SOLVE_DELAY_MS * 3)

    assert reports == [], "un déplacement ne doit declencher aucune résolution"


def test_moving_a_node_never_resets_the_table(window: MainWindow, qtbot: QtBot) -> None:
    """The table shows no position, so a move has nothing to tell it."""
    _loaded(window, 50)
    resets: list[object] = []
    window.table_panel.model.modelReset.connect(lambda: resets.append(None))

    item = window.scene.nodes[sorted(window.scene.nodes)[0]]
    item.setSelected(True)
    window.scene.begin_move()
    item.setPos(QPointF(800.0, 800.0))
    window.scene.end_move()
    qtbot.wait(SOLVE_DELAY_MS * 3)

    assert resets == [], "un déplacement ne doit pas reinitialiser le tableau"


def test_a_move_is_still_undoable_and_still_moves_the_item(window: MainWindow) -> None:
    """Undoing a move is a decision already taken; making it cheap must not lose it."""
    _loaded(window, 50)
    node_id = sorted(window.scene.nodes)[0]
    item = window.scene.nodes[node_id]
    origin = window.document.graph.node(node_id).position

    item.setSelected(True)
    window.scene.begin_move()
    item.setPos(QPointF(900.0, 900.0))
    window.scene.end_move()

    assert window.document.graph.node(node_id).position != origin
    window.document.undo_stack.undo()
    assert window.document.graph.node(node_id).position == origin
    # And the picture followed the graph back, which is the whole of what the
    # move signal is asked to do.
    assert item.pos() == QPointF(*origin)


def test_an_edit_still_recomputes_and_still_redraws(window: MainWindow) -> None:
    """The counterpart: an edit that changes the numbers must do all of it.

    Written next to the two above on purpose. A move costing nothing is only
    correct as long as an edit still costs what an edit has to cost.
    """
    _loaded(window, 50)
    node_id = _a_machine(window.document.graph)
    assert edits.set_quantity(window.document, node_id, 7.0) is None

    report = window.document.solve_now()
    assert report.node(node_id).machine_count == 7.0
    assert window.scene.nodes[node_id].solution is not None
    assert "7" in window.scene.nodes[node_id].subtitle()


# --------------------------------------------------------------------------- #
# The table says what changed, not "forget everything"
# --------------------------------------------------------------------------- #


def test_new_figures_on_the_same_nodes_do_not_reset_the_table(window: MainWindow) -> None:
    """Same rows, different values: ``dataChanged`` is the whole of the news."""
    _loaded(window, 50)
    resets: list[object] = []
    changes: list[object] = []
    window.table_panel.model.modelReset.connect(lambda: resets.append(None))
    window.table_panel.model.dataChanged.connect(lambda *_a: changes.append(None))

    edits.set_quantity(window.document, _a_machine(window.document.graph), 3.0)
    window.document.solve_now()

    assert resets == [], "aucune ligne n'est apparue ni n'a disparu"
    assert changes, "les valeurs ont change : le tableau doit le dire"


def test_removing_a_node_does_reset_the_table(window: MainWindow) -> None:
    """A reset stays right when the rows stop meaning what they meant."""
    _loaded(window, 50)
    resets: list[object] = []
    window.table_panel.model.modelReset.connect(lambda: resets.append(None))

    doomed = _a_machine(window.document.graph)
    window.scene.select_nodes([doomed])
    window.scene.delete_selection()

    assert resets, "un noeud en moins change la structure du tableau"


def test_the_table_still_reads_the_right_row_after_a_deletion(window: MainWindow) -> None:
    """The row index kept beside the row list must not outlive it."""
    _loaded(window, 50)
    model = window.table_panel.model
    doomed = sorted(node.id for node in window.document.graph.nodes)[0]
    assert model.row_of(doomed) == 0

    window.scene.select_nodes([doomed])
    window.scene.delete_selection()

    assert model.row_of(doomed) is None
    survivor = sorted(node.id for node in window.document.graph.nodes)[0]
    assert model.node_id_at(model.row_of(survivor) or 0) == survivor


# --------------------------------------------------------------------------- #
# The indexes, and the ways they could go stale
# --------------------------------------------------------------------------- #


def test_the_graph_index_follows_every_way_a_node_can_appear_or_go(
    window: MainWindow,
) -> None:
    """Add, paste, delete, undo, redo: the map must never answer from memory."""
    _loaded(window, 50)
    graph = window.document.graph
    victim = _a_machine(graph)

    window.scene.select_nodes([victim])
    window.scene.copy_selection()
    window.scene.paste()
    (pasted,) = [node.id for node in graph.nodes if node.id not in {*(), victim}][-1:]
    assert graph.node(pasted) is not None

    window.scene.select_nodes([victim])
    window.scene.delete_selection()
    with pytest.raises(Exception, match="noeud inconnu"):
        graph.node(victim)

    window.document.undo_stack.undo()
    assert graph.node(victim).id == victim
    window.document.undo_stack.redo()
    with pytest.raises(Exception, match="noeud inconnu"):
        graph.node(victim)


def test_a_report_copy_never_answers_with_the_solutions_it_replaced(
    game_data: GameData,
) -> None:
    """The trap under caching a lookup on a model that can be copied.

    ``model_copy`` carries the instance dictionary across, cached indexes included,
    so a report that swaps its solutions out would keep pointing at the old ones.
    Read the index first, *then* copy: that is the order that used to be wrong.
    """
    report = engine.solve(load_graph("iron_plate"), game_data)
    node_id = report.nodes[0].node_id
    before = report.node(node_id)  # builds the index

    renamed = report.nodes[0].model_copy(update={"label": "un autre nom"})
    copied = report.with_flows((renamed, *report.nodes[1:]), report.edges)

    assert copied.node(node_id).label == "un autre nom"
    assert before.label != "un autre nom", "l'original ne doit pas avoir bouge"


def test_two_identical_graphs_stay_equal_after_being_read_from(
    game_data: GameData,
) -> None:
    """An index is not part of what a graph *is*.

    Kept as a pydantic private attribute it would have been, because those take
    part in equality -- and a factory would then have stopped comparing equal to
    itself for the sole reason that somebody had looked a node up in it.
    """
    left, right = load_graph("iron_plate"), load_graph("iron_plate")
    assert left == right
    left.node(left.nodes[0].id)
    left.incoming(left.nodes[-1].id)
    assert left == right
    del game_data


# --------------------------------------------------------------------------- #
# The engine's multiplier
# --------------------------------------------------------------------------- #


def _fixed_points(graph: FactoryGraph, game_data: GameData, monkeypatch: pytest.MonkeyPatch) -> int:
    """How many times the solver iterated to a fixed point for one answer."""
    count = 0
    original = engine._Solver.iterate

    def counting(solver: engine._Solver) -> None:
        nonlocal count
        count += 1
        original(solver)

    monkeypatch.setattr(engine._Solver, "iterate", counting)
    engine.solve(graph, game_data)
    monkeypatch.undo()
    return count


def test_a_factory_whose_lines_all_cope_is_solved_once(
    game_data: GameData, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No line ever got in the way, so the uncapped companion has nothing to add."""
    assert _fixed_points(load_graph("iron_plate"), game_data, monkeypatch) == 1
    assert _fixed_points(load_graph("plastic_chain"), game_data, monkeypatch) == 1
    assert _fixed_points(load_graph("computer_chain"), game_data, monkeypatch) == 1


def test_a_saturated_line_still_gets_its_companion(
    game_data: GameData, monkeypatch: pytest.MonkeyPatch
) -> None:
    """And when a line does get in the way, the second run happens as before."""
    assert _fixed_points(load_graph("belt_saturation"), game_data, monkeypatch) == 2
    report = engine.solve(load_graph("belt_saturation"), game_data)
    saturated = [edge for edge in report.edges if edge.is_saturated]
    assert saturated, "cette fixture existe pour sa ligne saturée"
    assert saturated[0].desired_rate_per_minute is not None


def test_a_draining_buffer_is_what_doubles_the_work(
    game_data: GameData, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The established regime is computed only when the stock is being spent."""
    assert _fixed_points(load_graph("buffer_filling"), game_data, monkeypatch) == 1
    assert _fixed_points(load_graph("buffer_draining"), game_data, monkeypatch) == 2


def test_an_oversized_deposit_does_not_pay_for_a_second_run(
    game_data: GameData, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The commonest layout there is, and it used to cost double.

    A miner that puts far more on the belt than the smelter downstream can take
    has its offer clipped by the belt every round -- and the smelter still gets
    exactly what it would get down an infinite belt, because its own appetite is
    what runs out first.
    """
    graph = load_graph("iron_plate")
    deposit = next(node for node in graph.nodes if isinstance(node, ResourceNode))
    deposit.count = 12.0  # far more ore than anything downstream will ever take

    assert _fixed_points(graph, game_data, monkeypatch) == 1
    report = engine.solve(graph, game_data)
    assert not [edge for edge in report.edges if edge.is_saturated]


# --------------------------------------------------------------------------- #
# Ceilings, in milliseconds
# --------------------------------------------------------------------------- #


@untraced_only
@pytest.mark.parametrize("size", [50, 200, 500])
def test_a_resolution_stays_within_its_ceiling(game_data: GameData, size: int) -> None:
    graph = benchmark_graph(size)
    elapsed = _median(lambda: engine.solve(graph, game_data), repeats=3)
    ceiling = MEASURED_SOLVE_MS[size] * MARGIN
    assert elapsed < ceiling, (
        f"résolution de {size} noeuds en {elapsed:.0f} ms, plafond {ceiling:.0f} ms "
        f"(banc version {BENCHMARK_VERSION})"
    )


@untraced_only
@pytest.mark.parametrize("size", [50, 200, 500])
def test_an_edit_stays_within_its_ceiling(window: MainWindow, size: int) -> None:
    """From the edit to the display being right, through the door a user uses.

    The 120 ms quiet period before the engine runs is not part of this: it is a
    decision, not a cost, and ``solve_now`` is how the same work is done without
    waiting for it.
    """
    _loaded(window, size)
    node_id = _a_machine(window.document.graph)
    counts = iter(range(1, 1000))

    def one_edit() -> None:
        edits.set_quantity(window.document, node_id, float(next(counts)))
        window.document.solve_now()

    elapsed = _median(one_edit, repeats=3)
    ceiling = MEASURED_EDIT_MS[size] * MARGIN
    assert elapsed < ceiling, (
        f"édition sur {size} noeuds en {elapsed:.0f} ms, plafond {ceiling:.0f} ms "
        f"(banc version {BENCHMARK_VERSION})"
    )


@untraced_only
@pytest.mark.parametrize("size", [50, 200, 500])
def test_a_move_stays_within_its_ceiling(window: MainWindow, size: int) -> None:
    """The figure this lot was really about: a move must not grow with the factory."""
    _loaded(window, size)
    node_id = sorted(window.scene.nodes)[0]
    item = window.scene.nodes[node_id]
    item.setSelected(True)
    offsets = iter(range(1, 1000))

    def one_move() -> None:
        window.scene.begin_move()
        step = float(next(offsets) * 20)
        item.setPos(QPointF(step, step))
        window.scene.refresh_edges_of(node_id)
        window.scene.end_move()

    elapsed = _median(one_move)
    ceiling = MEASURED_MOVE_MS[size] * MARGIN
    assert elapsed < ceiling, (
        f"déplacement sur {size} noeuds en {elapsed:.1f} ms, plafond {ceiling:.1f} ms "
        f"(banc version {BENCHMARK_VERSION})"
    )


@untraced_only
def test_a_move_does_not_grow_with_the_size_of_the_factory(window: MainWindow) -> None:
    """The machine-independent form of the same promise, and the stronger one.

    A ceiling in milliseconds says "not too slow here"; this says "not proportional
    to the factory", which is the property that was actually missing and the one a
    different machine cannot flatter.
    """

    def cost(size: int) -> float:
        _loaded(window, size)
        node_id = sorted(window.scene.nodes)[0]
        item = window.scene.nodes[node_id]
        item.setSelected(True)
        offsets = iter(range(1, 1000))

        def one_move() -> None:
            window.scene.begin_move()
            step = float(next(offsets) * 20)
            item.setPos(QPointF(step, step))
            window.scene.end_move()

        return _median(one_move, repeats=7)

    small, large = cost(50), cost(500)
    # Ten times the nodes. Three times the cost is already far more slack than a
    # constant-time operation needs, and a hundredth of what proportional would be.
    assert large < small * 3.0 + 1.0, (
        f"un déplacement coute {small:.1f} ms a 50 noeuds et {large:.1f} ms a 500 : "
        f"il suit la taille de l'usine, ce qu'il ne doit pas faire"
    )


def test_the_generated_factories_are_what_the_thresholds_were_measured_on(
    game_data: GameData,
) -> None:
    """The graphs are the other half of every figure above.

    A threshold is meaningless without the factory it was taken on, so the shape of
    that factory is pinned here: the seven kinds of node, a recycling loop, a buffer
    being drained, and exactly one line too small.
    """
    graph = benchmark_graph(50)
    assert len(graph.nodes) == 50
    kinds = {node.kind.value for node in graph.nodes}
    assert kinds == {
        "resource",
        "water_extractor",
        "external_source",
        "machine",
        "generator",
        "storage",
        "output",
    }
    assert any(isinstance(node, OutputNode) and node.is_sink for node in graph.nodes)

    report = engine.solve(graph, game_data)
    assert report.converged
    assert report.sustained is not None, "il faut un tampon qui se vide"
    assert len([edge for edge in report.edges if edge.is_saturated]) == 1
    assert benchmark_graph(50) == graph, "le générateur doit être deterministe"
