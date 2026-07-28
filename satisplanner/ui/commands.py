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

from satisplanner.core.graph import Edge, FactoryGraph, GraphError, Node, check_edge
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
            msg = "commande rejouée après la fermeture de son document"
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


class PasteCommand(_DocumentCommand):
    """Drop a copied piece of factory in, as **one** undo step.

    The identifiers are renumbered against the document as it stands when the command
    is built, and the result is kept: undoing and redoing a paste must put back the
    very same nodes, or every command pushed after it would be pointing at names that
    no longer exist.
    """

    def __init__(
        self,
        document: FactoryDocument,
        pasted: FactoryGraph,
        offset: float,
        text: str | None = None,
    ) -> None:
        count = len(pasted.nodes)
        plural = "s" if count > 1 else ""
        super().__init__(document, text or f"collage de {count} nœud{plural}")
        self.nodes, self.edges = _renumbered(document, pasted, offset)

    @property
    def node_ids(self) -> list[str]:
        """What the paste created, so the canvas can select it."""
        return [node.id for node in self.nodes]

    def redo(self) -> None:
        graph = self.document.graph
        graph.nodes.extend(self.nodes)
        graph.edges.extend(self.edges)
        self._done()

    def undo(self) -> None:
        graph = self.document.graph
        added = {node.id for node in self.nodes}
        graph.nodes = [node for node in graph.nodes if node.id not in added]
        edge_ids = {edge.id for edge in self.edges}
        graph.edges = [edge for edge in graph.edges if edge.id not in edge_ids]
        self._done()


def _renumbered(
    document: FactoryDocument, pasted: FactoryGraph, offset: float
) -> tuple[list[Node], list[Edge]]:
    """Fresh identifiers and an offset position, keeping every other field.

    The prefix of the original name is reused when it has one -- ``machine3`` pasted
    into a document that already has three machines becomes ``machine4`` -- so the
    identifiers a user sees in the table stay readable rather than turning into
    hashes.
    """
    taken = {node.id for node in document.graph.nodes}
    renamed: dict[str, str] = {}
    nodes: list[Node] = []
    for original in pasted.sorted_nodes():
        fresh = _free_id(_prefix_of(original.id), taken)
        taken.add(fresh)
        renamed[original.id] = fresh
        moved = (original.position[0] + offset, original.position[1] + offset)
        # ``show_deployed`` and every other field ride along untouched: a copy of an
        # overclocked pure deposit is an overclocked pure deposit.
        nodes.append(original.model_copy(update={"id": fresh, "position": moved}))

    used_edges = {edge.id for edge in document.graph.edges}
    edges: list[Edge] = []
    for original_edge in pasted.sorted_edges():
        fresh_edge = _free_id("e", used_edges)
        used_edges.add(fresh_edge)
        edges.append(
            original_edge.model_copy(
                update={
                    "id": fresh_edge,
                    "source": renamed[original_edge.source],
                    "target": renamed[original_edge.target],
                }
            )
        )
    return nodes, edges


def _prefix_of(node_id: str) -> str:
    """``machine12`` -> ``machine``. Anything with no trailing digits keeps its name."""
    stripped = node_id.rstrip("0123456789")
    return stripped or node_id


def _free_id(prefix: str, taken: set[str]) -> str:
    index = 1
    while f"{prefix}{index}" in taken:
        index += 1
    return f"{prefix}{index}"


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
    """Move a selection. Positions are part of the document, so moving is undoable.

    The one command that does **not** call :meth:`_done`. Every other edit changes
    what the factory produces and has to be answered by a redraw and a resolution;
    this one changes where a box is drawn. It therefore announces itself on the
    document's other signal, which moves the items concerned and stops there.
    """

    def __init__(
        self,
        document: FactoryDocument,
        before: dict[str, tuple[float, float]],
        after: dict[str, tuple[float, float]],
    ) -> None:
        plural = "s" if len(after) > 1 else ""
        super().__init__(document, f"déplacement de {len(after)} nœud{plural}")
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
        self.document.moved(list(positions))

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
        return "une ligne ne peut pas boucler sur le même nœud"
    if any(
        edge.source == source and edge.target == target and edge.item_class == item_class
        for edge in document.graph.edges
    ):
        return "cette ligne existe déjà"
    try:
        check_edge(document.graph, candidate, document.game_data)
    except GraphError as exc:
        return str(exc)
    return None
