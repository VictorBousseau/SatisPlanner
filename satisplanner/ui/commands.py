"""Undoable edits. Every change to the factory is one of these -- no exceptions.

Each command owns the state it needs to put back: a deleted node keeps a copy of
itself *and* of every line that was attached to it, because undoing a deletion has to
restore the connections too, not just the box.

Command texts are French and appear verbatim in the Edit menu ("Annuler : ajout de
Fonderie"), which is why they name what the user did rather than which class changed.

The reference back to the document is **weak**, and that is not an optimisation. A
pushed command belongs to the stack in C++, the stack belongs to the document, and a
strong reference here would close the loop through a Python object. The collector
would then be free to finalise the Python wrapper of a command after C++ had already
deleted the command itself -- which on Windows shows up as a heap corruption in an
unrelated test minutes later. A command that outlives its document has nothing to do
anyway, so weak is also the honest relationship.
"""

import weakref
from typing import Final

from PySide6.QtGui import QUndoCommand

from satisplanner.core.graph import Edge, GraphError, Node, check_edge
from satisplanner.ui.document import FactoryDocument

# Merge identifier for consecutive moves, so nudging a node with the arrow keys ten
# times is one undo step rather than ten.
MOVE_COMMAND_ID: Final = 1000


class DeadDocumentError(RuntimeError):
    """A command was replayed after its document had been destroyed."""


class _DocumentCommand(QUndoCommand):
    """Common plumbing: reach the document, and redraw after every change."""

    def __init__(self, document: FactoryDocument, text: str) -> None:
        super().__init__(text)
        self._document = weakref.ref(document)

    @property
    def document(self) -> FactoryDocument:
        document = self._document()
        if document is None:
            msg = "commande rejouee apres la fermeture de son document"
            raise DeadDocumentError(msg)
        return document

    def _done(self) -> None:
        self.document.touch()


class AddNodeCommand(_DocumentCommand):
    """Drop a node on the canvas."""

    def __init__(self, document: FactoryDocument, node: Node, label: str) -> None:
        super().__init__(document, f"ajout de {label}")
        self.node = node

    def redo(self) -> None:
        self.document.graph.add_node(self.node)
        self._done()

    def undo(self) -> None:
        self.document.graph.remove_node(self.node.id)
        self._done()


class ConnectCommand(_DocumentCommand):
    """Draw a line between two ports.

    The edge is validated here as well as during the drag: a command can be redone
    long after the drag, on a graph the user has changed in between.
    """

    def __init__(
        self,
        document: FactoryDocument,
        source: str,
        target: str,
        item_class: str,
        transport_class: str,
        label: str,
    ) -> None:
        super().__init__(document, f"raccordement de {label}")
        self.edge = Edge(
            id=document.next_edge_id(),
            source=source,
            target=target,
            item_class=item_class,
            transport_class=transport_class,
        )

    def redo(self) -> None:
        check_edge(self.document.graph, self.edge, self.document.game_data)
        self.document.graph.edges.append(self.edge)
        self._done()

    def undo(self) -> None:
        self.document.graph.remove_edge(self.edge.id)
        self._done()


class RemoveCommand(_DocumentCommand):
    """Delete a selection of nodes and lines, and put it all back on undo."""

    def __init__(
        self,
        document: FactoryDocument,
        node_ids: set[str],
        edge_ids: set[str],
        text: str,
    ) -> None:
        super().__init__(document, text)
        graph = document.graph
        self.nodes: list[Node] = [node for node in graph.nodes if node.id in node_ids]
        # A line goes when either end goes, so deleting a node deletes its lines too.
        self.edges: list[Edge] = [
            edge
            for edge in graph.edges
            if edge.id in edge_ids or edge.source in node_ids or edge.target in node_ids
        ]

    def redo(self) -> None:
        graph = self.document.graph
        doomed_nodes = {node.id for node in self.nodes}
        doomed_edges = {edge.id for edge in self.edges}
        graph.nodes = [node for node in graph.nodes if node.id not in doomed_nodes]
        graph.edges = [edge for edge in graph.edges if edge.id not in doomed_edges]
        self._done()

    def undo(self) -> None:
        graph = self.document.graph
        graph.nodes.extend(self.nodes)
        graph.edges.extend(self.edges)
        self._done()


class MoveNodesCommand(_DocumentCommand):
    """Move a selection. Positions are part of the document, so moving is undoable."""

    def __init__(
        self,
        document: FactoryDocument,
        before: dict[str, tuple[float, float]],
        after: dict[str, tuple[float, float]],
    ) -> None:
        plural = "s" if len(after) > 1 else ""
        super().__init__(document, f"deplacement de {len(after)} noeud{plural}")
        self.before = before
        self.after = after

    def id(self) -> int:
        return MOVE_COMMAND_ID

    def mergeWith(self, other: QUndoCommand) -> bool:
        """Fold a following move of the same nodes into this one."""
        if not isinstance(other, MoveNodesCommand) or other.before.keys() != self.after.keys():
            return False
        self.after = other.after
        return True

    def _apply(self, positions: dict[str, tuple[float, float]]) -> None:
        for node_id, position in positions.items():
            self.document.graph.node(node_id).position = position
        self._done()

    def redo(self) -> None:
        self._apply(self.after)

    def undo(self) -> None:
        self._apply(self.before)


class SetNodeFieldCommand(_DocumentCommand):
    """Change one scalar of one node: machine count, purity, rate, stored item...

    A single command for every field rather than one class each: the fields differ
    only by their name and their label, and a generic command keeps the undo stack
    behaviour identical across all of them.
    """

    def __init__(
        self,
        document: FactoryDocument,
        node_id: str,
        field: str,
        value: object,
        text: str,
    ) -> None:
        super().__init__(document, text)
        self.node_id = node_id
        self.field = field
        self.value = value
        self.previous = getattr(document.graph.node(node_id), field)

    def _set(self, value: object) -> None:
        setattr(self.document.graph.node(self.node_id), self.field, value)
        self._done()

    def redo(self) -> None:
        self._set(self.value)

    def undo(self) -> None:
        self._set(self.previous)


class SetTransportCommand(_DocumentCommand):
    """Upgrade or downgrade one line's tier."""

    def __init__(
        self, document: FactoryDocument, edge_id: str, transport_class: str, label: str
    ) -> None:
        super().__init__(document, f"passage de la ligne en {label}")
        self.edge_id = edge_id
        self.transport_class = transport_class
        self.previous = document.graph.edge(edge_id).transport_class

    def _set(self, transport_class: str) -> None:
        edge = self.document.graph.edge(self.edge_id)
        candidate = edge.model_copy(update={"transport_class": transport_class})
        check_edge(self.document.graph, candidate, self.document.game_data)
        edge.transport_class = transport_class
        self._done()

    def redo(self) -> None:
        self._set(self.transport_class)

    def undo(self) -> None:
        self._set(self.previous)


def can_connect(
    document: FactoryDocument,
    source: str,
    target: str,
    item_class: str,
    transport_class: str,
) -> str | None:
    """``None`` when the line is legal, otherwise the French reason it is not.

    Used while the user is still dragging, so an impossible connection is refused on
    screen instead of being reported once it is too late.
    """
    candidate = Edge(
        id="?",
        source=source,
        target=target,
        item_class=item_class,
        transport_class=transport_class,
    )
    if source == target:
        return "une ligne ne peut pas boucler sur le meme noeud"
    if any(
        edge.source == source and edge.target == target and edge.item_class == item_class
        for edge in document.graph.edges
    ):
        return "cette ligne existe deja"
    try:
        check_edge(document.graph, candidate, document.game_data)
    except GraphError as exc:
        return str(exc)
    return None
