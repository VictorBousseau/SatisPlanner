"""The node table, kept in step with the canvas in both directions.

Selecting a row selects the node on the canvas and the other way round; editing a
cell pushes a command on the same undo stack as everything else. The guard against
the two views echoing each other is one flag, ``_syncing``, and it is the only piece
of state this widget owns.

The quantity column is deliberately **not** ``machine_count`` alone. A table of every
node with a machine count that is blank on two rows out of three would be a poor
table; instead the column edits whatever number that kind of node is sized by -- the
machine count of a machine, the number of extractors on a deposit, the rate of an
import, the initial stock of a buffer -- and the header says so.
"""

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Final, TypeGuard

from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QObject,
    QPersistentModelIndex,
    QSortFilterProxyModel,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHeaderView,
    QLineEdit,
    QStyledItemDelegate,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from satisplanner.core import formatting
from satisplanner.core.graph import (
    ExternalSourceNode,
    GraphError,
    MachineNode,
    Node,
    OutputNode,
    ResourceNode,
    StorageNode,
    WaterExtractorNode,
)
from satisplanner.core.results import LimitingFactor, NodeSolution
from satisplanner.ui import edits, theme
from satisplanner.ui.catalogue import PURITY_LABELS, extractor_choices
from satisplanner.ui.commands import SetNodeFieldCommand
from satisplanner.ui.document import FactoryDocument

logger = logging.getLogger(__name__)

KIND_LABELS: Final[dict[str, str]] = {
    "resource": "Gisement",
    "water_extractor": "Pompe",
    "external_source": "Entree",
    "machine": "Machine",
    "storage": "Tampon",
    "output": "Sortie",
}

LIMITING_LABELS: Final[dict[LimitingFactor, str]] = {
    LimitingFactor.NONE: "—",
    LimitingFactor.INPUTS: "deficit d'intrants",
    LimitingFactor.OUTPUTS: "contre-pression",
    LimitingFactor.LINE: "ligne trop petite",
    LimitingFactor.BLOCKED: "sous-produit bloque",
}

LIMITING_COLOURS: Final[dict[LimitingFactor, str]] = {
    LimitingFactor.NONE: theme.STATE_NOMINAL,
    LimitingFactor.INPUTS: theme.STATE_STARVED,
    LimitingFactor.OUTPUTS: theme.STATE_STARVED,
    LimitingFactor.LINE: theme.EDGE_SATURATED,
    LimitingFactor.BLOCKED: theme.STATE_BLOCKED,
}


@dataclass(frozen=True)
class Quantity:
    """The number a given kind of node is sized by."""

    field: str
    label: str
    minimum: float = 0.0


def quantity_of(node: Node) -> Quantity | None:
    """Which field the quantity column edits for this node, if any."""
    match node:
        case MachineNode():
            return Quantity("machine_count", "machine(s)")
        case ResourceNode() | WaterExtractorNode():
            # Strictly positive in the model: zero extractors is a deleted node.
            return Quantity("count", "extracteur(s)", minimum=1e-9)
        case ExternalSourceNode():
            return Quantity("rate_per_minute", "/min")
        case StorageNode():
            return Quantity("initial_content", "en stock")
        case OutputNode():
            return None


COLUMN_NODE: Final = 0
COLUMN_KIND: Final = 1
COLUMN_LABEL: Final = 2
COLUMN_QUANTITY: Final = 3
COLUMN_CLOCK: Final = 4
COLUMN_PURITY: Final = 5
COLUMN_EXTRACTOR: Final = 6
COLUMN_INPUTS: Final = 7
COLUMN_OUTPUTS: Final = 8
COLUMN_LIMITING: Final = 9
COLUMN_RATIO: Final = 10

HEADERS: Final[tuple[str, ...]] = (
    "Noeud",
    "Type",
    "Recette / contenu",
    "Quantite",
    "Cadence",
    "Purete",
    "Extracteur",
    "Entrees /min",
    "Sorties /min",
    "Facteur limitant",
    "Etat",
)

COLUMN_WIDTHS: Final[dict[int, int]] = {
    COLUMN_NODE: 95,
    COLUMN_KIND: 75,
    COLUMN_QUANTITY: 105,
    COLUMN_CLOCK: 80,
    COLUMN_PURITY: 80,
    COLUMN_EXTRACTOR: 120,
    COLUMN_INPUTS: 170,
    COLUMN_OUTPUTS: 170,
    COLUMN_LIMITING: 135,
    COLUMN_RATIO: 65,
}

# Node kinds that have a clock speed at all: a buffer or an exit has no throttle.
ClockedNode = MachineNode | ResourceNode | WaterExtractorNode


def has_clock(node: Node) -> TypeGuard[ClockedNode]:
    """True for the nodes that can be over- or underclocked, and narrows the type."""
    return isinstance(node, MachineNode | ResourceNode | WaterExtractorNode)


_ROLE_NODE_ID: Final = int(Qt.ItemDataRole.UserRole)
# The short list of values a cell accepts, as (stored value, French label). The
# delegate reads it to build a combo box; an empty list means "type it yourself".
_ROLE_CHOICES: Final = int(Qt.ItemDataRole.UserRole) + 1

Index = QModelIndex | QPersistentModelIndex


class NodeTableModel(QAbstractTableModel):
    """Every node of the factory, one row each, refreshed from the report."""

    def __init__(self, document: FactoryDocument, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.document = document
        self._rows: list[str] = []
        document.graphChanged.connect(self.refresh)
        document.reportChanged.connect(lambda _report: self.refresh())
        self.refresh()

    # -------------------------------------------------------------- structure

    def refresh(self) -> None:
        """Rebuild the row list. A factory has hundreds of nodes at most."""
        self.beginResetModel()
        self._rows = [node.id for node in self.document.graph.sorted_nodes()]
        self.endResetModel()

    def rowCount(self, parent: Index = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent: Index = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(HEADERS)

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = int(Qt.ItemDataRole.DisplayRole),
    ) -> Any:
        if role != Qt.ItemDataRole.DisplayRole or orientation is not Qt.Orientation.Horizontal:
            return None
        return HEADERS[section]

    def node_id_at(self, row: int) -> str | None:
        return self._rows[row] if 0 <= row < len(self._rows) else None

    def row_of(self, node_id: str) -> int | None:
        try:
            return self._rows.index(node_id)
        except ValueError:
            return None

    # ------------------------------------------------------------------ data

    def flags(self, index: Index) -> Qt.ItemFlag:
        base = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        node = self._node(index)
        if node is None:
            return base
        if index.column() == COLUMN_QUANTITY and quantity_of(node) is not None:
            return base | Qt.ItemFlag.ItemIsEditable
        if index.column() == COLUMN_CLOCK and has_clock(node):
            return base | Qt.ItemFlag.ItemIsEditable
        if index.column() in (COLUMN_PURITY, COLUMN_EXTRACTOR) and isinstance(node, ResourceNode):
            return base | Qt.ItemFlag.ItemIsEditable
        return base

    def data(self, index: Index, role: int = int(Qt.ItemDataRole.DisplayRole)) -> Any:
        node = self._node(index)
        if node is None:
            return None
        solution = self._solution(node.id)
        column = index.column()

        if role == Qt.ItemDataRole.EditRole and column == COLUMN_QUANTITY:
            quantity = quantity_of(node)
            return None if quantity is None else getattr(node, quantity.field)
        if role == Qt.ItemDataRole.EditRole and column == COLUMN_CLOCK:
            # Edited in percent, stored as a fraction: nobody types 2.5 for 250 %.
            return node.clock_speed * 100.0 if has_clock(node) else None
        if role == Qt.ItemDataRole.EditRole and column == COLUMN_PURITY:
            return node.purity.value if isinstance(node, ResourceNode) else None
        if role == Qt.ItemDataRole.EditRole and column == COLUMN_EXTRACTOR:
            return node.extractor_class if isinstance(node, ResourceNode) else None
        if role == _ROLE_CHOICES:
            return self._choices(node, column)
        if role == _ROLE_NODE_ID:
            return node.id
        if role == Qt.ItemDataRole.ForegroundRole:
            return self._foreground(column, solution)
        if role == Qt.ItemDataRole.TextAlignmentRole and column >= COLUMN_QUANTITY:
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if role == Qt.ItemDataRole.ToolTipRole:
            return self._tooltip(node, solution)
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        return self._display(node, solution, column)

    def _display(self, node: Node, solution: NodeSolution | None, column: int) -> str:
        match column:
            case _ if column == COLUMN_NODE:
                return node.id
            case _ if column == COLUMN_KIND:
                return KIND_LABELS[node.kind.value]
            case _ if column == COLUMN_LABEL:
                return node.label or (solution.label if solution else "")
            case _ if column == COLUMN_QUANTITY:
                return self._quantity_text(node)
            case _ if column == COLUMN_CLOCK:
                return formatting.percent(node.clock_speed) if has_clock(node) else "—"
            case _ if column == COLUMN_PURITY:
                return PURITY_LABELS[node.purity] if isinstance(node, ResourceNode) else "—"
            case _ if column == COLUMN_EXTRACTOR:
                return self._extractor_name(node)
            case _ if column == COLUMN_INPUTS:
                return self._flows(solution.inputs if solution else {})
            case _ if column == COLUMN_OUTPUTS:
                return self._flows(solution.outputs if solution else {})
            case _ if column == COLUMN_LIMITING:
                return LIMITING_LABELS[solution.limiting] if solution else "—"
            case _:
                return formatting.percent(solution.ratio) if solution else "—"

    def _quantity_text(self, node: Node) -> str:
        quantity = quantity_of(node)
        if quantity is None:
            return "—"
        value = formatting.number(getattr(node, quantity.field))
        return f"{value} {quantity.label}"

    def _extractor_name(self, node: Node) -> str:
        """The building working this node, French, or a dash when there is none."""
        if not isinstance(node, ResourceNode | WaterExtractorNode):
            return "—"
        building = self.document.game_data.buildings.get(node.extractor_class)
        return building.display_name_fr if building else node.extractor_class

    def _choices(self, node: Node, column: int) -> list[tuple[str, str]]:
        """What a cell of this column accepts, for the delegate's combo box."""
        if not isinstance(node, ResourceNode):
            return []
        if column == COLUMN_PURITY:
            return [(purity.value, label) for purity, label in PURITY_LABELS.items()]
        if column == COLUMN_EXTRACTOR:
            return extractor_choices(self.document.game_data, node.item_class)
        return []

    def _flows(self, values: dict[str, float]) -> str:
        if not values:
            return "—"
        return ", ".join(
            f"{self._item_name(item_class)} {formatting.number(rate)}"
            for item_class, rate in sorted(values.items())
        )

    def _item_name(self, class_name: str) -> str:
        item = self.document.game_data.items.get(class_name)
        return item.display_name_fr if item else class_name

    def _foreground(self, column: int, solution: NodeSolution | None) -> QColor | None:
        if solution is None or column not in (COLUMN_LIMITING, COLUMN_RATIO):
            return None
        return QColor(LIMITING_COLOURS[solution.limiting])

    def _tooltip(self, node: Node, solution: NodeSolution | None) -> str:
        if solution is None:
            return node.id
        lines = [f"{solution.label} — {formatting.percent(solution.ratio)}"]
        if solution.machine_count is not None and solution.useful_machine_count is not None:
            lines.append(
                f"{formatting.number(solution.useful_machine_count)} utile(s) sur "
                f"{formatting.number(solution.machine_count)}, "
                f"{solution.integer_machine_count} a batir"
            )
        if solution.clock_speed != 1.0:
            lines.append(
                f"cadence {formatting.percent(solution.clock_speed)}"
                + (f", {solution.power_shards} eclat(s)" if solution.power_shards else "")
            )
        for prefix, items in (
            ("manque", solution.starved_items),
            ("bloque par", solution.blocked_products),
        ):
            if items:
                lines.append(f"{prefix} : " + ", ".join(map(self._item_name, items)))
        return "\n".join(lines)

    # --------------------------------------------------------------- editing

    def setData(self, index: Index, value: Any, role: int = int(Qt.ItemDataRole.EditRole)) -> bool:
        if role != Qt.ItemDataRole.EditRole:
            return False
        node = self._node(index)
        if node is None:
            return False
        if index.column() == COLUMN_CLOCK:
            # Typed in percent, stored as a fraction: nobody types 2,5 for 250 %.
            return self._through(
                edits.set_clock_speed(self.document, node.id, _number(value) / 100.0)
            )
        if index.column() == COLUMN_PURITY:
            return self._through(edits.set_purity(self.document, node.id, str(value)))
        if index.column() == COLUMN_EXTRACTOR:
            return self._through(edits.set_extractor(self.document, node.id, str(value)))
        if index.column() != COLUMN_QUANTITY:
            return False
        quantity = quantity_of(node)
        if quantity is None:
            return False
        try:
            number = max(float(value), quantity.minimum)
        except (TypeError, ValueError):
            logger.debug("valeur non numerique refusee dans le tableau : %r", value)
            return False
        if number == getattr(node, quantity.field):
            return False
        self.document.undo_stack.push(
            SetNodeFieldCommand(
                self.document,
                node.id,
                quantity.field,
                number,
                f"{node.id} : {formatting.number(number)} {quantity.label}",
            )
        )
        return True

    def _through(self, problem: str | None) -> bool:
        """Report what the shared editor said. A refusal leaves the cell as it was."""
        if problem is not None:
            logger.debug("edition refusee dans le tableau : %s", problem)
            return False
        return True

    # --------------------------------------------------------------- lookups

    def _node(self, index: Index) -> Node | None:
        node_id = self.node_id_at(index.row())
        if node_id is None:
            return None
        try:
            return self.document.graph.node(node_id)
        except GraphError:
            # The row list can lag one signal behind a deletion; an empty cell is
            # better than an exception raised from inside a paint.
            return None

    def _solution(self, node_id: str) -> NodeSolution | None:
        report = self.document.report
        if report is None:
            return None
        try:
            return report.node(node_id)
        except KeyError:
            return None


def _number(value: Any) -> float:
    """A cell's text as a number, or a value the domain check will reject.

    ``NaN`` compares false against every bound, so a non-numeric entry is refused by
    the same range test as an out-of-range one, and there is one refusal path rather
    than two.
    """
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("nan")


class ChoiceDelegate(QStyledItemDelegate):
    """A combo box for the cells whose value is one of a short, known list.

    Purity and extractor are not numbers to be typed: they are three and four
    possibilities the catalogue already knows. A line edit expecting the reader to
    spell "impure" would be an invitation to get it wrong.
    """

    def createEditor(self, parent: QWidget, option: Any, index: Index) -> QWidget:
        choices = index.data(_ROLE_CHOICES)
        if not choices:
            return super().createEditor(parent, option, index)
        combo = QComboBox(parent)
        for value, label in choices:
            combo.addItem(label, value)
        return combo

    def setEditorData(self, editor: QWidget, index: Index) -> None:
        if isinstance(editor, QComboBox):
            position = editor.findData(index.data(Qt.ItemDataRole.EditRole))
            editor.setCurrentIndex(max(position, 0))
            return
        super().setEditorData(editor, index)

    def setModelData(self, editor: QWidget, model: Any, index: Index) -> None:
        if isinstance(editor, QComboBox):
            model.setData(index, editor.currentData(), Qt.ItemDataRole.EditRole)
            return
        super().setModelData(editor, model, index)


class NodeTablePanel(QWidget):
    """The table plus its filter box, and the two-way selection wiring."""

    selectionChangedTo = Signal(list)

    def __init__(self, document: FactoryDocument, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.document = document
        self._syncing = False

        self.model = NodeTableModel(document, self)
        self.proxy = QSortFilterProxyModel(self)
        self.proxy.setSourceModel(self.model)
        self.proxy.setFilterKeyColumn(-1)  # every column takes part in the search
        self.proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)

        self.filter = QLineEdit(self)
        self.filter.setPlaceholderText("Filtrer les noeuds...")
        self.filter.setClearButtonEnabled(True)
        self.filter.textChanged.connect(self.proxy.setFilterFixedString)

        self.view = QTableView(self)
        self.view.setModel(self.proxy)
        self.view.setSortingEnabled(True)
        self.view.setAlternatingRowColors(True)
        self.view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.view.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.view.verticalHeader().setVisible(False)
        header = self.view.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(False)
        header.setSectionResizeMode(COLUMN_LABEL, QHeaderView.ResizeMode.Stretch)
        # The dock is narrower than eight columns; the ones a user reads first are
        # given room and the rest scroll, rather than every column being unreadable.
        for column, width in COLUMN_WIDTHS.items():
            self.view.setColumnWidth(column, width)
        # Purity and extractor are chosen from a list, never typed.
        self.choice_delegate = ChoiceDelegate(self.view)
        for column in (COLUMN_PURITY, COLUMN_EXTRACTOR):
            self.view.setItemDelegateForColumn(column, self.choice_delegate)
        self.view.selectionModel().selectionChanged.connect(self._announce_selection)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.addWidget(self.filter)
        layout.addWidget(self.view, 1)

    # ------------------------------------------------------------- selection

    def selected_node_ids(self) -> list[str]:
        rows = {index.row() for index in self.view.selectionModel().selectedRows()}
        found = []
        for row in sorted(rows):
            node_id = self.model.node_id_at(self.proxy.mapToSource(self.proxy.index(row, 0)).row())
            if node_id is not None:
                found.append(node_id)
        return found

    def _announce_selection(self) -> None:
        if self._syncing:
            return
        self.selectionChangedTo.emit(self.selected_node_ids())

    def show_selection(self, node_ids: Sequence[str]) -> None:
        """Mirror the canvas selection, without echoing it straight back."""
        self._syncing = True
        try:
            selection = self.view.selectionModel()
            selection.clearSelection()
            wanted = set(node_ids)
            for row in range(self.proxy.rowCount()):
                source = self.proxy.mapToSource(self.proxy.index(row, 0))
                if self.model.node_id_at(source.row()) in wanted:
                    self.view.selectRow(row)
            if node_ids:
                self.view.scrollTo(self._first_visible(node_ids[0]))
        finally:
            self._syncing = False

    def _first_visible(self, node_id: str) -> QModelIndex:
        row = self.model.row_of(node_id)
        if row is None:
            return QModelIndex()
        return self.proxy.mapFromSource(self.model.index(row, 0))

    def connect_to(self, on_selection: Callable[[list[str]], None]) -> None:
        self.selectionChangedTo.connect(on_selection)
