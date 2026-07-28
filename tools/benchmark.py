"""Measure what the user waits for, at three factory sizes.

    py -3.12 tools/benchmark.py
    py -3.12 tools/benchmark.py --profile 500
    py -3.12 tools/benchmark.py --tabs

Three figures per size, each one a whole gesture rather than a function call:

* **resolution** -- one complete ``engine.solve`` on the graph;
* **edition** -- from the edit that changes a number to the display being right
  again: the command, the redraw, the solve, the panels, the repaint;
* **deplacement** -- from grabbing a node to letting it go, with everything that
  follows.

``--tabs`` answers a different question: what opening five more factories costs
the sixth. It measures one edit with a single tab open and the same edit with
six, and it reports the process's memory before and after those six are loaded --
because the benchmark measures time and six scenes' worth of graphics items are a
size, not a duration.

The deliberate 120 ms quiet period before the engine runs is **not** counted. It
is a design decision, not a cost, and including it would hide whatever the work
underneath actually takes.

The window is rendered offscreen so that two runs are comparable and no window
flashes up. That makes these numbers a fair measure of the CPU work and of the
raster painting, and not a measure of the compositor.

No dependency beyond the standard library: ``time.perf_counter`` for the timing,
``cProfile`` for the ``--profile`` mode, and ``ctypes`` against the API Windows
already exposes for the memory figure -- adding ``psutil`` to read one number
would be a dependency for the whole project's sake of one line here.
"""

import argparse
import cProfile
import ctypes
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
    print(f"{'taille':>7} {'arêtes':>7} {'résolution':>12} {'édition':>12} {'déplacement':>13}")
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


class _MemoryCounters(ctypes.Structure):
    """``PROCESS_MEMORY_COUNTERS``, enough of it to read the working set."""

    _fields_ = (
        ("cb", ctypes.c_uint32),
        ("PageFaultCount", ctypes.c_uint32),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    )


def working_set_mb() -> float | None:
    """What this process is holding, in megabytes, or ``None`` off Windows.

    Deliberately not ``tracemalloc``: it counts what Python allocated, and the
    six scenes being weighed here are graphics items living in C++. Measuring the
    half that does not matter would be worse than not measuring.
    """
    if sys.platform != "win32":
        return None
    counters = _MemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    kernel32 = ctypes.windll.kernel32
    kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    # Spelled out, because the default is a 32-bit int and the pseudo-handle then
    # arrives at a 64-bit parameter half written.
    read = ctypes.windll.psapi.GetProcessMemoryInfo
    read.argtypes = (ctypes.c_void_p, ctypes.POINTER(_MemoryCounters), ctypes.c_uint32)
    read.restype = ctypes.c_int
    if not read(kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
        return None
    return float(counters.WorkingSetSize) / (1024 * 1024)


def run_tabs(size: int, count: int) -> None:
    """What five more open factories cost the one being edited.

    The same edit, measured with one tab open and with six. They must cost the
    same: the five that are not being looked at are not solved, not redrawn and
    not consulted, and a figure that grew with the number of tabs would mean one
    of those three is not true.
    """
    game_data = db.load_game_data_from_file(db.default_database_path())
    graph = benchmark_graph(size)
    application, window = build_window(graph, game_data)
    try:
        alone = measure_edit(window, application)
        before = working_set_mb()
        for _ in range(count - 1):
            window.new_tab().document.reset(benchmark_graph(size))
        window.select_tab(window.open_tabs()[0])
        _settle(application)
        after = working_set_mb()
        crowded = measure_edit(window, application)
    finally:
        window.dispose()
        window.close()

    print(f"banc d'essai version {BENCHMARK_VERSION}, mediane de {REPEATS} passages")
    print(f"usines de {size} nœuds, {len(graph.edges)} arêtes")
    print(f"{'onglets ouverts':>16} {'édition':>12}")
    print(f"{1:>16} {alone.median:>9.1f} ms")
    print(f"{count:>16} {crowded.median:>9.1f} ms")
    if before is not None and after is not None:
        print(
            f"\nmémoire du processus : {before:.0f} Mo avec 1 onglet, "
            f"{after:.0f} Mo avec {count} — soit {(after - before) / (count - 1):.0f} Mo "
            f"par usine de {size} nœuds"
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
        help="profiler une résolution de cette taille au lieu de mesurer",
    )
    parser.add_argument(
        "--tabs",
        nargs="?",
        type=int,
        const=6,
        metavar="NOMBRE",
        help="mesurer une édition avec un onglet puis avec NOMBRE onglets (6 par défaut)",
    )
    arguments = parser.parse_args(argv)
    if arguments.profile:
        profile(arguments.profile)
    elif arguments.tabs:
        run_tabs(max(arguments.sizes), arguments.tabs)
    else:
        run(tuple(arguments.sizes))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
