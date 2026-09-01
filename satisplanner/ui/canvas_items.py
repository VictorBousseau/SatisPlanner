"""The two things drawn on the canvas: a node box and a line between two ports.

A node shows its icon, its label, the recipe, how many machines are built and
how many are useful, the rate on every input and output, and a coloured border that
says at a glance whether it is running, short of something, or stopped.

Ports are painted by the node rather than being child items: the node already knows
where every item sits, and hit-testing a handful of circles by hand is simpler than
keeping a parallel tree of child items in step with the recipe.
"""

import logging
from dataclasses import dataclass
from enum import StrEnum
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
    GeneratorNode,
    GeothermalNode,
    MachineNode,
    MergerNode,
    Node,
    OutputNode,
    ResourceNode,
    ResourceWellNode,
    SplitterNode,
    StorageNode,
    WaterExtractorNode,
    attachment_building,
    machine_building,
    node_output_items,
    port_line_budget,
    unit_count,
)
from satisplanner.core.i18n import _
from satisplanner.core.models import GameData, Item, ItemForm, Purity, SplitterMode
from satisplanner.core.results import EdgeSolution, LimitingFactor, NodeSolution
from satisplanner.ui import item_colours, theme
from satisplanner.ui.catalogue import purity_label, splitter_mode_label
from satisplanner.ui.icon_provider import IconProvider
from satisplanner.ui.item_colours import ItemPalette

logger = logging.getLogger(__name__)

# The width every node starts at, and the width past which a long row is elided
# rather than allowed to keep stretching the box.
NODE_WIDTH: Final = 260.0
MAX_NODE_WIDTH: Final = 440.0
HEADER_HEIGHT: Final = 34.0
DETAIL_HEIGHT: Final = 18.0
ROW_HEIGHT: Final = 18.0
ROW_MARGIN: Final = 10.0
ROW_GAP: Final = 6.0
BOTTOM_PADDING: Final = 8.0
CORNER_RADIUS: Final = 6.0
BORDER_WIDTH: Final = 2.0
ICON_SIDE: Final = 22.0

# Deployed rendering: one thumbnail per built machine, in a band under the subtitle.
# The side is chosen so that the default ceiling of twelve fits on a single row of a
# 260 px node with room left for the "... xN" that follows a truncated bank.
DEPLOYED_SIDE: Final = 16.0
DEPLOYED_GAP: Final = 3.0
DEPLOYED_ROW_HEIGHT: Final = DEPLOYED_SIDE + DEPLOYED_GAP
DEPLOYED_BAND_PADDING: Final = 6.0
# Thumbnails per row, from the node's own width. A grid rather than a single row: the
# ceiling is the user's setting, and one that does not fit across the box has to wrap
# rather than run off the edge -- which is also where the "... xN" ends up when the
# last row is already full.
DEPLOYED_PER_ROW: Final = int((NODE_WIDTH - 2 * ROW_MARGIN + DEPLOYED_GAP) / DEPLOYED_ROW_HEIGHT)
# Under this fraction of a machine, nothing is drawn: a sliver two pixels wide reads
# as a rendering glitch rather than as "a third of an assembler".
DEPLOYED_MIN_FRACTION: Final = 0.05
# How far the secondary text is faded towards its background.
MUTED_TEXT_ALPHA: Final = 0.65

PORT_RADIUS: Final = 5.0
# Extra slack around a port so it can be grabbed without pixel-hunting.
PORT_GRAB_SLACK: Final = 4.0

# Horizontal reach of the bezier handles, as a fraction of the gap between ports.
CURVE_TENSION: Final = 0.45
EDGE_LABEL_FONT_SIZE: Final = 8
# Wide enough for "1 200 / 1 800 m³/min", which is the longest a line label gets
# once the demanded rate is written beside the carried one.
EDGE_LABEL_WIDTH: Final = 130.0

# Port item class of a buffer whose content is not decided yet: it accepts whatever
# arrives first, and the graph infers the item from that line.
ANY_ITEM: Final = ""


def any_item_label() -> str:
    """What an undecided port is called. A function, so it follows the language."""
    return _("contenu indéterminé")


@dataclass(frozen=True)
class Port:
    """One connection point of a node: an item, and which side it is on."""

    item_class: str
    is_output: bool
    centre: QPointF  # local to the node item


class Field(StrEnum):
    """A value shown on a node that a double-click can edit.

    Deliberately coarse. ``QUANTITY`` is "however many of a thing this node stands
    for" -- machines, extractors, generators, cubic metres in a tank, items a minute
    coming in from outside -- exactly as the table's one Quantite column is, and for
    the same reason: the node shows one number, not five differently-named ones.
    """

    QUANTITY = "quantity"
    CLOCK = "clock"
    PURITY = "purity"
    EXTRACTOR = "extractor"
    FUEL = "fuel"
    # A resource well is sized by three numbers and not by one: how many satellites
    # of each purity it opens. That is not the same concept written three ways --
    # it is three quantities, each with its own rate -- so it gets three fields
    # rather than being crammed into ``QUANTITY``.
    SATELLITES_IMPURE = "satellites_impure"
    SATELLITES_NORMAL = "satellites_normal"
    SATELLITES_PURE = "satellites_pure"
    # A line's tier. Not on a node at all: a double-click on the line itself.
    TRANSPORT = "transport"


# Which field stands for which purity of satellite, and back.
SATELLITE_FIELDS: Final[dict[Purity, Field]] = {
    Purity.IMPURE: Field.SATELLITES_IMPURE,
    Purity.NORMAL: Field.SATELLITES_NORMAL,
    Purity.PURE: Field.SATELLITES_PURE,
}
PURITY_BY_FIELD: Final[dict[Field, Purity]] = {
    field: purity for purity, field in SATELLITE_FIELDS.items()
}

def satellite_word(purity: Purity, count: int) -> str:
    """How many satellites of one purity read on the face of a node.

    Plural agreement is written out rather than computed: "impur" does not take an s
    the way "normal" takes "normaux", and a rule that gets one of the three wrong is
    worse than six sentences. English needs the six all the same -- it agrees in its
    own places -- so the pairs are translated whole rather than assembled.
    """
    match purity:
        case Purity.IMPURE:
            return _("impur") if count == 1 else _("impurs")
        case Purity.NORMAL:
            return _("normal") if count == 1 else _("normaux")
        case Purity.PURE:
            return _("pur") if count == 1 else _("purs")


@dataclass(frozen=True)
class Segment:
    """One run of the subtitle, and the field it stands for if it is a value.

    Separators are segments too, with no field. Keeping them in the list rather than
    joining around them is what lets :meth:`NodeItem.subtitle` stay the exact string
    it always was while the hit-testing knows where each value begins and ends.
    """

    text: str
    field: "Field | None" = None


@dataclass(frozen=True)
class SubtitleLayout:
    """The subtitle, measured once: where every run sits and how tall the block is.

    Measuring text is the single most expensive thing a node item does, and it
    used to happen on every layout **and** every paint -- for every node on the
    canvas, at every change anywhere in the factory. Nothing about it varies
    between two calls unless the text or the width does, which is exactly what
    :attr:`segments` and :attr:`width` are kept for: they are the cache key, and
    they are compared rather than trusted, so a node whose value changed without
    anybody remembering to say so still measures itself again.
    """

    segments: tuple[Segment, ...]
    width: float
    lines: tuple[tuple[Segment, ...], ...]
    # Advance of each segment, line by line, in the same shape as ``lines``.
    advances: tuple[tuple[float, ...], ...]

    @property
    def height(self) -> float:
        return len(self.lines) * DETAIL_HEIGHT


def measure_subtitle(
    segments: tuple[Segment, ...], available: float, metrics: QFontMetricsF
) -> SubtitleLayout:
    """Break the subtitle into lines that fit, never splitting a run.

    A deposit at 250 % reads "1 Foreuse Mk.3 — gisement normal — cadence 250 %",
    which is wider than the box; so is a buffer holding heavy oil residue. Those
    used to be silently clipped, taking the last value off the node with them.
    Wrapping keeps every value on screen -- and therefore reachable, since a value
    nobody can see is a value nobody can double-click.
    """
    lines: list[list[Segment]] = [[]]
    advances: list[list[float]] = [[]]
    used = 0.0
    for original in segments:
        segment = original
        width = metrics.horizontalAdvance(segment.text)
        if lines[-1] and used + width > available:
            lines.append([])
            advances.append([])
            used = 0.0
            # A separator at the head of a wrapped line reads as a stray dash.
            segment = _lstripped(segment)
            width = metrics.horizontalAdvance(segment.text)
        lines[-1].append(segment)
        advances[-1].append(width)
        used += width
    return SubtitleLayout(
        segments=segments,
        width=available,
        lines=tuple(tuple(line) for line in lines),
        advances=tuple(tuple(line) for line in advances),
    )


@dataclass(frozen=True)
class DeployedLayout:
    """How a bank of ``total`` machines is drawn as thumbnails.

    Pure arithmetic, kept out of the painting so it can be checked without a window.
    """

    total: float
    full: int  # whole thumbnails
    fraction: float  # width of a trailing partial one, 0 when there is none
    truncated: bool  # more machines than the ceiling: the count is written instead
    per_row: int = DEPLOYED_PER_ROW

    @property
    def drawn(self) -> int:
        return self.full + (1 if self.fraction > 0 else 0)

    @property
    def rows(self) -> int:
        """Rows of thumbnails, plus one for the count when the last row is full.

        A "... x43" with two pixels left to write it in is a "..." and nothing else,
        which is how the first version of this read on a bank of forty-three.
        """
        used = max(1, -(-self.drawn // self.per_row))
        if self.truncated and self.drawn % self.per_row == 0:
            used += 1
        return used

    def cell(self, index: int) -> tuple[int, int]:
        """``(column, row)`` of the ``index``-th thumbnail."""
        return index % self.per_row, index // self.per_row


def deployed_layout(total: float, ceiling: int, per_row: int = DEPLOYED_PER_ROW) -> DeployedLayout:
    """Whole thumbnails, a partial one, and whether the bank was cut short.

    A fractional machine is drawn as a fraction of a thumbnail rather than rounded
    up: 4.33 assemblers are four and a third, and that third is the whole reason
    decimals are allowed in the first place. Past the ceiling the picture stops
    saying anything a number would not say better, so the number is written.
    """
    whole = int(total)
    if whole >= ceiling:
        return DeployedLayout(
            total=total, full=ceiling, fraction=0.0, truncated=True, per_row=per_row
        )
    remainder = total - whole
    fraction = remainder if remainder >= DEPLOYED_MIN_FRACTION else 0.0
    return DeployedLayout(
        total=total, full=whole, fraction=fraction, truncated=False, per_row=per_row
    )


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
        # The box is redrawn far more often than it changes: a selection anywhere
        # on the canvas, a scrollbar, a line moving over it. Kept as a pixmap it
        # costs a blit instead of a full repaint, and ``update()`` is already
        # called wherever something really did change.
        self.setCacheMode(QGraphicsItem.CacheMode.DeviceCoordinateCache)
        self.setPos(QPointF(*node.position))
        # What a buffer holds, or what a splitter is placed on, once the graph can
        # tell. Set by the scene, which is the only place that knows the other lines.
        self.content_item: str | None = None
        # Lines already on the many side of a splitter or a merger, likewise: a node
        # cannot count its own neighbours.
        self.branch_count = 0
        # What is written on the branches that are not simply "any", as
        # ``(neighbour, setting)`` pairs already worded. Set by the scene, which
        # is the only place that knows what the neighbours are called.
        self.branch_settings: tuple[tuple[str, str], ...] = ()
        # Deployed rendering, as resolved by the scene: the global preference unless
        # this node overrides it. Purely a way of drawing; nothing reads it back.
        self.deployed = False
        self.deployed_ceiling = 12
        # Colours by item, handed down by the scene. ``None`` means nobody set one,
        # and the node is drawn exactly as it was before this existed -- which is
        # what a test that builds an item on its own gets, and rightly so.
        self.palette: ItemPalette | None = None
        self._inputs: tuple[str, ...] = ()
        self._outputs: tuple[str, ...] = ()
        self._height = HEADER_HEIGHT
        self._width = NODE_WIDTH
        # Row texts the width was last measured against, and the answer.
        self._width_key: tuple[tuple[str, str, str], ...] | None = None
        self._width_value = NODE_WIDTH
        # Measured once and re-used until the text or the width changes.
        self._layout: SubtitleLayout | None = None
        # Port circles, rebuilt when the rows they sit beside move.
        self._ports: list[Port] = []
        self._ports_top: float | None = None
        self.relayout()

    # ---------------------------------------------------------------- geometry

    def relayout(self) -> None:
        """Recompute the port lists, the width and the height. Call after a change."""
        self._inputs, self._outputs = self._port_items()
        rows = max(len(self._inputs), len(self._outputs))
        self.prepareGeometryChange()
        # Width before height: the subtitle wraps against it, so the height depends
        # on it and not the other way round.
        self._width = self._required_width()
        self._height = (
            HEADER_HEIGHT
            + self._subtitle_height()
            + self._deployed_band()
            + rows * ROW_HEIGHT
            + BOTTOM_PADDING
        )
        # The ports belong to the rows, and the rows have just moved.
        self._ports_top = None

    def width(self) -> float:
        """How wide this node is drawn. At least :data:`NODE_WIDTH`, never more than
        :data:`MAX_NODE_WIDTH`."""
        return self._width

    def _required_width(self) -> float:
        """Wide enough for the longest row to be read, within reason.

        The rate is never shortened -- it is the reason the row exists -- so it used
        to be the item's name that gave way, and a name elided to "Minerai de ..." no
        longer says which item the row is about. Writing the real and the nominal
        rate side by side made that common rather than rare.

        So the box grows instead, up to a ceiling. Past that, eliding resumes: a node
        that stretched to fit "Résidus de pétrole lourd 90 / 180 m³/min" twice over
        would be readable and would also be half the canvas.

        Measured once per set of texts, and cached against them. This is asked on
        every report -- a new rate can genuinely change the width -- and measuring
        five hundred nodes' worth of rows each time cost about seventy milliseconds
        an edit. Nearly all of those nodes have exactly the rates they had a moment
        ago; the key is what says so.
        """
        key = self._row_texts()
        if key == self._width_key:
            return self._width_value
        value = self._measure_width(key)
        self._width_key, self._width_value = key, value
        return value

    def _row_texts(self) -> tuple[tuple[str, str, str], ...]:
        """``(item, name, rate)`` for every row, inputs then outputs.

        Both the cache key and the thing being measured, so the strings are built
        once rather than once for each purpose.
        """
        rows: list[tuple[str, str, str]] = []
        for item_class in self._inputs:
            rows.append(
                (
                    item_class,
                    self._row_name(item_class),
                    self.port_rate(item_class, is_output=False),
                )
            )
        for item_class in self._outputs:
            rows.append(
                (item_class, self._row_name(item_class), self.port_rate(item_class, is_output=True))
            )
        return tuple(rows)

    def _row_name(self, item_class: str) -> str:
        if item_class == ANY_ITEM:
            return any_item_label()
        return self.game_data.item(item_class).name

    def _measure_width(self, rows: tuple[tuple[str, str, str], ...]) -> float:
        metrics = QFontMetricsF(self._row_font())
        inputs = rows[: len(self._inputs)]
        outputs = rows[len(self._inputs) :]
        needed = NODE_WIDTH
        for index in range(max(len(inputs), len(outputs))):
            left = inputs[index] if index < len(inputs) else None
            right = outputs[index] if index < len(outputs) else None
            if left is not None and right is not None:
                half = max(_cell_width(metrics, left), _cell_width(metrics, right))
                needed = max(needed, 2 * (half + ROW_MARGIN + ROW_GAP))
            else:
                only = left if left is not None else right
                assert only is not None
                needed = max(needed, _cell_width(metrics, only) + 2 * ROW_MARGIN)
        return min(needed, MAX_NODE_WIDTH)

    def _row_font(self) -> QFont:
        """The font the item rows are painted with, so measuring matches drawing."""
        font = QFont()
        font.setPointSizeF(max(font.pointSizeF() - 0.5, 6.0))
        return font

    def deployed_units(self) -> float | None:
        """The bank this node stands for, when it is being drawn machine by machine."""
        if not self.deployed:
            return None
        count = unit_count(self.node)
        return count if count is not None and count > 0 else None

    def deployed_plan(self) -> DeployedLayout | None:
        count = self.deployed_units()
        if count is None:
            return None
        # Per row from this node's own width: a wider box fits more thumbnails.
        per_row = int((self._width - 2 * ROW_MARGIN + DEPLOYED_GAP) / DEPLOYED_ROW_HEIGHT)
        return deployed_layout(count, self.deployed_ceiling, max(1, per_row))

    def _deployed_band(self) -> float:
        plan = self.deployed_plan()
        if plan is None:
            return 0.0
        return plan.rows * DEPLOYED_ROW_HEIGHT + DEPLOYED_BAND_PADDING

    def _rows_top(self) -> float:
        """Where the item rows start, thumbnails included when they are drawn."""
        return HEADER_HEIGHT + self._subtitle_height() + self._deployed_band()

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
            case GeneratorNode() as generator:
                # Fuel first, make-up water second: the game's own order, not the
                # alphabet. An output port only when the thing actually returns
                # something on a belt, which is the nuclear plant and nothing else.
                return (
                    self._generator_inputs(generator),
                    tuple(sorted(node_output_items(generator, self.game_data))),
                )
            case GeothermalNode():
                # Nothing in, nothing out: a hole in the ground and a cable.
                return ((), ())
            case StorageNode() | SplitterNode() | MergerNode():
                # One row each side for whatever it carries: how many *lines* hang
                # off that side is the port budget's business, not a row's.
                content = self.content_item
                return ((content or ANY_ITEM,), (content,) if content else ())
            case OutputNode() as output:
                return ((output.item_class,), ())
            case ResourceNode() | ResourceWellNode() | ExternalSourceNode() as source:
                return ((), (source.item_class,))
            case WaterExtractorNode() as pump:
                item = self.game_data.extractor(pump.extractor_class).item_class
                return ((), (item,) if item else ())

    def _generator_inputs(self, node: GeneratorNode) -> tuple[str, ...]:
        generator = self.game_data.generators.get(node.generator_class)
        fuel = generator.fuel(node.fuel_class) if generator else None
        if fuel is None:
            return ()
        return tuple(fuel.input_rates())

    def boundingRect(self) -> QRectF:
        margin = BORDER_WIDTH + PORT_RADIUS
        return QRectF(-margin, -margin, self._width + 2 * margin, self._height + 2 * margin)

    def ports(self) -> list[Port]:
        """Every port, inputs first, in the order they are drawn.

        Asked for twice per line on every redraw of the canvas, which is why the
        list is kept rather than built again each time. It is rebuilt when the
        rows have moved -- and :meth:`relayout` clears it outright, because a
        recipe can change the ports without changing where the first one sits.
        """
        top = self._rows_top() + ROW_HEIGHT / 2
        if self._ports_top == top:
            return self._ports
        found = [
            Port(item_class, False, QPointF(0.0, top + index * ROW_HEIGHT))
            for index, item_class in enumerate(self._inputs)
        ]
        found.extend(
            Port(item_class, True, QPointF(self._width, top + index * ROW_HEIGHT))
            for index, item_class in enumerate(self._outputs)
        )
        self._ports, self._ports_top = found, top
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
        side = self._width if is_output else 0.0
        return self.mapToScene(QPointF(side, self._height / 2))

    # ----------------------------------------------------------------- content

    def apply(self, solution: NodeSolution | None) -> None:
        """Take the solved figures, and re-measure only if they changed the shape.

        A rate is part of a row, and a row decides how wide the node has to be, so a
        new report can genuinely change the geometry -- "60/min" becoming
        "60 / 90/min" is wider. Laying out again is only worth it when that happens,
        which is why the width is compared rather than assumed: at five hundred
        nodes, an unconditional ``relayout`` on every report is exactly the cost Lot
        1 was spent removing.
        """
        self.solution = solution
        if self._required_width() != self._width:
            self.relayout()
        self.update()

    def title(self) -> str:
        return self.node.label or self._default_label()

    def _default_label(self) -> str:
        match self.node:
            case MachineNode() as machine:
                return self.game_data.recipe(machine.recipe_class).name
            case (
                ResourceNode() | ResourceWellNode() | ExternalSourceNode() | OutputNode()
            ) as endpoint:
                return self.game_data.item(endpoint.item_class).name
            case WaterExtractorNode() as pump:
                return self.game_data.building(pump.extractor_class).name
            case GeneratorNode() | GeothermalNode() as generator:
                return self.game_data.building(generator.generator_class).name
            case StorageNode() as storage:
                return self.game_data.building(storage.storage_class).name
            case SplitterNode() | MergerNode():
                return self._attachment_name()

    def _attachment_name(self) -> str:
        """What this splitter or merger is called, once its item says which it is.

        A solid one is a conveyor splitter and a fluid one a pipe junction, so the
        name cannot be settled before something is on the line -- and until then the
        generic word is the honest answer rather than a building picked at random.
        """
        building = attachment_building(self.node, self.content_item, self.game_data)
        if building is not None and building in self.game_data.buildings:
            return self.game_data.buildings[building].name
        return _("Répartiteur") if isinstance(self.node, SplitterNode) else _("Groupeur")

    def subtitle(self) -> str:
        """The line under the title, as one string. Assembled from the segments."""
        return "".join(segment.text for segment in self.subtitle_segments())

    def subtitle_segments(self) -> list[Segment]:
        """The subtitle cut into runs, each knowing whether it is an editable value.

        The line has always read as one sentence -- "1 Foreuse Mk.3 — gisement pur —
        cadence 250 %" -- and it still does, because :meth:`subtitle` simply joins
        these. What the cut adds is the ability to say *which* value the cursor is
        over, which is what turns a double-click into an edit of that field rather
        than of some field the node happens to have.
        """
        match self.node:
            case MachineNode() as machine:
                recipe = self.game_data.recipe(machine.recipe_class)
                building = self.game_data.building(recipe.building_class).name
                count = formatting.number(machine.machine_count)
                return [
                    Segment(building),
                    Segment(" — "),
                    Segment(_("{count} machine(s)").format(count=count), Field.QUANTITY),
                    *_clock_segments(machine.clock_speed),
                ]
            case ResourceNode() as deposit:
                extractor = self.game_data.building(deposit.extractor_class).name
                # The purity is on the face of the node because nothing else shows it
                # and everything depends on it: the same miner pulls 120, 240 or 480.
                return [
                    Segment(formatting.number(deposit.count), Field.QUANTITY),
                    Segment(" "),
                    Segment(extractor, Field.EXTRACTOR),
                    Segment(_(" — gisement ")),
                    Segment(purity_label(deposit.purity).lower(), Field.PURITY),
                    *_clock_segments(deposit.clock_speed),
                ]
            case ResourceWellNode() as well:
                return self._well_segments(well)
            case WaterExtractorNode() as pump:
                return [
                    Segment(
                        _("{count} unité(s)").format(count=formatting.number(pump.count)),
                        Field.QUANTITY,
                    ),
                    Segment(_(" — débit fixe")),
                    *_clock_segments(pump.clock_speed),
                ]
            case GeneratorNode() as generator:
                # The fuel is on the face of the node for the same reason the purity
                # of a deposit is: it changes every number, starting with how much
                # of it the thing swallows.
                power = self.game_data.generators[generator.generator_class].power_mw
                total = formatting.number(power * generator.count)
                return [
                    Segment(
                        _("{count} unité(s)").format(count=formatting.number(generator.count)),
                        Field.QUANTITY,
                    ),
                    Segment(" — "),
                    Segment(self.game_data.item(generator.fuel_class).name, Field.FUEL),
                    Segment(_(" — {total} MW produits").format(total=total)),
                ]
            case GeothermalNode() as geyser:
                # The purity is on the face of the node for the same reason a
                # deposit's is: nothing else shows it and everything depends on it.
                # The same building gives 100, 200 or 400 MW on average.
                produced = self.game_data.generators[geyser.generator_class].production_at(
                    geyser.purity
                )
                total = formatting.number(produced * geyser.count)
                return [
                    Segment(
                        _("{count} unité(s)").format(count=formatting.number(geyser.count)),
                        Field.QUANTITY,
                    ),
                    Segment(_(" — geyser ")),
                    Segment(purity_label(geyser.purity).lower(), Field.PURITY),
                    Segment(_(" — {total} MW en moyenne").format(total=total)),
                ]
            case ExternalSourceNode() as source:
                item = self.game_data.item(source.item_class)
                return [
                    Segment(_("apport externe ")),
                    Segment(formatting.rate(source.rate_per_minute, item), Field.QUANTITY),
                ]
            case StorageNode() as storage:
                return self._storage_segments(storage)
            case OutputNode() as output:
                return [Segment(_("rejet assumé") if output.is_sink else _("sortie de l'usine"))]
            case SplitterNode() | MergerNode():
                return self._attachment_segments()

    def _well_segments(self, node: ResourceWellNode) -> list[Segment]:
        """A well's face: the pressuriser, then the tally, one number per purity.

        The three counts are on the node for the same reason a deposit's purity is:
        nothing else shows them and every figure depends on them. They are written
        even when nought, because a well whose pure satellites read zero is telling
        you something, and a line that appears only when it is non-empty makes the
        reader wonder whether the field exists at all.
        """
        extractor = self.game_data.extractors.get(node.extractor_class)
        activator = extractor.activator_class if extractor else None
        head = (
            self.game_data.building(activator).name
            if activator and activator in self.game_data.buildings
            else _("Puits de ressource")
        )
        segments = [Segment(head), Segment(" — ")]
        for index, purity in enumerate(Purity):
            count = node.satellites.get(purity, 0)
            if index:
                segments.append(Segment(" · "))
            segments.append(Segment(str(count), SATELLITE_FIELDS[purity]))
            segments.append(Segment(f" {satellite_word(purity, count)}"))
        segments.extend(_clock_segments(node.clock_speed))
        return segments

    def subtitle_layout(self) -> SubtitleLayout:
        """The measured subtitle, from the cache when the text has not changed.

        The key is the segments themselves, not a flag somebody has to remember to
        clear: building them is a handful of catalogue lookups, and paying that to
        be certain the measurement still describes what is written is a far better
        trade than a cache that can quietly go stale.
        """
        segments = tuple(self.subtitle_segments())
        available = self._width - 16
        cached = self._layout
        if cached is not None and cached.width == available and cached.segments == segments:
            return cached
        fresh = measure_subtitle(segments, available, QFontMetricsF(self._detail_font()))
        self._layout = fresh
        return fresh

    def subtitle_lines(self) -> list[list[Segment]]:
        """The subtitle broken into lines that fit across the node.

        Segments are never split: a line break falls between two runs, so a value is
        never cut in half across two lines.
        """
        return [list(line) for line in self.subtitle_layout().lines]

    def field_at(self, local: QPointF) -> Field | None:
        """The editable value under a point, or ``None`` when there is none there.

        Only the subtitle band is live. A separator counts as part of the value it
        follows, so " machine(s)" and the space after a count are targets too --
        otherwise the quantity of a deposit would be a single digit to aim at.
        """
        band = self._subtitle_rect()
        if not band.contains(local):
            return None
        layout = self.subtitle_layout()
        row = int((local.y() - band.top()) / DETAIL_HEIGHT)
        if not 0 <= row < len(layout.lines):
            return None
        cursor = band.left()
        found: Field | None = None
        for segment, width in zip(layout.lines[row], layout.advances[row], strict=True):
            if segment.field is not None:
                found = segment.field
            if local.x() <= cursor + width:
                return found
            cursor += width
        return None

    def field_rect(self, field: Field) -> QRectF:
        """Exactly where a field's text is drawn, so an editor can sit over it.

        The true width and not a comfortable one: an editor needs a minimum size to
        type into, but that is the editor's business. Widening the rectangle here
        would make ``field_at`` disagree with itself -- the centre of a padded "1"
        lands on the extractor name beside it.
        """
        layout = self.subtitle_layout()
        band = self._subtitle_rect()
        for row, line in enumerate(layout.lines):
            cursor = band.left()
            for segment, width in zip(line, layout.advances[row], strict=True):
                if segment.field is field:
                    return QRectF(cursor, band.top() + row * DETAIL_HEIGHT, width, DETAIL_HEIGHT)
                cursor += width
        return band

    def _subtitle_height(self) -> float:
        return self.subtitle_layout().height

    def _subtitle_rect(self) -> QRectF:
        return QRectF(8, HEADER_HEIGHT - 16, self._width - 16, self._subtitle_height())

    def _detail_font(self) -> QFont:
        """The font the subtitle is painted with, so hit-testing measures the truth."""
        font = QFont()
        font.setPointSizeF(max(font.pointSizeF() - 1.0, 6.0))
        return font

    def clock_badge(self) -> str:
        """The clock, or an empty string at 100 %. Exposed so a test can read it."""
        clock = getattr(self.node, "clock_speed", 1.0)
        return "" if clock == 1.0 else formatting.percent(clock)

    def _storage_segments(self, storage: StorageNode) -> list[Segment]:
        """What the buffer holds, and whether that was decided or deduced.

        A buffer that silently keeps a content decided by a line the user has since
        removed would refuse the next line for no visible reason, so the state is
        spelled out: "(fixe)" means it was chosen and will not follow the lines.

        The stock is written even when it is zero. It used to be hidden then, which
        made it the one editable value with no place on the node to double-click.

        Cut into runs like every other subtitle, and deliberately short. It used to
        be one long run -- "tampon — Lingot d'acier (deduit des lignes), stock
        initial " -- and a run is never broken across two lines, so on a buffer with
        a long item name the whole thing overflowed the box and was clipped to
        "...), stc" with the stock left stranded on the line below. Saying "(deduit)"
        and "stock" instead of "(deduit des lignes)" and "stock initial" costs
        nothing a reader needed and makes the line fit; the cuts do the rest.
        """
        if self.content_item is None:
            return [Segment(_("tampon — contenu indéterminé"))]
        name = self.game_data.item(self.content_item).name
        origin = _("fixe") if storage.item_class else _("déduit")
        return [
            Segment(_("tampon — ")),
            Segment(name),
            Segment(f" ({origin})"),
            Segment(_(" — stock ")),
            Segment(formatting.number(storage.initial_content), Field.QUANTITY),
        ]

    def _attachment_segments(self) -> list[Segment]:
        """How many lines are on it and how many it can take, which is the whole point.

        A reader looking at a splitter wants to know whether there is a branch left.
        The count is read off the lines rather than stored, so it is right the moment
        one is drawn, and it is the number of **lines** and not of items: three
        outputs of the same thing is exactly the case this node exists for.
        """
        node = self.node
        assert isinstance(node, SplitterNode | MergerNode)
        wide = port_line_budget(node, is_output=isinstance(node, SplitterNode))
        used = self.branch_count
        if isinstance(node, SplitterNode):
            # The mode is an adjective, and the two languages do not put an
            # adjective on the same side of its noun: "répartiteur intelligent"
            # against "smart splitter". So the pair is one sentence, not two words.
            role = (
                _("répartiteur")
                if node.mode is SplitterMode.STANDARD
                else _("répartiteur {mode}").format(mode=splitter_mode_label(node.mode).lower())
            )
            ports = _(" — {used} sortie(s) sur {wide}")
        else:
            role = _("groupeur")
            ports = _(" — {used} entrée(s) sur {wide}")
        head: list[Segment] = [Segment(f"{role} — ")]
        if self.content_item is None:
            return [
                Segment(role + _(" — contenu indéterminé")),
                *self._branch_segments(),
            ]
        head.append(Segment(self.game_data.item(self.content_item).name))
        head.append(Segment(ports.format(used=used, wide=wide)))
        return head + self._branch_segments()

    def _branch_segments(self) -> list[Segment]:
        """The branches that say something, one run each so a line can break between.

        Only the ones that are not "any": three branches all reading "tout-venant"
        would bury the one that reads "surplus", and it is the surplus that moves
        the figures. A field that changes a rate has to be readable without a
        click, and this is that field.
        """
        return [
            Segment(f" — {neighbour} : {setting}") for neighbour, setting in self.branch_settings
        ]

    def icon(self) -> QIcon:
        match self.node:
            case SplitterNode() | MergerNode():
                building = attachment_building(self.node, self.content_item, self.game_data)
                if building is not None and building in self.game_data.buildings:
                    return self.icons.for_building(self.game_data.buildings[building])
                return self.icons.icon_for(self.node.kind.value, None, self._attachment_name())
            case MachineNode() as machine:
                recipe = self.game_data.recipe(machine.recipe_class)
                headline = recipe.products[0].item_class if recipe.products else None
                if headline is not None:
                    return self.icons.for_item(self.game_data.item(headline))
                return self.icons.for_building(self.game_data.building(recipe.building_class))
            case (
                ResourceNode() | ResourceWellNode() | ExternalSourceNode() | OutputNode()
            ) as endpoint:
                return self.icons.for_item(self.game_data.item(endpoint.item_class))
            case WaterExtractorNode() as pump:
                return self.icons.for_building(self.game_data.building(pump.extractor_class))
            case GeneratorNode() | GeothermalNode() as generator:
                return self.icons.for_building(self.game_data.building(generator.generator_class))
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
        body = QRectF(0, 0, self._width, self._height)

        border = QColor(theme.SELECTION) if self.isSelected() else state_colour(self.solution)
        painter.setBrush(QColor(self.background_colour()))
        painter.setPen(QPen(border, BORDER_WIDTH))
        painter.drawRoundedRect(body, CORNER_RADIUS, CORNER_RADIUS)

        self._paint_header(painter)
        self._paint_deployed(painter)
        self._paint_rows(painter)
        self._paint_ports(painter)

    def _paint_header(self, painter: QPainter) -> None:
        self.icon().paint(painter, QRectF(8, 6, ICON_SIDE, ICON_SIDE).toRect())

        title_font = QFont(painter.font())
        title_font.setBold(True)
        painter.setFont(title_font)
        painter.setPen(QColor(self.text_colour()))
        ratio_width = 52.0
        painter.drawText(
            QRectF(8 + ICON_SIDE + 6, 5, self._width - ICON_SIDE - ratio_width - 24, 20),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            self.title(),
        )

        if self.solution is not None:
            painter.setPen(state_colour(self.solution))
            painter.drawText(
                QRectF(self._width - ratio_width - 8, 5, ratio_width, 20),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                formatting.percent(self.solution.ratio),
            )

        # The very font ``field_at`` measures with, so what is hit is what is seen.
        painter.setFont(self._detail_font())
        painter.setPen(self.muted_text_colour())
        band = self._subtitle_rect()
        for row, line in enumerate(self.subtitle_layout().lines):
            painter.drawText(
                QRectF(band.left(), band.top() + row * DETAIL_HEIGHT, band.width(), DETAIL_HEIGHT),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                "".join(segment.text for segment in line),
            )

    def _paint_deployed(self, painter: QPainter) -> None:
        """One thumbnail per built machine, in a row under the subtitle.

        Nothing here changes a rate. It answers a question the numbers answer badly
        -- "how much of this am I actually putting down?" -- and it is drawn *in
        addition to* the subtitle, never instead of it: the purity, the clock and
        the fuel stay where they were.
        """
        plan = self.deployed_plan()
        if plan is None:
            return
        icon = self.building_icon()
        top = HEADER_HEIGHT + self._subtitle_height()
        step = DEPLOYED_SIDE + DEPLOYED_GAP

        def box(index: int) -> QRectF:
            column, row = plan.cell(index)
            return QRectF(
                ROW_MARGIN + column * step,
                top + row * DEPLOYED_ROW_HEIGHT,
                DEPLOYED_SIDE,
                DEPLOYED_SIDE,
            )

        for index in range(plan.full):
            icon.paint(painter, box(index).toRect())

        if plan.fraction > 0:
            # Clipped rather than scaled: a third of a machine is a third of a
            # machine's width, not a smaller machine.
            cell = box(plan.full)
            painter.save()
            painter.setClipRect(
                QRectF(cell.left(), cell.top(), DEPLOYED_SIDE * plan.fraction, DEPLOYED_SIDE)
            )
            icon.paint(painter, cell.toRect())
            painter.restore()

        if plan.truncated:
            cell = box(plan.drawn)
            font = QFont(painter.font())
            font.setPointSizeF(max(font.pointSizeF() - 1.0, 6.0))
            painter.setFont(font)
            painter.setPen(self.muted_text_colour())
            painter.drawText(
                QRectF(
                    cell.left(),
                    cell.top(),
                    self._width - cell.left() - ROW_MARGIN,
                    DEPLOYED_SIDE,
                ),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                f"... x{formatting.number(plan.total)}",
            )

    def building_icon(self) -> QIcon:
        """The building this node is a bank of, for the deployed thumbnails.

        Not :meth:`icon`, which shows what a node *makes*: four smelters drawn as
        four iron ingots would be exactly the wrong picture.
        """
        match self.node:
            case MachineNode() as machine:
                building = machine_building(machine, self.game_data)
            case ResourceNode() | WaterExtractorNode() as extractor:
                building = extractor.extractor_class
            case GeneratorNode() as generator:
                building = generator.generator_class
            case _:
                return self.icon()
        if building not in self.game_data.buildings:
            return self.icon()
        return self.icons.for_building(self.game_data.buildings[building])

    def _paint_rows(self, painter: QPainter) -> None:
        """One line per item, inputs down the left, outputs down the right.

        A row that has the side to itself takes the whole width; otherwise the two
        share it. The rate is never shortened -- it is the reason the row exists --
        so it is the item's name that gets elided when the space runs out.
        """
        # The very font ``_required_width`` measured with, so the box that was
        # sized to fit this row really does fit it.
        painter.setFont(self._row_font())
        top = self._rows_top()
        rows = max(len(self._inputs), len(self._outputs))
        for index in range(rows):
            shared = index < len(self._inputs) and index < len(self._outputs)
            # When both sides are used on the same row they split the width and leave a
            # gutter between them, so an input's rate never abuts an output's name.
            width = (
                self._width / 2 - ROW_MARGIN - ROW_GAP if shared else self._width - 2 * ROW_MARGIN
            )
            y = top + index * ROW_HEIGHT
            if index < len(self._inputs):
                self._paint_row(
                    painter,
                    QRectF(ROW_MARGIN, y, width, ROW_HEIGHT),
                    self._inputs[index],
                    is_output=False,
                )
            if index < len(self._outputs):
                left = self._width - ROW_MARGIN - width
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
            painter.drawText(cell, centre | Qt.AlignmentFlag.AlignLeft, any_item_label())
            return

        item = self.game_data.item(item_class)
        metrics = QFontMetricsF(painter.font())
        rate = self.port_rate(item_class, is_output=is_output)
        rate_width = metrics.horizontalAdvance(rate) if rate else 0.0
        name_cell = cell.adjusted(0, 0, -(rate_width + ROW_GAP if rate else 0.0), 0)
        painter.drawText(
            name_cell,
            centre | Qt.AlignmentFlag.AlignLeft,
            metrics.elidedText(
                item.name, Qt.TextElideMode.ElideRight, name_cell.width()
            ),
        )
        if rate:
            painter.drawText(cell, centre | Qt.AlignmentFlag.AlignRight, rate)

    def port_rate(self, item_class: str, *, is_output: bool) -> str:
        """What one port reads: the real rate, and the nominal one when they differ.

        Exposed rather than inlined into the painting so that a test can read exactly
        what is on the node without going through a screenshot.

        Empty for the wildcard port of a buffer whose content is not decided: there
        is no item, so there is no rate and nothing to look up in the catalogue.
        """
        if self.solution is None or item_class == ANY_ITEM:
            return ""
        item = self.game_data.item(item_class)
        flows = self.solution.outputs if is_output else self.solution.inputs
        nameplate = self.solution.nominal_outputs if is_output else self.solution.nominal_inputs
        return formatting.pair(flows.get(item_class, 0.0), nameplate.get(item_class), item)

    def _row_colour(self, item_class: str, *, is_output: bool) -> QColor:
        """Orange on the item that is actually holding the node back."""
        if item_class == ANY_ITEM or self.solution is None:
            return self.muted_text_colour()
        if item_class in self.solution.blocked_products and is_output:
            return QColor(theme.STATE_BLOCKED)
        if item_class in self.solution.starved_items and not is_output:
            return QColor(theme.STATE_STARVED)
        if item_class in self.solution.line_limited_items:
            return QColor(theme.EDGE_SATURATED)
        return QColor(self.text_colour())

    def _paint_ports(self, painter: QPainter) -> None:
        painter.setPen(QPen(QColor(theme.BACKGROUND), 1.0))
        for port in self.ports():
            painter.setBrush(QColor(self._port_colour(port.item_class)))
            painter.drawEllipse(port.centre, PORT_RADIUS, PORT_RADIUS)

    def _port_colour(self, item_class: str) -> str:
        """The colour of what travels through this port.

        Falls back to the transport's colour when no palette is in play, which is
        what a node built outside the application gets: a belt is pale, a pipe blue,
        exactly as before colours by item existed.
        """
        if item_class == ANY_ITEM:
            return theme.TEXT_MUTED
        item = self.game_data.item(item_class)
        if self.palette is not None:
            return self.palette.colour_for(item)
        return theme.PIPE_COLOUR if item.form.is_fluid else theme.BELT_COLOUR

    def background_colour(self) -> str:
        """What the box is filled with: the colour of the item this node is about.

        A node is about one thing -- what it makes, what it extracts, what it holds --
        and that is what a reader scans the canvas for. Its inputs are named on their
        own rows and keep their own colours on the ports.
        """
        if self.palette is None:
            return theme.SURFACE_RAISED
        subject = self.subject_item()
        if subject is None:
            return theme.SURFACE_RAISED
        return self.palette.colour_for(subject)

    def subject_item(self) -> Item | None:
        """The item this node is *about*, or ``None`` when it is about nothing yet."""
        match self.node:
            case MachineNode() as machine:
                recipe = self.game_data.recipe(machine.recipe_class)
                headline = recipe.products[0].item_class if recipe.products else None
            case GeneratorNode() as generator:
                headline = generator.fuel_class
            case WaterExtractorNode() as pump:
                headline = self.game_data.extractor(pump.extractor_class).item_class
            case StorageNode() | SplitterNode() | MergerNode():
                headline = self.content_item
            case _:
                headline = getattr(self.node, "item_class", None)
        return self.game_data.items.get(headline) if headline else None

    def text_colour(self) -> str:
        """Light or dark, whichever survives the background this node ended up with."""
        return item_colours.text_colour_on(self.background_colour())

    def muted_text_colour(self) -> QColor:
        """The same decision for the secondary text, kept a step quieter.

        Faded by transparency rather than by a second fixed colour, so that it stays
        the right side of whatever background the palette produced.
        """
        colour = QColor(self.text_colour())
        colour.setAlphaF(MUTED_TEXT_ALPHA)
        return colour

    def hoverMoveEvent(self, event: QGraphicsSceneHoverEvent) -> None:
        """Name the port under the cursor, so a fan of ten lines stays readable."""
        port = self.port_at(event.pos())
        if port is None:
            self.setToolTip(self._node_tooltip())
        elif port.item_class == ANY_ITEM:
            self.setToolTip(_("entrée : n'importe quel item, le tampon prend celui qui arrive"))
        else:
            item = self.game_data.item(port.item_class)
            pattern = (
                _("sortie : {item} ({unit})")
                if port.is_output
                else _("entrée : {item} ({unit})")
            )
            self.setToolTip(pattern.format(item=item.name, unit=formatting.unit(item)))
        super().hoverMoveEvent(event)

    def _node_tooltip(self) -> str:
        if self.solution is None:
            return self.title()
        lines = [f"{self.title()} — {formatting.percent(self.solution.ratio)}"]
        if self.solution.machine_count is not None:
            useful = formatting.number(self.solution.useful_machine_count or 0.0)
            lines.append(
                _("{useful} machine(s) utile(s) sur {built}, {integer} à bâtir").format(
                    useful=useful,
                    built=formatting.number(self.solution.machine_count),
                    integer=self.solution.integer_machine_count,
                )
            )
        if self.solution.power_mw:
            lines.append(
                _("{power} MW consommés").format(power=formatting.number(self.solution.power_mw))
            )
        if self.solution.power_produced_mw:
            lines.append(
                _("{power} MW produits").format(
                    power=formatting.number(self.solution.power_produced_mw)
                )
            )
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
        self._start = QPointF()
        self._end = QPointF()
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
        """Re-anchor the curve, unless it is already anchored exactly there.

        Every redraw of the canvas re-anchors every line, and on a factory where
        one node changed that is several hundred identical curves rebuilt and
        several hundred regions marked for repainting. Comparing two points first
        is cheaper than either.
        """
        if self._start == start and self._end == end:
            return
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
        name = transport.name if transport else self.solution.transport_class
        lines = [
            f"{item.name} — {formatting.rate(self.solution.rate_per_minute, item)}",
            f"{name} : {formatting.rate(self.solution.capacity_per_minute, item)} "
            f"({formatting.percent(self.solution.saturation)})",
        ]
        if self.solution.is_saturated:
            upgrade = self.game_data.smallest_transport_for(item.form, self.solution.demanded_rate)
            advice = (
                self.game_data.buildings[upgrade.class_name].name
                if upgrade is not None
                else _("aucun palier ne suffit, doublez la ligne")
            )
            lines.append(
                _("Saturée : {demanded} demandes, {blocked} refoulés — {advice}").format(
                    demanded=formatting.rate(self.solution.demanded_rate, item),
                    blocked=formatting.rate(self.solution.blocked_rate, item),
                    advice=advice,
                )
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
        font = QFont(painter.font())
        font.setPointSize(EDGE_LABEL_FONT_SIZE)
        painter.setFont(font)
        painter.setPen(self.pen().color())
        middle = self.path().pointAtPercent(0.5)
        painter.drawText(
            QRectF(middle.x() - EDGE_LABEL_WIDTH / 2, middle.y() - 14, EDGE_LABEL_WIDTH, 12),
            Qt.AlignmentFlag.AlignCenter,
            self.label(),
        )

    def label(self) -> str:
        """What the line carries, and what it would carry if it were big enough.

        The two differ only on a saturated line, which is exactly the line a reader
        is looking for: "60 / 120" says at once that half of it is being held back.
        """
        if self.solution is None:
            return ""
        item = self.game_data.item(self.solution.item_class)
        return formatting.pair(
            self.solution.rate_per_minute, self.solution.desired_rate_per_minute, item
        )


def _cell_width(metrics: QFontMetricsF, row: tuple[str, str, str], gap: float = ROW_GAP) -> float:
    """Room one row needs: its name, the gutter, and its rate.

    Takes the already-built texts rather than a node and an item class, so that the
    strings are made once and serve as both the measurement and the cache key.
    """
    _item_class, name, rate = row
    width = metrics.horizontalAdvance(name)
    if rate:
        width += gap + metrics.horizontalAdvance(rate)
    return width


def _lstripped(segment: Segment) -> Segment:
    """The same segment without its leading spaces, for the head of a wrapped line."""
    return Segment(segment.text.lstrip(), segment.field)


def _clock_segments(clock_speed: float) -> list[Segment]:
    """The clock, spelled out on the node whenever it is not 100 %.

    Never hidden and never abbreviated to an icon: a node running at 250 % produces
    two and a half times what its recipe says and costs three and a third times the
    power, and a reader who has to hover to find that out will not find it out.

    At 100 % there is nothing to show and therefore nothing to double-click, which
    is consistent rather than a gap: the context menu and the table still reach it.
    """
    if clock_speed == 1.0:
        return []
    return [Segment(_(" — cadence ")), Segment(formatting.percent(clock_speed), Field.CLOCK)]


def curve(start: QPointF, end: QPointF) -> QPainterPath:
    """A horizontal-tangent bezier, so lines leave a port sideways and read as flow."""
    path = QPainterPath(start)
    reach = max(abs(end.x() - start.x()) * CURVE_TENSION, 40.0)
    path.cubicTo(QPointF(start.x() + reach, start.y()), QPointF(end.x() - reach, end.y()), end)
    return path
