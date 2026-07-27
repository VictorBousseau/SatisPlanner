"""Copy, cut and paste a piece of factory, through the system clipboard.

The payload is a **share code**: the same string the "copier le code de partage"
action produces, and read back by the same decoder. That is deliberate. A second
serialisation format would be a second thing to migrate, a second thing to get wrong
on a schema bump, and a second place for a corrupt payload to crash the application.
There is one format, and it already refuses versions from the future in French.

It is **read** from a private MIME type, so that a paste between two SatisPlanner
windows is unambiguous and a clipboard full of prose is never mistaken for a factory.
It is **written** under that type *and* as plain text, because a clipboard holds one
thing at a time: setting ours necessarily replaces whatever was there, and leaving a
readable share code behind is a great deal better than leaving an empty clipboard.

**A clipboard that does not hold one of ours is not an error.** Someone pressing
Ctrl+V after copying a URL should get nothing at all -- no box, no beep, no message.
Every failure here is therefore a ``None`` and a debug line, never an exception.
"""

import logging
from collections.abc import Sequence
from typing import Final

from PySide6.QtCore import QMimeData
from PySide6.QtWidgets import QApplication

from satisplanner.core.graph import Edge, FactoryGraph, Node
from satisplanner.data import factory_file

logger = logging.getLogger(__name__)

# Private to the application, so a paste can tell "a SatisPlanner selection" from
# "some text that happens to look like one".
CLIPBOARD_MIME: Final = "application/x-satisplanner-sélection"

# How far a pasted copy is offset from the original, in scene units. Two grid steps:
# far enough that the copy is visibly a second object, near enough to stay in view.
PASTE_OFFSET: Final = 48.0


def selection_graph(graph: FactoryGraph, node_ids: Sequence[str]) -> FactoryGraph:
    """The chosen nodes, with the lines that run **between** them and no others.

    A line leaving the selection has one end that is not being copied, so there is
    nothing for it to attach to on the other side. Keeping it would mean either
    inventing a node or leaving a dangling reference, and both are worse than the
    honest answer: the copy is the selection, and it comes unwired.
    """
    wanted = set(node_ids)
    nodes: list[Node] = [node for node in graph.sorted_nodes() if node.id in wanted]
    edges: list[Edge] = [
        edge for edge in graph.sorted_edges() if edge.source in wanted and edge.target in wanted
    ]
    return FactoryGraph(nodes=nodes, edges=edges)


def encode(graph: FactoryGraph, node_ids: Sequence[str]) -> str | None:
    """The selection as a share code, or ``None`` when nothing is selected."""
    if not node_ids:
        return None
    return factory_file.encode_share_code(selection_graph(graph, node_ids))


def write(graph: FactoryGraph, node_ids: Sequence[str]) -> bool:
    """Put the selection on the system clipboard. False when there was nothing to put."""
    code = encode(graph, node_ids)
    if code is None:
        return False
    payload = QMimeData()
    payload.setData(CLIPBOARD_MIME, code.encode("utf-8"))
    # The same code as text, since the clipboard was going to be overwritten either
    # way: a user who pastes into a chat window gets a factory rather than nothing.
    payload.setText(code)
    QApplication.clipboard().setMimeData(payload)
    logger.debug("%d nœud(s) dans le presse-papiers", len(node_ids))
    return True


def read() -> FactoryGraph | None:
    """What is on the clipboard, if it is one of ours and it is intact.

    Anything else -- an empty clipboard, a URL, a truncated code, a document from a
    newer schema -- comes back as ``None``. The share-code decoder does the refusing;
    this only makes sure the refusal is quiet.
    """
    payload = QApplication.clipboard().mimeData()
    if payload is None or not payload.hasFormat(CLIPBOARD_MIME):
        return None
    try:
        code = bytes(payload.data(CLIPBOARD_MIME).data()).decode("utf-8")
    except UnicodeDecodeError:
        logger.debug("presse-papiers illisible : ce n'est pas de l'UTF-8")
        return None
    return decode(code)


def decode(code: str) -> FactoryGraph | None:
    """A share code back into a graph, quietly refusing anything that is not one."""
    try:
        return factory_file.decode_share_code(code).graph
    except factory_file.FactoryFileError as exc:
        logger.debug("presse-papiers refusé : %s", exc)
        return None
