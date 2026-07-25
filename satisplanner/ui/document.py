"""The edited factory: the graph, the catalogue, the undo stack, and the last report.

Everything that changes the factory goes through a :class:`QUndoCommand` pushed onto
this object's stack -- there is no back door. Widgets read the graph and the report,
and never write either.

The engine is re-run after every change, but not *during* one: dragging a node emits
a change per pixel, and solving a big graph a hundred times a second would make the
canvas stutter. Changes therefore restart a short timer and the solve happens once
the user stops moving. :meth:`solve_now` is the synchronous door, used by the tests
and before anything that has to read a fresh report.
"""

import logging
from typing import Final

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QUndoStack

from satisplanner.core import engine
from satisplanner.core.graph import FactoryGraph, Node
from satisplanner.core.models import GameData
from satisplanner.core.results import FactoryReport

logger = logging.getLogger(__name__)

# Quiet period before the engine runs again. Long enough to swallow a drag, short
# enough that releasing the mouse feels like an immediate answer.
SOLVE_DELAY_MS: Final = 120

# How many undo steps to keep. Well beyond any editing session, and bounded so a
# long one cannot grow without limit.
UNDO_LIMIT: Final = 500


class FactoryDocument(QObject):
    """One factory being edited."""

    graphChanged = Signal()
    reportChanged = Signal(FactoryReport)

    def __init__(self, game_data: GameData, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.game_data = game_data
        self.graph = FactoryGraph()
        self.undo_stack = QUndoStack(self)
        self.undo_stack.setUndoLimit(UNDO_LIMIT)
        self._report: FactoryReport | None = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(SOLVE_DELAY_MS)
        self._timer.timeout.connect(self._recompute)

    # ------------------------------------------------------------------ state

    @property
    def report(self) -> FactoryReport | None:
        """The last computed answer, or ``None`` before the first solve."""
        return self._report

    def node(self, node_id: str) -> Node:
        return self.graph.node(node_id)

    # ----------------------------------------------------------------- change

    def touch(self) -> None:
        """Declare the graph modified: redraw now, recompute in a moment.

        Called by the commands, never by a widget directly.
        """
        self.graphChanged.emit()
        self._timer.start()

    def solve_now(self) -> FactoryReport:
        """Run the engine immediately and return the report."""
        self._timer.stop()
        return self._recompute()

    def _recompute(self) -> FactoryReport:
        report = engine.solve(self.graph, self.game_data)
        self._report = report
        logger.debug(
            "resolution : %d noeud(s), %d iteration(s), %d diagnostic(s)",
            len(report.nodes),
            report.iterations,
            len(report.diagnostics),
        )
        self.reportChanged.emit(report)
        return report

    # ------------------------------------------------------------ identifiers

    def next_node_id(self, prefix: str) -> str:
        """A free identifier of the form ``prefix3``, stable and readable in saves."""
        taken = {node.id for node in self.graph.nodes}
        index = 1
        while f"{prefix}{index}" in taken:
            index += 1
        return f"{prefix}{index}"

    def next_edge_id(self) -> str:
        taken = {edge.id for edge in self.graph.edges}
        index = 1
        while f"e{index}" in taken:
            index += 1
        return f"e{index}"
