"""The palette: search the catalogue, then drag onto the canvas.

A flat searchable list, grouped by section, rather than a tree of tiers and
milestones: someone looking for "tole" wants the recipe, and having to remember which
tier unlocked it is not help. Search is accent-insensitive and matches every word
independently, so "alt plaque" finds "Alternative : plaque de fer moulée".

Three toggles sit above the list. Alternate recipes and event content are *display*
filters, never write filters -- the database holds everything, exactly as decided in
phase 2. The third is not a filter at all: it is the tier that new lines are created
with, which is what makes capacity being a constraint bearable to work with.

**A double-click opens the item's card, it does not place the node.** It used to do
the latter, and the change is deliberate: a palette is a catalogue before it is a
stock of parts, and "tell me about this" is the more common question. Placing is a
drag onto the canvas, the **Enter key** on the highlighted row, or the button every
recipe carries on its card.
"""

import logging
from collections.abc import Sequence
from typing import Any, Final

from PySide6.QtCore import (
    QAbstractListModel,
    QMimeData,
    QModelIndex,
    QObject,
    QPersistentModelIndex,
    QSize,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor, QDrag, QKeyEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QListView,
    QVBoxLayout,
    QWidget,
)

from satisplanner.core.models import GameData, ItemForm
from satisplanner.ui import theme
from satisplanner.ui.catalogue import (
    SECTION_LABELS,
    PaletteEntry,
    build_entries,
    fold,
    machine_choices,
    transport_choices,
)
from satisplanner.ui.icon_provider import IconProvider
from satisplanner.ui.item_colours import ItemPalette, text_colour_on

logger = logging.getLogger(__name__)

# Private drag payload: the entry's kind and class names, enough to rebuild it from
# the catalogue on the other side. Never a pickled object.
ENTRY_MIME_TYPE: Final = "application/x-satisplanner-entry"

ANY_MACHINE: Final = "*"
LIST_ICON_SIDE: Final = 20
# Long lists make the palette useless; searching is the way through them.
MAX_VISIBLE_ENTRIES: Final = 400

_ROLE_ENTRY: Final = int(Qt.ItemDataRole.UserRole)


def encode_entry(entry: PaletteEntry) -> bytes:
    """The drag payload: kind, main class, extractor class, separated by tabs."""
    return "\t".join((entry.kind.value, entry.class_name, entry.extractor_class or "")).encode(
        "utf-8"
    )


def decode_entry(payload: bytes, entries: Sequence[PaletteEntry]) -> PaletteEntry | None:
    """Rebuild an entry from a drag payload by looking it up in the catalogue.

    Matching against the real catalogue rather than trusting the payload means a
    malformed drop is ignored instead of creating a node that refers to nothing.
    """
    try:
        kind, class_name, extractor = payload.decode("utf-8").split("\t")
    except (UnicodeDecodeError, ValueError):
        logger.debug("charge utile de glisser-déposer illisible")
        return None
    for entry in entries:
        if (
            entry.kind.value == kind
            and entry.class_name == class_name
            and (entry.extractor_class or "") == extractor
        ):
            return entry
    return None


class PaletteModel(QAbstractListModel):
    """Rows of the palette: section headings and entries, icons fetched on demand.

    A model rather than a widget full of items, for one measured reason: building the
    icon of every row up front costs about nine milliseconds each, and seven hundred
    of those is nearly ten seconds of a frozen window. Qt only asks a model for the
    rows it is about to paint, so the cost follows the scrollbar instead of the
    catalogue.
    """

    def __init__(
        self,
        icons: IconProvider,
        game_data: GameData | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.icons = icons
        self.game_data = game_data
        # Colours by item, so that the place a reader goes looking for an item shows
        # the same colour they will see on the canvas once they have dropped it.
        self.palette = ItemPalette()
        self._rows: list[PaletteEntry | str] = []

    def set_item_palette(self, palette: ItemPalette) -> None:
        self.palette = palette
        if self._rows:
            top, bottom = self.index(0), self.index(len(self._rows) - 1)
            self.dataChanged.emit(top, bottom, [Qt.ItemDataRole.BackgroundRole])

    def entry_colour(self, entry: PaletteEntry) -> str | None:
        """The colour of what this entry is about, or ``None`` for a building."""
        if self.game_data is None:
            return None
        subject = entry.subject_item(self.game_data)
        item = self.game_data.items.get(subject) if subject else None
        return None if item is None else self.palette.colour_for(item)

    def set_rows(self, rows: Sequence[PaletteEntry | str]) -> None:
        self.beginResetModel()
        self._rows = list(rows)
        self.endResetModel()

    def rowCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(self._rows)

    def entry_at(self, index: QModelIndex | QPersistentModelIndex) -> PaletteEntry | None:
        if not index.isValid() or index.row() >= len(self._rows):
            return None
        row = self._rows[index.row()]
        return row if isinstance(row, PaletteEntry) else None

    def flags(self, index: QModelIndex | QPersistentModelIndex) -> Qt.ItemFlag:
        if self.entry_at(index) is None:
            return Qt.ItemFlag.NoItemFlags
        return (
            Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsDragEnabled
        )

    def data(
        self,
        index: QModelIndex | QPersistentModelIndex,
        role: int = int(Qt.ItemDataRole.DisplayRole),
    ) -> Any:
        if not index.isValid() or index.row() >= len(self._rows):
            return None
        row = self._rows[index.row()]
        if isinstance(row, str):
            if role == Qt.ItemDataRole.DisplayRole:
                return row.upper()
            if role == Qt.ItemDataRole.ForegroundRole:
                return QColor(theme.TEXT_MUTED)
            return None
        match role:
            case Qt.ItemDataRole.DisplayRole:
                return f"{row.label}{_alternate_marker(row)}\n{row.detail}"
            case Qt.ItemDataRole.DecorationRole:
                return self.icons.icon_for(row.icon_class, row.icon_file, row.label)
            case Qt.ItemDataRole.BackgroundRole:
                colour = self.entry_colour(row)
                return None if colour is None else QColor(colour)
            case Qt.ItemDataRole.ForegroundRole:
                colour = self.entry_colour(row)
                return None if colour is None else QColor(text_colour_on(colour))
            case Qt.ItemDataRole.ToolTipRole:
                return f"{row.label}\n{row.detail}\n{row.class_name}"
            case _ if role == _ROLE_ENTRY:
                return row
            case _:
                return None


class PaletteList(QListView):
    """The list itself, split out so it can start a drag."""

    # A PaletteEntry is a plain dataclass, so it travels as an opaque Python object.
    entryOpened = Signal(object)
    entryActivated = Signal(object)

    def __init__(self, model: PaletteModel, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setModel(model)
        self.palette_model = model
        self.setIconSize(QSize(LIST_ICON_SIDE, LIST_ICON_SIDE))
        self.setDragEnabled(True)
        self.setSelectionMode(QListView.SelectionMode.SingleSelection)
        self.setUniformItemSizes(True)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.doubleClicked.connect(self._activate)

    def _activate(self, index: QModelIndex) -> None:
        entry = self.palette_model.entry_at(index)
        if entry is not None:
            self.entryOpened.emit(entry)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        """Enter places the highlighted entry, the double-click having moved on.

        The gesture that used to place a node now opens its card, and this is where
        the fast path went: search, arrow down, Enter, without touching the mouse.
        """
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            entry = self.current_entry()
            if entry is not None:
                self.entryActivated.emit(entry)
                event.accept()
                return
        super().keyPressEvent(event)

    def current_entry(self) -> PaletteEntry | None:
        return self.palette_model.entry_at(self.currentIndex())

    def startDrag(self, supported_actions: Qt.DropAction) -> None:
        entry = self.current_entry()
        if entry is None:
            super().startDrag(supported_actions)
            return
        payload = QMimeData()
        payload.setData(ENTRY_MIME_TYPE, encode_entry(entry))
        drag = QDrag(self)
        drag.setMimeData(payload)
        icon = self.palette_model.icons.icon_for(entry.icon_class, entry.icon_file, entry.label)
        if not icon.isNull():
            drag.setPixmap(icon.pixmap(LIST_ICON_SIDE * 2, LIST_ICON_SIDE * 2))
        drag.exec(Qt.DropAction.CopyAction)


def _alternate_marker(entry: PaletteEntry) -> str:
    """The French labels already say so for most alternates ("Alternative : ..."),
    so the marker is only added where the game left it out."""
    return " (alternative)" if entry.is_alternate and "alternative" not in fold(entry.label) else ""


class PaletteWidget(QWidget):
    """Search box, filters, and the list of everything that can be placed."""

    # "Put this on the canvas": the Enter key, and the item card's own button. A
    # double-click no longer means this -- it opens the card instead.
    entryActivated = Signal(object)
    # "Tell me about this". A double-click, the way a catalogue behaves.
    entryOpened = Signal(object)
    defaultTransportsChanged = Signal(str, str)
    # Alternates shown, event content shown. Emitted so the preferences can follow the
    # toggles: a filter changed here and forgotten on the next run is a filter the
    # user has to set twice.
    filtersChanged = Signal(bool, bool)

    def __init__(
        self,
        game_data: GameData,
        icons: IconProvider,
        entries: Sequence[PaletteEntry] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.game_data = game_data
        self.icons = icons
        # Built once and shared with the canvas, which needs the same list to decode a
        # drop; building it twice would be pure waste.
        self.entries = list(entries) if entries is not None else build_entries(game_data)

        self.search = QLineEdit(self)
        self.search.setPlaceholderText("Rechercher une recette, un minerai, une sortie...")
        self.search.setClearButtonEnabled(True)

        self.machine = QComboBox(self)
        self.machine.addItem("Toutes les machines", ANY_MACHINE)
        for class_name, label in machine_choices(game_data):
            self.machine.addItem(label, class_name)

        self.alternates = QCheckBox("Inclure les recettes alternatives", self)
        self.alternates.setChecked(True)
        self.events = QCheckBox("Afficher les objets d'événement (FICSMAS)", self)
        self.events.setChecked(False)

        self.belt_tier = QComboBox(self)
        for class_name, label in transport_choices(game_data, ItemForm.SOLID):
            self.belt_tier.addItem(label, class_name)
        self.pipe_tier = QComboBox(self)
        for class_name, label in transport_choices(game_data, ItemForm.LIQUID):
            self.pipe_tier.addItem(label, class_name)

        self.model = PaletteModel(icons, game_data, self)
        self.list = PaletteList(self.model, self)
        self.count_label = QLabel(self)
        self.count_label.setStyleSheet(f"color: {theme.TEXT_MUTED};")

        self._build_layout()
        self._connect()
        self.refresh()

    def _build_layout(self) -> None:
        tiers = QFormLayout()
        tiers.setContentsMargins(0, 0, 0, 0)
        tiers.addRow("Convoyeur par défaut", self.belt_tier)
        tiers.addRow("Tuyauterie par défaut", self.pipe_tier)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.addWidget(self.search)
        layout.addWidget(self.machine)
        layout.addWidget(self.alternates)
        layout.addWidget(self.events)
        layout.addLayout(tiers)
        layout.addWidget(self.list, 1)
        layout.addWidget(self.count_label)

    def _connect(self) -> None:
        self.search.textChanged.connect(self.refresh)
        self.machine.currentIndexChanged.connect(self.refresh)
        self.alternates.toggled.connect(self.refresh)
        self.events.toggled.connect(self.refresh)
        self.alternates.toggled.connect(self._announce_filters)
        self.events.toggled.connect(self._announce_filters)
        self.list.entryOpened.connect(self.entryOpened)
        self.list.entryActivated.connect(self.entryActivated)
        self.belt_tier.currentIndexChanged.connect(self._announce_transports)
        self.pipe_tier.currentIndexChanged.connect(self._announce_transports)

    # ---------------------------------------------------------------- contents

    def default_transports(self) -> tuple[str, str]:
        """``(belt class, pipe class)`` new lines are created with."""
        return str(self.belt_tier.currentData()), str(self.pipe_tier.currentData())

    def _announce_transports(self) -> None:
        belt, pipe = self.default_transports()
        self.defaultTransportsChanged.emit(belt, pipe)

    def filters(self) -> tuple[bool, bool]:
        """``(alternates shown, event content shown)``."""
        return self.alternates.isChecked(), self.events.isChecked()

    def set_item_palette(self, palette: ItemPalette) -> None:
        """Colour the list the same way the canvas is coloured."""
        self.model.set_item_palette(palette)

    def _announce_filters(self) -> None:
        self.filtersChanged.emit(*self.filters())

    def apply_stored(
        self, belt: str, pipe: str, *, show_alternates: bool, show_events: bool
    ) -> None:
        """Adopt the settings kept from the last run, then refresh once.

        Signals are blocked while the widgets are set: each one would otherwise write
        straight back to the store it was just read from, and the list would be
        rebuilt four times before the window is even visible.
        """
        for widget, value in (
            (self.alternates, show_alternates),
            (self.events, show_events),
        ):
            blocked = widget.blockSignals(True)
            widget.setChecked(value)
            widget.blockSignals(blocked)
        for combo, class_name in ((self.belt_tier, belt), (self.pipe_tier, pipe)):
            index = combo.findData(class_name) if class_name else -1
            if index >= 0:
                blocked = combo.blockSignals(True)
                combo.setCurrentIndex(index)
                combo.blockSignals(blocked)
        self.refresh()
        self._announce_transports()

    def set_icons(self, icons: IconProvider) -> None:
        """Swap the icon source, after the user pointed the preferences elsewhere."""
        self.icons = icons
        self.model.icons = icons
        self.refresh()

    def visible_entries(self) -> list[PaletteEntry]:
        """Entries surviving the query and the toggles, in section order."""
        query = self.search.text().strip()
        machine = str(self.machine.currentData())
        kept: list[PaletteEntry] = []
        for entry in self.entries:
            if entry.is_event and not self.events.isChecked():
                continue
            if entry.is_alternate and not self.alternates.isChecked():
                continue
            if machine != ANY_MACHINE and entry.machine_class != machine:
                continue
            if query and not entry.matches(query):
                continue
            kept.append(entry)
        return kept

    def refresh(self) -> None:
        """Rebuild the row list, headings included. Icons are fetched as they show."""
        entries = self.visible_entries()
        rows: list[PaletteEntry | str] = []
        section = ""
        for entry in entries[:MAX_VISIBLE_ENTRIES]:
            heading = SECTION_LABELS[entry.kind]
            if heading != section:
                section = heading
                rows.append(heading)
            rows.append(entry)
        self.model.set_rows(rows)
        self.count_label.setText(_count_text(len(entries)))


def _count_text(total: int) -> str:
    if total > MAX_VISIBLE_ENTRIES:
        return f"{total} résultats, {MAX_VISIBLE_ENTRIES} affichés — affinez la recherche"
    return f"{total} résultat(s)"
