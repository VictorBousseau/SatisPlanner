"""The two things drawn on the canvas: a node box and a line between two ports.

A node shows its icon, its French label, the recipe, how many machines are built and
how many are useful, the rate on every input and output, and a coloured border that
says at a glance whether it is running, short of something, or stopped.

Ports are painted by the node rather than being child items: the node already knows
where every item sits, and hit-testing a handful of circles by hand is simpler than
keeping a parallel tree of child items in step with the recipe.
"""

import logging
from dataclasses import dataclass
from typing import Final

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
    QIcon,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtWidgets import (
    QGraphicsItem,
    QGraphicsPathItem,
    QGraphicsSceneHoverEvent,
    QStyleOptionGraphicsItem,
    QWidget,
)

from satisplanner.core import formatting
from satisplanner.core.graph import (
    ExternalSourceNode,
    MachineNode,
    Node,
    OutputNode,
    ResourceNode,
    StorageNode,
    WaterExtractorNode,
)
from satisplanner.core.models import GameData, ItemForm
from satisplanner.core.results import EdgeSolution, LimitingFactor, NodeSolution
from satisplanner.ui import theme
from satisplanner.ui.icon_provider import IconProvider

logger = logging.getLogger(__name__)

NODE_WIDTH: Final = 260.0
HEADER_HEIGHT: Final = 34.0
DETAIL_HEIGHT: Final = 18.0
ROW_HEIGHT: Final = 18.0
ROW_MARGIN: Final = 10.0
ROW_GAP: Final = 6.0
BOTTOM_PADDING: Final = 8.0
CORNER_RADIUS: Final = 6.0
BORDER_WIDTH: Final = 2.0
ICON_SIDE: Final = 22.0
PORT_RADIUS: Final = 5.0
# Extra slack around a port so it can be grabbed without pixel-hunting.
PORT_GRAB_SLACK: Final = 4.0

# Horizontal reach of the bezier handles, as a fraction of the gap between ports.
CURVE_TENSION: Final = 0.45
EDGE_LABEL_FONT_SIZE: Final = 8

# Port item class of a buffer whose content is not decided yet: it accepts whatever
# arrives first, and the graph infers the item from that line.
ANY_ITEM: Final = ""
ANY_ITEM_LABEL: Final = "contenu indetermine"


@dataclass(frozen=True)
class Port:
    """One connection point of a node: an item, and which side it is on."""

    item_class: str
    is_output: bool
    centre: QPointF  # local to the node item


def state_colour(solution: NodeSolution | None) -> QColor:
    """Border colour: what the liseré says.

    Grey before the first solve, red when the node is stopped, orange when it is held
    back, green when it runs at full speed.
    """
    if solution is None:
        return QColor(theme.STATE_IDLE)
    if solution.limiting is LimitingFactor.BLOCKED or solution.ratio <= 0.0:
        return QColor(theme.STATE_BLOCKED)
    if solution.limiting is LimitingFactor.NONE:
        return QColor(theme.STATE_NOMINAL)
    return QColor(theme.STATE_STARVED)


class NodeItem(QGraphicsItem):
    """A node of the factory, drawn as a box with ports down each side."""

    def __init__(
        self,
        node: Node,
        game_data: GameData,
        icons: IconProvider,
    ) -> None:
        super().__init__()
        self.node = node
        self.game_data = game_data
        self.icons = icons
        self.solution: NodeSolution | None = None
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )
        self.setAcceptHoverEvents(True)
        self.setZValue(1.0)
        self.setPos(QPointF(*node.position))
        # What a buffer holds, once the graph can tell. Set by the scene, which is the
        # only place that knows the other lines.
        self.content_item: str | None = None
        self._inputs: tuple[str, ...] = ()
        self._outputs: tuple[str, ...] = ()
        self._height = HEADER_HEIGHT
        self.relayout()

    # ---------------------------------------------------------------- geometry

    def relayout(self) -> None:
        """Recompute the port lists and the height. Call after the node changes."""
        self._inputs, self._outputs = self._port_items()
        rows = max(len(self._inputs), len(self._outputs))
        self.prepareGeometryChange()
        self._height = HEADER_HEIGHT + DETAIL_HEIGHT + rows * ROW_HEIGHT + BOTTOM_PADDING

    def _port_items(self) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """Inputs and outputs, in the recipe's own slot order rather than alphabetical.

        A player reads a recipe the way the game shows it, and the headline product is
        always the first slot.
        """
        match self.node:
            case MachineNode() as machine:
                recipe = self.game_data.recipe(machine.recipe_class)
                return (
                    tuple(slot.item_class for slot in recipe.ingredients),
                    tuple(slot.item_class for slot in recipe.products),
                )
            case StorageNode():
                content = self.content_item
                return ((content or ANY_ITEM,), (content,) if content else ())
            case OutputNode() as output:
                return ((output.item_class,), ())
            case ResourceNode() | ExternalSourceNode() as source:
                return ((), (source.item_class,))
            case WaterExtractorNode() as pump:
                item = self.game_data.extractor(pump.extractor_class).item_class
                return ((), (item,) if item else ())

    def boundingRect(self) -> QRectF:
        margin = BORDER_WIDTH + PORT_RADIUS
        return QRectF(-margin, -margin, NODE_WIDTH + 2 * margin, self._height + 2 * margin)

    def ports(self) -> list[Port]:
        """Every port, inputs first, in the order they are drawn."""
        top = HEADER_HEIGHT + DETAIL_HEIGHT + ROW_HEIGHT / 2
        found = [
            Port(item_class, False, QPointF(0.0, top + index * ROW_HEIGHT))
            for index, item_class in enumerate(self._inputs)
        ]
        found.extend(
            Port(item_class, True, QPointF(NODE_WIDTH, top + index * ROW_HEIGHT))
            for index, item_class in enumerate(self._outputs)
        )
        return found

    def port_at(self, local: QPointF) -> Port | None:
        """The port under a point in this item's coordinates, if any."""
        reach = PORT_RADIUS + PORT_GRAB_SLACK
        for port in self.ports():
            delta = local - port.centre
            if delta.x() ** 2 + delta.y() ** 2 <= reach**2:
                return port
        return None

    def port_scene_position(self, item_class: str, *, is_output: bool) -> QPointF:
        """Where a line should attach for this item, in scene coordinates.

        Falls back to the middle of the relevant side when the item has no port, which
        happens for a buffer whose content is not determined yet.
        """
        for port in self.ports():
            if port.item_class == item_class and port.is_output is is_output:
                return self.mapToScene(port.centre)
        side = NODE_WIDTH if is_output else 0.0
        return self.mapToScene(QPointF(side, self._height / 2))

    # ----------------------------------------------------------------- content

    def apply(self, solution: NodeSolution | None) -> None:
        self.solution = solution
        self.update()

    def title(self) -> str:
        return self.node.label or self._default_label()

    def _default_label(self) -> str:
        match self.node:
            case MachineNode() as machine:
                return self.game_data.recipe(machine.recipe_class).display_name_fr
            case ResourceNode() | ExternalSourceNode() | OutputNode() as endpoint:
                return self.game_data.item(endpoint.item_class).display_name_fr
            case WaterExtractorNode() as pump:
                return self.game_data.building(pump.extractor_class).display_name_fr
            case StorageNode() as storage:
                return self.game_data.building(storage.storage_class).display_name_fr

    def subtitle(self) -> str:
        """The line under the title: which building, or what the endpoint does."""
        match self.node:
            case MachineNode() as machine:
                recipe = self.game_data.recipe(machine.recipe_class)
                building = self.game_data.building(recipe.building_class).display_name_fr
                return f"{building} — {formatting.number(machine.machine_count)} machine(s)"
            case ResourceNode() as deposit:
                extractor = self.game_data.building(deposit.extractor_class).display_name_fr
                purity = {"impure": "impur", "normal": "normal", "pure": "pur"}[
                    deposit.purity.value
                ]
                count = formatting.number(deposit.count)
                return f"{count} {extractor} — gisement {purity}"
            case WaterExtractorNode() as pump:
                return f"{formatting.number(pump.count)} unite(s) — debit fixe"
            case ExternalSourceNode() as source:
                item = self.game_data.item(source.item_class)
                return f"apport externe {formatting.rate(source.rate_per_minute, item)}"
            case StorageNode() as storage:
                return self._storage_subtitle(storage)
            case OutputNode() as output:
                return "rejet assume" if output.is_sink else "sortie de l'usine"

    def _storage_subtitle(self, storage: StorageNode) -> str:
        """What the buffer holds, and whether that was decided or deduced.

        A buffer that silently keeps a content decided by a line the user has since
        removed would refuse the next line for no visible reason, so the state is
        spelled out: "(fixe)" means it was chosen and will not follow the lines.
        """
        if self.content_item is None:
            return "tampon — contenu indetermine"
        name = self.game_data.item(self.content_item).display_name_fr
        origin = "fixe" if storage.item_class else "deduit des lignes"
        stock = ""
        if storage.initial_content > 0:
            stock = f", stock initial {formatting.number(storage.initial_content)}"
        return f"tampon — {name} ({origin}){stock}"

    def icon(self) -> QIcon:
        match self.node:
            case MachineNode() as machine:
                recipe = self.game_data.recipe(machine.recipe_class)
                headline = recipe.products[0].item_class if recipe.products else None
                if headline is not None:
                    return self.icons.for_item(self.game_data.item(headline))
                return self.icons.for_building(self.game_data.building(recipe.building_class))
            case ResourceNode() | ExternalSourceNode() | OutputNode() as endpoint:
                return self.icons.for_item(self.game_data.item(endpoint.item_class))
            case WaterExtractorNode() as pump:
                return self.icons.for_building(self.game_data.building(pump.extractor_class))
            case StorageNode() as storage:
                return self.icons.for_building(self.game_data.building(storage.storage_class))

    # ----------------------------------------------------------------- drawing

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionGraphicsItem,
        widget: QWidget | None = None,
    ) -> None:
        del option, widget
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        body = QRectF(0, 0, NODE_WIDTH, self._height)

        border = QColor(theme.SELECTION) if self.isSelected() else state_colour(self.solution)
        painter.setBrush(QColor(theme.SURFACE_RAISED))
        painter.setPen(QPen(border, BORDER_WIDTH))
        painter.drawRoundedRect(body, CORNER_RADIUS, CORNER_RADIUS)

        self._paint_header(painter)
        self._paint_rows(painter)
        self._paint_ports(painter)

    def _paint_header(self, painter: QPainter) -> None:
        self.icon().paint(painter, QRectF(8, 6, ICON_SIDE, ICON_SIDE).toRect())

        title_font = QFont(painter.font())
        title_font.setBold(True)
        painter.setFont(title_font)
        painter.setPen(QColor(theme.TEXT))
        ratio_width = 52.0
        painter.drawText(
            QRectF(8 + ICON_SIDE + 6, 5, NODE_WIDTH - ICON_SIDE - ratio_width - 24, 20),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            self.title(),
        )

        if self.solution is not None:
            painter.setPen(state_colour(self.solution))
            painter.drawText(
                QRectF(NODE_WIDTH - ratio_width - 8, 5, ratio_width, 20),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                formatting.percent(self.solution.ratio),
            )

        detail_font = QFont(painter.font())
        detail_font.setBold(False)
        detail_font.setPointSizeF(max(detail_font.pointSizeF() - 1.0, 6.0))
        painter.setFont(detail_font)
        painter.setPen(QColor(theme.TEXT_MUTED))
        painter.drawText(
            QRectF(8, HEADER_HEIGHT - 16, NODE_WIDTH - 16, DETAIL_HEIGHT),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            self.subtitle(),
        )

    def _paint_rows(self, painter: QPainter) -> None:
        """One line per item, inputs down the left, outputs down the right.

        A row that has the side to itself takes the whole width; otherwise the two
        share it. The rate is never shortened -- it is the reason the row exists --
        so it is the item's name that gets elided when the space runs out.
        """
        font = QFont(painter.font())
        font.setPointSizeF(max(font.pointSizeF() - 0.5, 6.0))
        painter.setFont(font)
        top = HEADER_HEIGHT + DETAIL_HEIGHT
        rows = max(len(self._inputs), len(self._outputs))
        for index in range(rows):
            shared = index < len(self._inputs) and index < len(self._outputs)
            # When both sides are used on the same row they split the width and leave a
            # gutter between them, so an input's rate never abuts an output's name.
            width = NODE_WIDTH / 2 - ROW_MARGIN - ROW_GAP if shared else NODE_WIDTH - 2 * ROW_MARGIN
            y = top + index * ROW_HEIGHT
            if index < len(self._inputs):
                self._paint_row(
                    painter,
                    QRectF(ROW_MARGIN, y, width, ROW_HEIGHT),
                    self._inputs[index],
                    is_output=False,
                )
            if index < len(self._outputs):
                left = NODE_WIDTH - ROW_MARGIN - width
                self._paint_row(
                    painter,
                    QRectF(left, y, width, ROW_HEIGHT),
                    self._outputs[index],
                    is_output=True,
                )

    def _paint_row(
        self, painter: QPainter, cell: QRectF, item_class: str, *, is_output: bool
    ) -> None:
        painter.setPen(self._row_colour(item_class, is_output=is_output))
        centre = Qt.AlignmentFlag.AlignVCenter
        if item_class == ANY_ITEM:
            painter.drawText(cell, centre | Qt.AlignmentFlag.AlignLeft, ANY_ITEM_LABEL)
            return

        item = self.game_data.item(item_class)
        metrics = QFontMetricsF(painter.font())
        rate = ""
        if self.solution is not None:
            flows = self.solution.outputs if is_output else self.solution.inputs
            rate = formatting.rate(flows.get(item_class, 0.0), item)
        rate_width = metrics.horizontalAdvance(rate) if rate else 0.0
        name_cell = cell.adjusted(0, 0, -(rate_width + ROW_GAP if rate else 0.0), 0)
        painter.drawText(
            name_cell,
            centre | Qt.AlignmentFlag.AlignLeft,
            metrics.elidedText(
                item.display_name_fr, Qt.TextElideMode.ElideRight, name_cell.width()
            ),
        )
        if rate:
            painter.drawText(cell, centre | Qt.AlignmentFlag.AlignRight, rate)

    def _row_colour(self, item_class: str, *, is_output: bool) -> QColor:
        """Orange on the item that is actually holding the node back."""
        if item_class == ANY_ITEM or self.solution is None:
            return QColor(theme.TEXT_MUTED)
        if item_class in self.solution.blocked_products and is_output:
            return QColor(theme.STATE_BLOCKED)
        if item_class in self.solution.starved_items and not is_output:
            return QColor(theme.STATE_STARVED)
        if item_class in self.solution.line_limited_items:
            return QColor(theme.EDGE_SATURATED)
        return QColor(theme.TEXT)

    def _paint_ports(self, painter: QPainter) -> None:
        painter.setPen(QPen(QColor(theme.BACKGROUND), 1.0))
        for port in self.ports():
            painter.setBrush(QColor(self._port_colour(port.item_class)))
            painter.drawEllipse(port.centre, PORT_RADIUS, PORT_RADIUS)

    def _port_colour(self, item_class: str) -> str:
        if item_class == ANY_ITEM:
            return theme.TEXT_MUTED
        if self.game_data.item(item_class).form.is_fluid:
            return theme.PIPE_COLOUR
        return theme.BELT_COLOUR

    def hoverMoveEvent(self, event: QGraphicsSceneHoverEvent) -> None:
        """Name the port under the cursor, so a fan of ten lines stays readable."""
        port = self.port_at(event.pos())
        if port is None:
            self.setToolTip(self._node_tooltip())
        elif port.item_class == ANY_ITEM:
            self.setToolTip("entree : n'importe quel item, le tampon prend celui qui arrive")
        else:
            item = self.game_data.item(port.item_class)
            side = "sortie" if port.is_output else "entree"
            self.setToolTip(f"{side} : {item.display_name_fr} ({formatting.unit(item)})")
        super().hoverMoveEvent(event)

    def _node_tooltip(self) -> str:
        if self.solution is None:
            return self.title()
        lines = [f"{self.title()} — {formatting.percent(self.solution.ratio)}"]
        if self.solution.machine_count is not None:
            useful = formatting.number(self.solution.useful_machine_count or 0.0)
            lines.append(
                f"{useful} machine(s) utile(s) sur "
                f"{formatting.number(self.solution.machine_count)}, "
                f"{self.solution.integer_machine_count} a batir"
            )
        if self.solution.power_mw:
            lines.append(f"{formatting.number(self.solution.power_mw)} MW")
        return "\n".join(lines)


class EdgeItem(QGraphicsPathItem):
    """A conveyor or a pipe, drawn as a curve carrying its rate.

    Pipes are thicker and blue, conveyors thin and pale: the form of what travels has
    to be readable without hovering, because half the mistakes a player makes are
    trying to put a fluid on a belt.
    """

    def __init__(self, edge_id: str, form: ItemForm, game_data: GameData) -> None:
        super().__init__()
        self.edge_id = edge_id
        self.form = form
        self.game_data = game_data
        self.solution: EdgeSolution | None = None
        self.setZValue(0.0)
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )
        self.setAcceptHoverEvents(True)
        self._restyle()

    def itemChange(self, change: QGraphicsItem.GraphicsItemChange, value: object) -> object:
        """Restyle on selection here rather than in ``paint``.

        Changing the pen from inside ``paint`` schedules another repaint while one is
        in progress, which Qt does not promise to survive.
        """
        if change is QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged:
            self._restyle()
        return super().itemChange(change, value)

    def set_ends(self, start: QPointF, end: QPointF) -> None:
        self._start, self._end = start, end
        self.setPath(curve(start, end))

    def apply(self, solution: EdgeSolution | None) -> None:
        self.solution = solution
        self._restyle()
        self.setToolTip(self._tooltip())
        self.update()

    def _restyle(self) -> None:
        width = theme.PIPE_WIDTH if self.form.is_fluid else theme.BELT_WIDTH
        colour = QColor(theme.PIPE_COLOUR if self.form.is_fluid else theme.BELT_COLOUR)
        style = Qt.PenStyle.SolidLine
        if self.solution is not None and self.solution.is_saturated:
            colour = QColor(theme.EDGE_SATURATED)
            width += 1.0
            style = Qt.PenStyle.DashLine
        if self.isSelected():
            colour = QColor(theme.SELECTION)
        pen = QPen(colour, width, style)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        self.setPen(pen)

    def _tooltip(self) -> str:
        if self.solution is None:
            return ""
        item = self.game_data.item(self.solution.item_class)
        transport = self.game_data.buildings.get(self.solution.transport_class)
        name = transport.display_name_fr if transport else self.solution.transport_class
        lines = [
            f"{item.display_name_fr} — {formatting.rate(self.solution.rate_per_minute, item)}",
            f"{name} : {formatting.rate(self.solution.capacity_per_minute, item)} "
            f"({formatting.percent(self.solution.saturation)})",
        ]
        if self.solution.is_saturated:
            upgrade = self.game_data.smallest_transport_for(item.form, self.solution.demanded_rate)
            advice = (
                self.game_data.buildings[upgrade.class_name].display_name_fr
                if upgrade is not None
                else "aucun palier ne suffit, doublez la ligne"
            )
            lines.append(
                f"SATUREE : {formatting.rate(self.solution.demanded_rate, item)} demandes, "
                f"{formatting.rate(self.solution.blocked_rate, item)} refoules — {advice}"
            )
        return "\n".join(lines)

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionGraphicsItem,
        widget: QWidget | None = None,
    ) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        super().paint(painter, option, widget)
        if self.solution is None:
            return
        item = self.game_data.item(self.solution.item_class)
        font = QFont(painter.font())
        font.setPointSize(EDGE_LABEL_FONT_SIZE)
        painter.setFont(font)
        painter.setPen(self.pen().color())
        middle = self.path().pointAtPercent(0.5)
        painter.drawText(
            QRectF(middle.x() - 45, middle.y() - 14, 90, 12),
            Qt.AlignmentFlag.AlignCenter,
            formatting.rate(self.solution.rate_per_minute, item),
        )


def curve(start: QPointF, end: QPointF) -> QPainterPath:
    """A horizontal-tangent bezier, so lines leave a port sideways and read as flow."""
    path = QPainterPath(start)
    reach = max(abs(end.x() - start.x()) * CURVE_TENSION, 40.0)
    path.cubicTo(QPointF(start.x() + reach, start.y()), QPointF(end.x() - reach, end.y()), end)
    return path
