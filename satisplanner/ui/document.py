"""The edited factory: the graph, the catalogue, the undo stack, and the last report.

Everything that changes the factory goes through a :class:`QUndoCommand` pushed onto
this object's stack -- there is no back door. Widgets read the graph and the report,
and never write either.

The engine is re-run after every change, but not *during* one: dragging a node emits
a change per pixel, and solving a big graph a hundred times a second would make the
canvas stutter. Changes therefore restart a short timer and the solve happens once
the user stops moving. :meth:`solve_now` is the synchronous door, used by the tests
and before anything that has to read a fresh report.

"Modified" is read off the undo stack rather than tracked by hand: undoing back to
the point where the file was saved makes the document clean again, which is what a
user expects and what a hand-kept boolean always gets wrong.
"""

import logging
from pathlib import Path
from typing import Final

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QUndoStack

from satisplanner.core import engine
from satisplanner.core.graph import FactoryGraph, Node
from satisplanner.core.models import GameData
from satisplanner.core.results import FactoryReport
from satisplanner.data import factory_file

logger = logging.getLogger(__name__)

# Quiet period before the engine runs again. Long enough to swallow a drag, short
# enough that releasing the mouse feels like an immediate answer.
SOLVE_DELAY_MS: Final = 120

# How many undo steps to keep. Well beyond any editing session, and bounded so a
# long one cannot grow without limit.
UNDO_LIMIT: Final = 500

UNTITLED: Final = "Usine sans titre"


class FactoryDocument(QObject):
    """One factory being edited."""

    graphChanged = Signal()
    reportChanged = Signal(FactoryReport)
    # Emitted whenever the window title should change: a new file, or the first edit
    # after a save.
    identityChanged = Signal()

    def __init__(self, game_data: GameData, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.game_data = game_data
        self.graph = FactoryGraph()
        self.undo_stack = QUndoStack(self)
        self.undo_stack.setUndoLimit(UNDO_LIMIT)
        # A bound method rather than a lambda: Qt then knows the document is the
        # receiver and drops the connection when it is destroyed, instead of firing
        # into an object that no longer exists.
        self.undo_stack.cleanChanged.connect(self._clean_changed)
        self._path: Path | None = None
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

    @property
    def path(self) -> Path | None:
        """Where this factory lives, or ``None`` while it has never been saved."""
        return self._path

    @property
    def is_modified(self) -> bool:
        return not self.undo_stack.isClean()

    def _clean_changed(self, _clean: bool) -> None:
        self.identityChanged.emit()

    @property
    def display_name(self) -> str:
        return UNTITLED if self._path is None else self._path.stem

    def node(self, node_id: str) -> Node:
        return self.graph.node(node_id)

    # ------------------------------------------------------------ persistence

    def reset(self, graph: FactoryGraph | None = None, path: Path | None = None) -> None:
        """Replace the whole factory. Clears the history: there is nothing to undo
        back into, and offering it would restore half of the previous document."""
        self.graph = graph if graph is not None else FactoryGraph()
        self._path = path
        self.undo_stack.clear()
        self.undo_stack.setClean()
        self.graphChanged.emit()
        self.identityChanged.emit()
        self.solve_now()

    def open(self, path: Path) -> factory_file.LoadedFactory:
        """Load a ``.sfp``. Raises :class:`FactoryFileError` with a French reason."""
        loaded = factory_file.load(path)
        self.adopt(loaded.graph, path, loaded.warnings)
        return loaded

    def adopt(
        self,
        graph: FactoryGraph,
        path: Path | None = None,
        warnings: list[str] | None = None,
    ) -> list[str]:
        """Take on a factory from anywhere, dropping what the catalogue cannot describe.

        A document that comes in is never "modified": it is exactly what was received
        until the user changes something.
        """
        missing, removed = factory_file.prune_unknown(graph, self.game_data)
        if missing and warnings is not None:
            warnings.append(factory_file.describe_unknown(missing, removed))
        self.reset(graph, path)
        return missing

    def save_as(self, path: Path, thumbnail: bytes | None = None) -> None:
        factory_file.save(path, self.graph, thumbnail)
        self._path = path
        self.undo_stack.setClean()
        self.identityChanged.emit()

    def share_code(self) -> str:
        return factory_file.encode_share_code(self.graph)

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
