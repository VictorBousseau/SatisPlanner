"""The nodal canvas: a scene kept in step with the graph, and the view onto it.

The scene owns no state of its own. It mirrors the document's graph -- one item per
node, one per line -- and every edit it makes goes through a command on the undo
stack. Rebuilding is therefore always safe: throw the items away, read the graph
again, and the picture is right by construction.

Two interactions are worth describing:

**Dragging a line.** Pressing on an output port starts a rubber band instead of
moving the node. While the band follows the cursor, the candidate line is checked
against the graph on every move, and the band is drawn green or red accordingly. A
line that could not exist is therefore refused *while the user is still holding the
mouse*, not reported afterwards.

**Moving nodes.** Positions belong to the document, so a move is a command like any
other. The command is pushed on release with the positions from before the drag,
which is what makes a drag one undo step rather than several hundred.
"""

import logging
from collections.abc import Sequence
from typing import Final

from PySide6.QtCore import QLineF, QPointF, QRect, QRectF, Qt, Signal
from PySide6.QtGui import (
    QAction,
    QColor,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPen,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QGraphicsItem,
    QGraphicsPathItem,
    QGraphicsScene,
    QGraphicsSceneMouseEvent,
    QGraphicsView,
    QInputDialog,
    QMenu,
    QWidget,
)

from satisplanner.core import constants, engine, formatting
from satisplanner.core.graph import (
    GeneratorNode,
    MachineNode,
    ResourceNode,
    StorageNode,
    WaterExtractorNode,
    storage_item,
)
from satisplanner.core.models import ItemForm, Purity
from satisplanner.core.results import FactoryReport
from satisplanner.ui import edits, theme
from satisplanner.ui.canvas_items import ANY_ITEM, EdgeItem, NodeItem, Port, curve
from satisplanner.ui.catalogue import (
    PURITY_LABELS,
    PaletteEntry,
    build_entries,
    extractor_choices,
    fuel_choices,
    transport_choices,
)
from satisplanner.ui.commands import (
    AddNodeCommand,
    ConnectCommand,
    MoveNodesCommand,
    RemoveCommand,
    SetNodeFieldCommand,
    SetTransportCommand,
    can_connect,
)
from satisplanner.ui.document import FactoryDocument
from satisplanner.ui.icon_provider import IconProvider
from satisplanner.ui.palette import ENTRY_MIME_TYPE, decode_entry

logger = logging.getLogger(__name__)

SCENE_SIDE: Final = 100_000.0
MIN_SCALE: Final = 0.2
MAX_SCALE: Final = 3.0
ZOOM_STEP: Final = 1.15

# Below this zoom the grid is more noise than help.
GRID_VISIBLE_SCALE: Final = 0.45
MAJOR_GRID_EVERY: Final = 5


def snapped(position: QPointF) -> QPointF:
    """Nearest grid intersection, so hand-placed nodes still line up."""
    step = theme.GRID_STEP
    return QPointF(round(position.x() / step) * step, round(position.y() / step) * step)


class FactoryScene(QGraphicsScene):
    """Mirror of the document's graph, plus the link-dragging interaction."""

    selectionSummaryChanged = Signal(str)
    # An item class the user asked to read about. The scene knows which item a node
    # is about; opening a window is the main window's business.
    itemCardRequested = Signal(str)

    def __init__(
        self,
        document: FactoryDocument,
        icons: IconProvider,
        entries: Sequence[PaletteEntry] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.document = document
        self.icons = icons
        # The same list the palette shows: a drop carries class names, and the entry is
        # looked up here rather than trusted.
        self.entries = list(entries) if entries is not None else build_entries(document.game_data)
        self.setSceneRect(-SCENE_SIDE / 2, -SCENE_SIDE / 2, SCENE_SIDE, SCENE_SIDE)
        self.nodes: dict[str, NodeItem] = {}
        self.edges: dict[str, EdgeItem] = {}
        # Live link drag, or None when the user is not drawing one.
        self._band: QGraphicsPathItem | None = None
        self._band_origin: tuple[str, Port] | None = None
        # Positions before the current node drag, for the undo command.
        self._drag_origin: dict[str, tuple[float, float]] = {}
        self._default_belt = transport_choices(document.game_data, ItemForm.SOLID)[0][0]
        self._default_pipe = transport_choices(document.game_data, ItemForm.LIQUID)[0][0]

        document.graphChanged.connect(self.rebuild)
        document.reportChanged.connect(self.apply_report)
        self.selectionChanged.connect(self._announce_selection)
        self.rebuild()

    # ------------------------------------------------------------------- sync

    def rebuild(self) -> None:
        """Make the picture match the graph, keeping items that are still there."""
        graph = self.document.graph
        wanted_nodes = {node.id for node in graph.nodes}
        for node_id in set(self.nodes) - wanted_nodes:
            self.removeItem(self.nodes.pop(node_id))
        for node in graph.sorted_nodes():
            item = self.nodes.get(node.id)
            if item is None:
                item = NodeItem(node, self.document.game_data, self.icons)
                self.nodes[node.id] = item
                self.addItem(item)
            else:
                item.node = node
            if isinstance(node, StorageNode):
                item.content_item = storage_item(node, graph)
            item.relayout()
            item.setPos(QPointF(*node.position))

        wanted_edges = {edge.id for edge in graph.edges}
        for edge_id in set(self.edges) - wanted_edges:
            self.removeItem(self.edges.pop(edge_id))
        for edge in graph.sorted_edges():
            if edge.id not in self.edges:
                form = self.document.game_data.item(edge.item_class).form
                edge_item = EdgeItem(edge.id, form, self.document.game_data)
                self.edges[edge.id] = edge_item
                self.addItem(edge_item)
        self.refresh_edge_geometry()
        report = self.document.report
        if report is not None:
            self.apply_report(report)

    def set_icons(self, icons: IconProvider) -> None:
        """Adopt a new icon source, after the preferences pointed somewhere else.

        Node items take their provider once, at construction, so the honest way to
        change it is to throw them away and let :meth:`rebuild` make them again. The
        graph is untouched, so nothing here goes near the undo stack.
        """
        self.icons = icons
        for item in list(self.nodes.values()):
            self.removeItem(item)
        self.nodes.clear()
        self.rebuild()

    def dispose(self) -> None:
        """Detach from the document and let go of every item, in that order.

        A graphics item belongs to C++ as soon as it joins a scene, so the Python
        wrappers must be dropped while the scene is still alive. A window keeps its
        scene until the process ends and never needs this; a test that builds a dozen
        scenes does.
        """
        self.document.graphChanged.disconnect(self.rebuild)
        self.document.reportChanged.disconnect(self.apply_report)
        self._band = None
        self._band_origin = None
        # Take the items back from C++ one by one. Calling ``clear()`` first would
        # delete them under the wrappers still sitting in these dictionaries, and the
        # garbage collector would later walk a dangling pointer.
        for item in (*self.nodes.values(), *self.edges.values()):
            self.removeItem(item)
        self.nodes.clear()
        self.edges.clear()
        self.clear()

    def refresh_edge_geometry(self) -> None:
        """Re-anchor every line on its ports. Cheap enough to do on every frame."""
        for edge in self.document.graph.edges:
            item = self.edges.get(edge.id)
            source, target = self.nodes.get(edge.source), self.nodes.get(edge.target)
            if item is None or source is None or target is None:
                continue
            item.set_ends(
                source.port_scene_position(edge.item_class, is_output=True),
                target.port_scene_position(edge.item_class, is_output=False),
            )

    def refresh_edges_of(self, node_id: str) -> None:
        """Only the lines touching one node, for a smooth drag."""
        for edge in self.document.graph.edges:
            if node_id not in (edge.source, edge.target):
                continue
            item = self.edges.get(edge.id)
            source, target = self.nodes.get(edge.source), self.nodes.get(edge.target)
            if item is None or source is None or target is None:
                continue
            item.set_ends(
                source.port_scene_position(edge.item_class, is_output=True),
                target.port_scene_position(edge.item_class, is_output=False),
            )

    def apply_report(self, report: FactoryReport) -> None:
        """Push the solved figures into the items."""
        for node_id, item in self.nodes.items():
            try:
                item.apply(report.node(node_id))
            except KeyError:
                item.apply(None)
        for edge_id, item_edge in self.edges.items():
            try:
                item_edge.apply(report.edge(edge_id))
            except KeyError:
                item_edge.apply(None)

    # ------------------------------------------------------------------ adding

    def add_entry(self, entry: PaletteEntry, position: QPointF) -> None:
        """Create the node a palette entry stands for, snapped to the grid."""
        target = snapped(position)
        node = entry.make_node(
            self.document.next_node_id(_id_prefix(entry)), (target.x(), target.y())
        )
        self.document.undo_stack.push(AddNodeCommand(self.document, node, entry.label))
        item = self.nodes.get(node.id)
        if item is not None:
            self.clearSelection()
            item.setSelected(True)

    # ------------------------------------------------------------ mouse: links

    def mousePressEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        if event.button() is Qt.MouseButton.LeftButton:
            found = self._port_under(event.scenePos())
            if found is not None:
                node_id, port = found
                if port.is_output:
                    self._begin_band(node_id, port, event.scenePos())
                    event.accept()
                    return
        self.begin_move()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        if self._band is not None:
            self._update_band(event.scenePos())
            event.accept()
            return
        super().mouseMoveEvent(event)
        for item in self.selected_nodes():
            self.refresh_edges_of(item.node.id)

    def mouseReleaseEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        if self._band is not None:
            self._finish_band(event.scenePos())
            event.accept()
            return
        super().mouseReleaseEvent(event)
        self.end_move()

    def begin_move(self) -> None:
        """Remember where the selection was, so the drag can become one command."""
        self._drag_origin = {item.node.id: item.node.position for item in self.selected_nodes()}

    def end_move(self) -> None:
        """Turn a finished drag into one undo step, snapping every node moved."""
        if not self._drag_origin:
            return
        after: dict[str, tuple[float, float]] = {}
        for node_id, before in self._drag_origin.items():
            item = self.nodes.get(node_id)
            if item is None:
                continue
            target = snapped(item.pos())
            position = (target.x(), target.y())
            if position != before:
                after[node_id] = position
        origin, self._drag_origin = self._drag_origin, {}
        if not after:
            # Nothing moved: put the items back where the graph says they are, in case
            # a click nudged one by a pixel.
            self.refresh_edge_geometry()
            return
        self.document.undo_stack.push(
            MoveNodesCommand(self.document, {key: origin[key] for key in after}, after)
        )

    def band_colour(self) -> QColor | None:
        """Colour of the line being dragged: green if it could exist, red if not.

        Exposed so the refusal can be asserted the way the user sees it -- during the
        drag -- rather than only through the check behind it.
        """
        return None if self._band is None else self._band.pen().color()

    def _begin_band(self, node_id: str, port: Port, at: QPointF) -> None:
        self._band_origin = (node_id, port)
        band = QGraphicsPathItem()
        band.setZValue(10.0)
        band.setPen(QPen(QColor(theme.ACCENT), 2.0, Qt.PenStyle.DashLine))
        self.addItem(band)
        self._band = band
        self._update_band(at)

    def _update_band(self, at: QPointF) -> None:
        """Follow the cursor and colour the band by whether the line would be legal."""
        if self._band is None or self._band_origin is None:
            return
        node_id, port = self._band_origin
        start = self.nodes[node_id].port_scene_position(port.item_class, is_output=True)
        self._band.setPath(curve(start, at))

        colour, reason = QColor(theme.ACCENT), ""
        landing = self._landing(at)
        if landing is not None:
            target_id, item_class = landing
            problem = can_connect(
                self.document,
                node_id,
                target_id,
                item_class,
                self._default_transport(item_class),
            )
            colour = QColor(theme.EDGE_INVALID if problem else theme.EDGE_VALID)
            reason = problem or ""
        pen = QPen(colour, 2.0, Qt.PenStyle.DashLine)
        self._band.setPen(pen)
        self._band.setToolTip(reason)
        self.selectionSummaryChanged.emit(reason or "Relachez sur une entree compatible.")

    def _finish_band(self, at: QPointF) -> None:
        assert self._band is not None and self._band_origin is not None
        node_id = self._band_origin[0]
        # Read the landing before dropping the drag state: it is what says which item
        # the line carries.
        landing = self._landing(at)
        self.removeItem(self._band)
        self._band, self._band_origin = None, None

        if landing is None:
            self.selectionSummaryChanged.emit("")
            return
        target_id, item_class = landing
        problem = self.connect_nodes(node_id, target_id, item_class)
        self.selectionSummaryChanged.emit(
            f"Raccordement refuse : {problem}" if problem is not None else ""
        )

    def connect_nodes(self, source: str, target: str, item_class: str) -> str | None:
        """Draw a line at the default tier, or return why it cannot exist.

        The single door through which lines are created, whether the request comes
        from a drag or from a menu.
        """
        transport = self._default_transport(item_class)
        problem = can_connect(self.document, source, target, item_class, transport)
        if problem is not None:
            return problem
        label = self.document.game_data.item(item_class).display_name_fr
        self.document.undo_stack.push(
            ConnectCommand(self.document, source, target, item_class, transport, label)
        )
        return None

    def _landing(self, at: QPointF) -> tuple[str, str] | None:
        """The node and item a release at this point would connect to.

        A buffer with no content yet has a wildcard port, so the item comes from the
        line being dragged rather than from the port.
        """
        if self._band_origin is None:
            return None
        dragged = self._band_origin[1].item_class
        found = self._port_under(at)
        if found is not None and not found[1].is_output:
            target_id, port = found
            return target_id, (dragged if port.item_class == ANY_ITEM else port.item_class)
        # Dropping anywhere on an undecided buffer is accepted: it has one input.
        item = self._node_under(at)
        if item is not None and isinstance(item.node, StorageNode) and item.content_item is None:
            return item.node.id, dragged
        return None

    def set_default_transports(self, belt: str, pipe: str) -> None:
        """Tiers new lines are created with. Owned by the palette's tier chooser.

        Enforcing capacity would be tiresome if every new line started at Mk.1, so the
        user picks the tier they normally build once and forgets about it.
        """
        self._default_belt, self._default_pipe = belt, pipe

    def default_transport_for(self, form: ItemForm) -> str:
        return self._default_pipe if form.is_fluid else self._default_belt

    def _default_transport(self, item_class: str) -> str:
        return self.default_transport_for(self.document.game_data.item(item_class).form)

    # ---------------------------------------------------------------- lookups

    def _node_under(self, at: QPointF) -> NodeItem | None:
        for item in self.items(at):
            if isinstance(item, NodeItem):
                return item
        return None

    def _port_under(self, at: QPointF) -> tuple[str, Port] | None:
        for item in self.items(at):
            if not isinstance(item, NodeItem):
                continue
            port = item.port_at(item.mapFromScene(at))
            if port is not None:
                return item.node.id, port
        # Ports stick out past the node's body, so look a little wider as well.
        reach = QRectF(at.x() - 8, at.y() - 8, 16, 16)
        for item in self.items(reach):
            if not isinstance(item, NodeItem):
                continue
            port = item.port_at(item.mapFromScene(at))
            if port is not None:
                return item.node.id, port
        return None

    def select_nodes(self, node_ids: Sequence[str]) -> None:
        """Mirror a selection made elsewhere -- the table, or a diagnostic."""
        wanted = set(node_ids)
        self.clearSelection()
        for node_id, item in self.nodes.items():
            item.setSelected(node_id in wanted)

    def select_target(self, node_id: str, edge_id: str) -> QGraphicsItem | None:
        """Select what a diagnostic points at, and hand back the item to centre on.

        A finding carries a node or a line, never both, and sometimes neither -- the
        sustainability warning is about the whole factory.
        """
        self.clearSelection()
        item: QGraphicsItem | None = self.nodes.get(node_id) if node_id else None
        if item is None and edge_id:
            item = self.edges.get(edge_id)
        if item is not None:
            item.setSelected(True)
        return item

    def select_all(self) -> None:
        """Nodes and lines alike, so that Ctrl+A then Suppr clears the canvas."""
        for item in (*self.nodes.values(), *self.edges.values()):
            item.setSelected(True)

    def selected_nodes(self) -> list[NodeItem]:
        return [item for item in self.selectedItems() if isinstance(item, NodeItem)]

    def selected_edges(self) -> list[EdgeItem]:
        return [item for item in self.selectedItems() if isinstance(item, EdgeItem)]

    def _announce_selection(self) -> None:
        nodes, edges = len(self.selected_nodes()), len(self.selected_edges())
        if not nodes and not edges:
            self.selectionSummaryChanged.emit("")
            return
        parts = []
        if nodes:
            parts.append(f"{nodes} noeud(s)")
        if edges:
            parts.append(f"{edges} ligne(s)")
        self.selectionSummaryChanged.emit(" et ".join(parts) + " selectionne(s)")

    # --------------------------------------------------------------- deleting

    def delete_selection(self) -> None:
        nodes = {item.node.id for item in self.selected_nodes()}
        edges = {item.edge_id for item in self.selected_edges()}
        if not nodes and not edges:
            return
        pieces = []
        if nodes:
            pieces.append(f"{len(nodes)} noeud(s)")
        if edges:
            pieces.append(f"{len(edges)} ligne(s)")
        self.document.undo_stack.push(
            RemoveCommand(self.document, nodes, edges, f"suppression de {' et '.join(pieces)}")
        )

    # ---------------------------------------------------------- context menus

    def build_context_menu(self, at: QPointF, parent: QWidget) -> QMenu | None:
        """The right-click menu for whatever is under the cursor."""
        node = self._node_under(at)
        if node is not None:
            return self._node_menu(node, parent)
        for item in self.items(at):
            if isinstance(item, EdgeItem):
                return self._edge_menu(item, parent)
        return None

    def headline_item(self, node_id: str) -> str | None:
        """The item a node is *about*, for the card: its product, or what it carries.

        A machine is about what it makes -- its first product, the recipe's headline
        -- and everything else is about the item it already names.
        """
        node = self.document.graph.node(node_id)
        match node:
            case MachineNode():
                recipe = self.document.game_data.recipe(node.recipe_class)
                return recipe.products[0].item_class if recipe.products else None
            case WaterExtractorNode():
                return self.document.game_data.extractor(node.extractor_class).item_class
            case GeneratorNode():
                return node.fuel_class
            case StorageNode():
                return storage_item(node, self.document.graph)
            case _:
                return getattr(node, "item_class", None)

    def _node_menu(self, item: NodeItem, parent: QWidget) -> QMenu:
        menu = QMenu(parent)
        subject = self.headline_item(item.node.id)
        if subject is not None:
            name = self.document.game_data.item(subject).display_name_fr
            card = QAction(f"Fiche de {name}", menu)
            card.triggered.connect(
                lambda _checked=False, cls=subject: self.itemCardRequested.emit(cls)
            )
            menu.addAction(card)
            menu.addSeparator()
        if isinstance(item.node, MachineNode):
            adjust = QAction("Ajuster ce noeud a ses intrants", menu)
            adjust.triggered.connect(lambda: self.adjust_node(item.node.id))
            menu.addAction(adjust)
            count = QAction("Nombre de machines...", menu)
            count.triggered.connect(lambda: self.ask_machine_count(item.node.id, parent))
            menu.addAction(count)
        if isinstance(item.node, ResourceNode):
            menu.addMenu(self._purity_menu(item.node, menu))
            menu.addMenu(self._extractor_menu(item.node, menu))
        if isinstance(item.node, GeneratorNode):
            menu.addMenu(self._fuel_menu(item.node, menu))
        if isinstance(item.node, MachineNode | ResourceNode | WaterExtractorNode):
            clock = QAction("Cadence...", menu)
            clock.triggered.connect(lambda: self.ask_clock_speed(item.node.id, parent))
            menu.addAction(clock)
        if isinstance(item.node, StorageNode):
            menu.addMenu(self._storage_content_menu(item.node, menu))
        delete = QAction("Supprimer", menu)
        delete.triggered.connect(self.delete_selection)
        menu.addSeparator()
        menu.addAction(delete)
        return menu

    def _purity_menu(self, node: ResourceNode, parent: QMenu) -> QMenu:
        """The purity of the deposit, which nothing else in the interface can set.

        It has been in the model since the beginning and reachable from nowhere,
        which is the same as not existing. It cannot live in the palette -- ten ores
        by three miners by three purities is a hundred entries -- so it belongs on
        the node that is already on the canvas.
        """
        menu = QMenu("Purete du gisement", parent)
        for purity, label in PURITY_LABELS.items():
            action = QAction(label, menu)
            action.setCheckable(True)
            action.setChecked(node.purity is purity)
            action.triggered.connect(
                lambda _checked=False, value=purity: self.set_purity(node.id, value)
            )
            menu.addAction(action)
        return menu

    def _extractor_menu(self, node: ResourceNode, parent: QMenu) -> QMenu:
        """Swap the miner without deleting the node and its lines with it."""
        menu = QMenu("Extracteur", parent)
        for class_name, label in extractor_choices(self.document.game_data, node.item_class):
            action = QAction(label, menu)
            action.setCheckable(True)
            action.setChecked(node.extractor_class == class_name)
            action.triggered.connect(
                lambda _checked=False, cls=class_name: self.set_extractor(node.id, cls)
            )
            menu.addAction(action)
        return menu

    def _fuel_menu(self, node: GeneratorNode, parent: QMenu) -> QMenu:
        """What this bank of generators burns, among what the building accepts."""
        menu = QMenu("Carburant", parent)
        for class_name, label in fuel_choices(self.document.game_data, node.generator_class):
            action = QAction(label, menu)
            action.setCheckable(True)
            action.setChecked(node.fuel_class == class_name)
            action.triggered.connect(
                lambda _checked=False, cls=class_name: self.set_fuel(node.id, cls)
            )
            menu.addAction(action)
        return menu

    def set_purity(self, node_id: str, purity: Purity | str) -> bool:
        return self._apply(edits.set_purity(self.document, node_id, purity))

    def set_fuel(self, node_id: str, fuel_class: str) -> bool:
        return self._apply(edits.set_fuel(self.document, node_id, fuel_class))

    def set_extractor(self, node_id: str, extractor_class: str) -> bool:
        return self._apply(edits.set_extractor(self.document, node_id, extractor_class))

    def _apply(self, problem: str | None) -> bool:
        """Report a refusal in the status bar, and say whether the edit went through."""
        if problem is not None:
            self.selectionSummaryChanged.emit(problem)
            return False
        return True

    def _storage_content_menu(self, node: StorageNode, parent: QMenu) -> QMenu:
        """Choose what a buffer holds, or hand the decision back to the lines.

        Deduction is the default and covers the usual case, but two different items
        arriving leaves it undecidable, and a buffer pinned by hand must be
        un-pinnable -- otherwise it refuses the next line without saying why.
        """
        menu = QMenu("Contenu du tampon", parent)
        deduced = QAction("Deduit des lignes raccordees", menu)
        deduced.setCheckable(True)
        deduced.setChecked(node.item_class is None)
        deduced.triggered.connect(lambda: self.set_storage_item(node.id, None))
        menu.addAction(deduced)
        menu.addSeparator()

        arriving = sorted({edge.item_class for edge in self.document.graph.incoming(node.id)})
        for item_class in arriving:
            label = self.document.game_data.item(item_class).display_name_fr
            action = QAction(label, menu)
            action.setCheckable(True)
            action.setChecked(node.item_class == item_class)
            action.triggered.connect(
                lambda _checked=False, cls=item_class: self.set_storage_item(node.id, cls)
            )
            menu.addAction(action)
        if not arriving:
            empty = QAction("aucune ligne entrante", menu)
            empty.setEnabled(False)
            menu.addAction(empty)
        return menu

    def set_storage_item(self, node_id: str, item_class: str | None) -> None:
        """Pin a buffer's content, or ``None`` to go back to deducing it."""
        node = self.document.graph.node(node_id)
        assert isinstance(node, StorageNode)
        if node.item_class == item_class:
            return
        label = (
            "deduction du contenu"
            if item_class is None
            else f"contenu fixe a {self.document.game_data.item(item_class).display_name_fr}"
        )
        self.document.undo_stack.push(
            SetNodeFieldCommand(self.document, node_id, "item_class", item_class, label)
        )

    def sufficient_tier(self, edge_id: str) -> str | None:
        """The smallest tier that would carry what this line is being asked to carry.

        ``None`` when the line copes already, or when no tier is big enough and the
        answer is to double the line instead.
        """
        report = self.document.report
        if report is None:
            return None
        try:
            solution = report.edge(edge_id)
        except KeyError:
            return None
        if not solution.is_saturated:
            return None
        item = self.document.game_data.item(solution.item_class)
        upgrade = self.document.game_data.smallest_transport_for(item.form, solution.demanded_rate)
        if upgrade is None or upgrade.class_name == solution.transport_class:
            return None
        return upgrade.class_name

    def upgrade_line(self, edge_id: str) -> bool:
        """The "passer au tier suffisant" action. False when there is nothing to do."""
        target = self.sufficient_tier(edge_id)
        if target is None:
            return False
        label = self.document.game_data.building(target).display_name_fr
        self.document.undo_stack.push(SetTransportCommand(self.document, edge_id, target, label))
        return True

    def _edge_menu(self, item: EdgeItem, parent: QWidget) -> QMenu:
        menu = QMenu(parent)
        edge = self.document.graph.edge(item.edge_id)
        game_data = self.document.game_data
        upgrade = self.sufficient_tier(edge.id)
        if upgrade is not None:
            label = game_data.building(upgrade).display_name_fr
            action = QAction(f"Passer au tier suffisant ({label})", menu)
            action.triggered.connect(lambda: self.upgrade_line(edge.id))
            menu.addAction(action)
            menu.addSeparator()
        for class_name, label in transport_choices(game_data, item.form):
            action = QAction(label, menu)
            action.setCheckable(True)
            action.setChecked(class_name == edge.transport_class)
            action.triggered.connect(
                lambda _checked=False, cls=class_name, name=label: self.document.undo_stack.push(
                    SetTransportCommand(self.document, edge.id, cls, name)
                )
            )
            menu.addAction(action)
        menu.addSeparator()
        delete = QAction("Supprimer la ligne", menu)
        delete.triggered.connect(self.delete_selection)
        menu.addAction(delete)
        del parent
        return menu

    # ------------------------------------------------------------- node edits

    def adjust_node_if_machine(self, node_id: str) -> None:
        """Adjust only what can be adjusted, so a mixed selection does not raise."""
        if isinstance(self.document.graph.node(node_id), MachineNode):
            self.adjust_node(node_id)

    def adjust_node(self, node_id: str) -> None:
        """The "ajuster ce noeud" action: size one machine to what reaches it.

        A local answer on purpose. Sizing the whole factory from a target is the
        top-down mode, and that is a separate feature.
        """
        suggestion = engine.suggest_machine_count(
            self.document.graph, self.document.game_data, node_id
        )
        node = self.document.graph.node(node_id)
        assert isinstance(node, MachineNode)
        if abs(suggestion - node.machine_count) < 1e-9:
            self.selectionSummaryChanged.emit("Ce noeud est deja a la bonne taille.")
            return
        self.document.undo_stack.push(
            SetNodeFieldCommand(
                self.document,
                node_id,
                "machine_count",
                suggestion,
                f"ajustement a {suggestion:g} machine(s)",
            )
        )

    def set_clock_speed(self, node_id: str, clock_speed: float) -> bool:
        """Change one node's clock, through the door the table also uses."""
        return self._apply(edits.set_clock_speed(self.document, node_id, clock_speed))

    def ask_clock_speed(self, node_id: str, parent: QWidget) -> None:
        node = self.document.graph.node(node_id)
        assert isinstance(node, MachineNode | ResourceNode | WaterExtractorNode)
        shard = self.document.game_data.overclock_shard()
        hint = ""
        if shard is not None:
            name = self.document.game_data.item(shard.class_name).display_name_fr
            hint = f"\nAu-dela de 100 %, chaque machine consomme des {name}."
        value, accepted = QInputDialog.getDouble(
            parent,
            "Cadence",
            f"Cadence en pourcentage ({formatting.percent(constants.MIN_CLOCK_SPEED)} a "
            f"{formatting.percent(constants.MAX_CLOCK_SPEED)}) :{hint}",
            node.clock_speed * 100.0,
            constants.MIN_CLOCK_SPEED * 100.0,
            constants.MAX_CLOCK_SPEED * 100.0,
            2,
        )
        if accepted:
            self.set_clock_speed(node_id, value / 100.0)

    def ask_machine_count(self, node_id: str, parent: QWidget) -> None:
        node = self.document.graph.node(node_id)
        assert isinstance(node, MachineNode)
        value, accepted = QInputDialog.getDouble(
            parent,
            "Nombre de machines",
            "Machines construites (les decimales sont admises) :",
            node.machine_count,
            0.0,
            10_000.0,
            2,
        )
        if accepted and value != node.machine_count:
            self.document.undo_stack.push(
                SetNodeFieldCommand(
                    self.document,
                    node_id,
                    "machine_count",
                    value,
                    f"passage a {value:g} machine(s)",
                )
            )


def _id_prefix(entry: PaletteEntry) -> str:
    """Readable identifiers, since they end up in the saved file."""
    return {
        "recipe": "machine",
        "extractor": "gisement",
        "water_extractor": "pompe",
        "generator": "generateur",
        "storage": "tampon",
        "external": "entree",
        "output": "sortie",
        "sink": "rejet",
    }[entry.kind.value]


class FactoryView(QGraphicsView):
    """The window onto the scene: wheel zoom, middle-button pan, grid."""

    def __init__(self, scene: FactoryScene, parent: QWidget | None = None) -> None:
        super().__init__(scene, parent)
        self.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
        self.setBackgroundBrush(QColor(theme.CANVAS_BACKGROUND))
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.DefaultContextMenu)
        self.setAcceptDrops(True)
        self.setMouseTracking(True)
        self._panning = False
        self._pan_from = QPointF()

    @property
    def factory_scene(self) -> FactoryScene:
        scene = self.scene()
        assert isinstance(scene, FactoryScene)
        return scene

    # -------------------------------------------------------------- zoom, pan

    def zoom_by(self, step: float) -> None:
        """One zoom step, refused rather than clamped outside the allowed range.

        The single door for zooming, so the wheel and the menu cannot drift apart --
        which is exactly how ``fitInView`` ended up ignoring the limit.
        """
        scale = self.transform().m11() * step
        if MIN_SCALE <= scale <= MAX_SCALE:
            self.scale(step, step)

    def zoom_in(self) -> None:
        self.zoom_by(ZOOM_STEP)

    def zoom_out(self) -> None:
        self.zoom_by(1 / ZOOM_STEP)

    def wheelEvent(self, event: QWheelEvent) -> None:
        self.zoom_by(ZOOM_STEP if event.angleDelta().y() > 0 else 1 / ZOOM_STEP)
        event.accept()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() is Qt.MouseButton.MiddleButton:
            self._panning = True
            self._pan_from = event.position()
            self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._panning:
            delta = event.position() - self._pan_from
            self._pan_from = event.position()
            horizontal = self.horizontalScrollBar()
            vertical = self.verticalScrollBar()
            horizontal.setValue(horizontal.value() - int(delta.x()))
            vertical.setValue(vertical.value() - int(delta.y()))
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() is Qt.MouseButton.MiddleButton and self._panning:
            self._panning = False
            self.viewport().unsetCursor()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def reset_zoom(self) -> None:
        self.resetTransform()

    # ------------------------------------------------------------- keyboard

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.factory_scene.delete_selection()
            event.accept()
            return
        super().keyPressEvent(event)

    # ----------------------------------------------------------------- drops

    def dragEnterEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if event.mimeData().hasFormat(ENTRY_MIME_TYPE):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if event.mimeData().hasFormat(ENTRY_MIME_TYPE):
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        payload = event.mimeData().data(ENTRY_MIME_TYPE)
        entry = decode_entry(bytes(payload.data()), self.factory_scene.entries)
        if entry is None:
            super().dropEvent(event)
            return
        self.factory_scene.add_entry(entry, self.mapToScene(event.position().toPoint()))
        event.acceptProposedAction()

    # ------------------------------------------------------------ context menu

    def contextMenuEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        menu = self.factory_scene.build_context_menu(self.mapToScene(event.pos()), self)
        if menu is None:
            super().contextMenuEvent(event)
            return
        menu.exec(event.globalPos())

    # ------------------------------------------------------------------- grid

    def drawBackground(self, painter: QPainter, rect: QRectF | QRect) -> None:
        """A grid that fades out when zoomed too far to be useful."""
        super().drawBackground(painter, rect)
        if self.transform().m11() < GRID_VISIBLE_SCALE:
            return
        area = QRectF(rect)
        step = theme.GRID_STEP
        left = int(area.left()) - int(area.left()) % step
        top = int(area.top()) - int(area.top()) % step

        minor: list[QLineF] = []
        major: list[QLineF] = []
        x = left
        while x < area.right():
            line = QLineF(x, area.top(), x, area.bottom())
            (major if (x // step) % MAJOR_GRID_EVERY == 0 else minor).append(line)
            x += step
        y = top
        while y < area.bottom():
            line = QLineF(area.left(), y, area.right(), y)
            (major if (y // step) % MAJOR_GRID_EVERY == 0 else minor).append(line)
            y += step

        painter.setPen(QPen(QColor(theme.GRID_LINE), 0))
        painter.drawLines(minor)
        painter.setPen(QPen(QColor(theme.GRID_LINE_MAJOR), 0))
        painter.drawLines(major)
