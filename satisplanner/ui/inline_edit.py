"""Editing a value where it is drawn, by double-clicking it.

The third door onto :mod:`satisplanner.ui.edits`, and it opens onto exactly the same
room: this module decides *what* widget to show and *where*, and hands the result
straight to the one function that already validates that field for the context menu
and for the table. There is no branch here that could disagree with them.

Two rules the widget itself enforces:

* **Escape cancels, Enter validates.** Nothing is written on the way past -- clicking
  elsewhere abandons the edit rather than half-applying it.
* **A refused value stays on screen.** When ``edits`` says no, the editor keeps the
  focus and keeps what was typed, so the user can see what they wrote and fix it.
  Clearing the box and closing would lose both the value and the reason.

Discrete fields -- purity, extractor, fuel, a line's tier -- get the same combo box
the table's delegate builds, from the same catalogue lists. Expecting someone to
spell "impure" into a cell was a trap; expecting it at a double-click would be the
same trap in a different place.
"""

import logging
from collections.abc import Callable

from PySide6.QtCore import QRect, Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QComboBox, QLineEdit, QWidget

from satisplanner.core import constants, formatting
from satisplanner.core.graph import GeneratorNode, ResourceNode, ResourceWellNode
from satisplanner.ui import edits
from satisplanner.ui.canvas_items import PURITY_BY_FIELD, Field
from satisplanner.ui.catalogue import (
    PURITY_LABELS,
    extractor_choices,
    fuel_choices,
    transport_choices,
)
from satisplanner.ui.document import FactoryDocument

logger = logging.getLogger(__name__)

# Fields whose value is one of a short, known list rather than a number.
DISCRETE_FIELDS = frozenset({Field.PURITY, Field.EXTRACTOR, Field.FUEL, Field.TRANSPORT})

# Nothing narrower is comfortable to type into, whatever the value measures.
MIN_EDITOR_WIDTH = 90


def choices_for(document: FactoryDocument, target: str, field: Field) -> list[tuple[str, str]]:
    """What a discrete field accepts, from the catalogue the table also reads."""
    if field is Field.TRANSPORT:
        edge = document.graph.edge(target)
        form = document.game_data.item(edge.item_class).form
        return transport_choices(document.game_data, form)
    node = document.graph.node(target)
    if field is Field.PURITY and isinstance(node, ResourceNode):
        return [(purity.value, label) for purity, label in PURITY_LABELS.items()]
    if field is Field.EXTRACTOR and isinstance(node, ResourceNode):
        return extractor_choices(document.game_data, node.item_class)
    if field is Field.FUEL and isinstance(node, GeneratorNode):
        return fuel_choices(document.game_data, node.generator_class)
    return []


def current_value(document: FactoryDocument, target: str, field: Field) -> str:
    """What the editor starts on: the value the node holds, written as it is shown."""
    if field is Field.TRANSPORT:
        return document.graph.edge(target).transport_class
    node = document.graph.node(target)
    match field:
        case Field.PURITY:
            return node.purity.value if isinstance(node, ResourceNode) else ""
        case Field.EXTRACTOR:
            return node.extractor_class if isinstance(node, ResourceNode) else ""
        case Field.FUEL:
            return node.fuel_class if isinstance(node, GeneratorNode) else ""
        case Field.CLOCK:
            # Typed in percent, stored as a fraction: nobody types 2,5 for 250 %.
            return formatting.number(getattr(node, "clock_speed", 1.0) * 100.0)
        case Field.SATELLITES_IMPURE | Field.SATELLITES_NORMAL | Field.SATELLITES_PURE:
            if not isinstance(node, ResourceWellNode):
                return ""
            return str(node.satellites.get(PURITY_BY_FIELD[field], 0))
        case _:
            quantity = edits.quantity_of(node)
            return "" if quantity is None else formatting.number(getattr(node, quantity.field))


def apply(document: FactoryDocument, target: str, field: Field, value: str) -> str | None:
    """Hand the value to ``edits``. ``None`` on success, a French reason otherwise.

    Every branch below is a call into the same module the menu and the table call.
    Nothing is validated here and nothing is pushed here.
    """
    match field:
        case Field.PURITY:
            return edits.set_purity(document, target, value)
        case Field.EXTRACTOR:
            return edits.set_extractor(document, target, value)
        case Field.FUEL:
            return edits.set_fuel(document, target, value)
        case Field.TRANSPORT:
            return edits.set_transport(document, target, value)
        case Field.CLOCK:
            return edits.set_clock_speed(document, target, _number(value) / 100.0)
        case Field.QUANTITY:
            return edits.set_quantity(document, target, _number(value))
        case Field.SATELLITES_IMPURE | Field.SATELLITES_NORMAL | Field.SATELLITES_PURE:
            return edits.set_satellites(
                document, target, PURITY_BY_FIELD[field], _number(value)
            )


def _number(text: str) -> float:
    """A typed value as a number, French comma included, or ``NaN``.

    ``NaN`` compares false against every bound, so "douze" is refused by the same
    range check as 400 %, and there is one refusal path rather than two.
    """
    try:
        return float(text.strip().replace(",", ".").replace(" ", ""))
    except ValueError:
        return float("nan")


class _LineEdit(QLineEdit):
    """A line edit that says when Escape was pressed rather than eating it."""

    cancelled = Signal()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.cancelled.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class InlineEditor:
    """The one editor a view shows at a time, and its lifecycle.

    Not a widget itself: it owns whichever widget the field calls for, parents it to
    the viewport so it floats over the canvas, and takes it away again.
    """

    def __init__(self, document: FactoryDocument, on_problem: Callable[[str], None]) -> None:
        self.document = document
        self._on_problem = on_problem
        self.widget: QWidget | None = None
        self.target: str | None = None
        self.field: Field | None = None

    # ------------------------------------------------------------------- open

    def open(self, parent: QWidget, target: str, field: Field, rect: QRect) -> bool:
        """Show an editor for ``field`` over ``rect``. False when there is nothing to edit."""
        self.close()
        if field in DISCRETE_FIELDS:
            widget = self._combo(parent, target, field)
        else:
            widget = self._line(parent, target, field)
        if widget is None:
            return False
        widget.setGeometry(
            QRect(rect.left(), rect.top(), max(rect.width(), MIN_EDITOR_WIDTH), rect.height())
        )
        widget.show()
        widget.setFocus(Qt.FocusReason.MouseFocusReason)
        self.widget, self.target, self.field = widget, target, field
        return True

    def _combo(self, parent: QWidget, target: str, field: Field) -> QWidget | None:
        choices = choices_for(self.document, target, field)
        if not choices:
            return None
        combo = QComboBox(parent)
        for value, label in choices:
            combo.addItem(label, value)
        position = combo.findData(current_value(self.document, target, field))
        combo.setCurrentIndex(max(position, 0))
        combo.activated.connect(lambda _index: self.commit())
        return combo

    def _line(self, parent: QWidget, target: str, field: Field) -> QWidget | None:
        text = current_value(self.document, target, field)
        if not text:
            return None
        editor = _LineEdit(text, parent)
        editor.selectAll()
        editor.setToolTip(_hint(field))
        editor.returnPressed.connect(self.commit)
        editor.cancelled.connect(self.cancel)
        return editor

    # ------------------------------------------------------------------ close

    def is_open(self) -> bool:
        return self.widget is not None

    def value(self) -> str:
        """What the editor currently holds, for a test that wants to read it back."""
        if isinstance(self.widget, QComboBox):
            return str(self.widget.currentData())
        if isinstance(self.widget, QLineEdit):
            return self.widget.text()
        return ""

    def commit(self) -> bool:
        """Validate through ``edits``. Keeps the editor open when it is refused."""
        if self.widget is None or self.target is None or self.field is None:
            return False
        problem = apply(self.document, self.target, self.field, self.value())
        if problem is not None:
            # Left open, with what was typed still in it: the user has to be able to
            # see what they wrote next to the reason it was not accepted.
            self._on_problem(problem)
            self.widget.setFocus(Qt.FocusReason.OtherFocusReason)
            return False
        self.close()
        return True

    def cancel(self) -> None:
        """Escape: nothing was written, so there is nothing to undo."""
        self.close()

    def close(self) -> None:
        widget, self.widget = self.widget, None
        self.target = self.field = None
        if widget is not None:
            widget.hide()
            widget.setParent(None)
            widget.deleteLater()


def _hint(field: Field) -> str:
    if field is Field.CLOCK:
        return (
            f"Cadence en pourcentage, de {formatting.percent(constants.MIN_CLOCK_SPEED)} "
            f"a {formatting.percent(constants.MAX_CLOCK_SPEED)}. Entrée valide, Echap annule."
        )
    return "Entrée valide, Echap annule."
