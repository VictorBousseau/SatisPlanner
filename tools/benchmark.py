"""Measure what the user waits for, at three factory sizes.

    py -3.12 tools/benchmark.py
    py -3.12 tools/benchmark.py --profile 500

Three figures per size, each one a whole gesture rather than a function call:

* **resolution** -- one complete ``engine.solve`` on the graph;
* **edition** -- from the edit that changes a number to the display being right
  again: the command, the redraw, the solve, the panels, the repaint;
* **deplacement** -- from grabbing a node to letting it go, with everything that
  follows.

The deliberate 120 ms quiet period before the engine runs is **not** counted. It
is a design decision, not a cost, and including it would hide whatever the work
underneath actually takes.

The window is rendered offscreen so that two runs are comparable and no window
flashes up. That makes these numbers a fair measure of the CPU work and of the
raster painting, and not a measure of the compositor.

No dependency beyond the standard library: ``time.perf_counter`` for the timing
and ``cProfile`` for the ``--profile`` mode.
"""

import argparse
import cProfile
import io
import os
import pstats
import statistics
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Set before Qt is imported: the platform plugin is chosen at import time.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QApplication

from satisplanner.core import engine
from satisplanner.core.graph import FactoryGraph
from satisplanner.core.models import GameData
from satisplanner.data import db
from satisplanner.ui import edits
from satisplanner.ui.main_window import MainWindow
from tests.benchmark_graphs import (
    BENCHMARK_SIZES,
    BENCHMARK_VERSION,
    benchmark_graph,
)

# Enough repeats for the median to be stable, few enough that the whole run
# finishes while somebody is still watching it.
REPEATS = 5
WINDOW_SIZE = (1600, 950)


@dataclass(frozen=True)
class Timing:
    """One measurement, in milliseconds."""

    median: float
    best: float

    def __str__(self) -> str:
        return f"{self.median:8.1f} ms"


def timed(action: Callable[[], None], repeats: int = REPEATS) -> Timing:
    """Run ``action`` a few times and keep the median, in milliseconds.

    The median rather than the mean: one scheduling hiccup on a laptop should not
    decide the figure a threshold is derived from.
    """
    samples: list[float] = []
    for _ in range(repeats):
        start = time.perf_counter()
        action()
        samples.append((time.perf_counter() - start) * 1000.0)
    return Timing(median=statistics.median(samples), best=min(samples))


def measure_solve(graph: FactoryGraph, game_data: GameData) -> Timing:
    """One complete resolution, engine only, no interface anywhere near it."""

    def one_solve() -> None:
        engine.solve(graph, game_data)

    return timed(one_solve)


def _settle(application: QApplication) -> None:
    """Let Qt finish: deliver the queued events, painting included.

    Deliberately not ``repaint()`` on each widget. ``repaint`` is unconditional --
    it redraws a panel whether or not anything on it changed -- and a benchmark
    that forced it would charge every gesture for three panels it never touched.
    Draining the queue paints exactly the regions Qt marked dirty, which is what
    the user actually waits for. Twice, because delivering one event can post the
    next.
    """
    application.processEvents()
    application.processEvents()


def measure_edit(window: MainWindow, application: QApplication) -> Timing:
    """From an edit that changes the numbers to the display being right again.

    The edit goes through ``ui/edits.py``, which is the only door there is: this
    measures the same path a user's double-click takes, not a shortcut into the
    engine.
    """
    node_id = next(
        node.id for node in window.document.graph.sorted_nodes() if node.id.startswith("machine")
    )
    counts = iter(range(1, 10_000))

    def one_edit() -> None:
        # A different value every time, so the edit is never a no-op the command
        # layer could refuse for being the value already held.
        edits.set_quantity(window.document, node_id, float(next(counts)))
        application.processEvents()
        window.document.solve_now()
        _settle(application)

    return timed(one_edit)


def measure_move(window: MainWindow, application: QApplication) -> Timing:
    """From grabbing a node to letting it go.

    A position changes no rate, so everything this triggers is, by definition,
    work nobody asked for. It is measured through the scene's own ``begin_move`` /
    ``end_move`` because that is what the mouse handlers call.
    """
    scene = window.scene
    node_id = next(iter(sorted(scene.nodes)))
    item = scene.nodes[node_id]
    steps = iter(range(1, 10_000))

    def one_move() -> None:
        scene.clearSelection()
        item.setSelected(True)
        scene.begin_move()
        offset = float(next(steps) * 20)
        item.setPos(QPointF(offset, offset))
        scene.refresh_edges_of(node_id)
        scene.end_move()
        _settle(application)

    return timed(one_move)


def build_window(graph: FactoryGraph, game_data: GameData) -> tuple[QApplication, MainWindow]:
    application = QApplication.instance() or QApplication([])
    assert isinstance(application, QApplication)
    window = MainWindow(game_data)
    window.resize(*WINDOW_SIZE)
    window.document.reset(graph)
    window.show()
    application.processEvents()
    return application, window


def run(sizes: tuple[int, ...]) -> None:
    game_data = db.load_game_data_from_file(db.default_database_path())
    print(f"banc d'essai version {BENCHMARK_VERSION}, mediane de {REPEATS} passages")
    print(f"{'taille':>7} {'aretes':>7} {'resolution':>12} {'edition':>12} {'deplacement':>13}")
    for size in sizes:
        graph = benchmark_graph(size)
        solve = measure_solve(graph, game_data)
        application, window = build_window(graph, game_data)
        try:
            edit = measure_edit(window, application)
            move = measure_move(window, application)
        finally:
            window.document.undo_stack.setClean()
            window.scene.dispose()
            window.close()
        print(
            f"{size:>7} {len(graph.edges):>7} {solve.median:>9.1f} ms "
            f"{edit.median:>9.1f} ms {move.median:>10.1f} ms"
        )


def profile(size: int) -> None:
    """Where the time goes on one resolution of the largest graph."""
    game_data = db.load_game_data_from_file(db.default_database_path())
    graph = benchmark_graph(size)
    profiler = cProfile.Profile()
    profiler.enable()
    engine.solve(graph, game_data)
    profiler.disable()
    buffer = io.StringIO()
    pstats.Stats(profiler, stream=buffer).sort_stats("cumulative").print_stats(25)
    print(buffer.getvalue())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sizes",
        type=int,
        nargs="+",
        default=list(BENCHMARK_SIZES),
        help="tailles de graphe a mesurer",
    )
    parser.add_argument(
        "--profile",
        type=int,
        metavar="TAILLE",
        help="profiler une resolution de cette taille au lieu de mesurer",
    )
    arguments = parser.parse_args(argv)
    if arguments.profile:
        profile(arguments.profile)
    else:
        run(tuple(arguments.sizes))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
