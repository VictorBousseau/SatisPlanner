"""The palette: search the catalogue, then drag onto the canvas.

A flat searchable list, grouped by section, rather than a tree of tiers and
milestones: someone looking for "tole" wants the recipe, and having to remember which
tier unlocked it is not help. Search is accent-insensitive and matches every word
independently, so "alt plaque" finds "Alternative : plaque de fer moulée".

Three toggles sit above the list. Alternate recipes and event content are *display*
filters, never write filters -- the database holds everything, exactly as decided in
phase 2. The third is not a filter at all: it is the tier that new lines are created
with, which is what makes capacity being a constraint bearable to work with.
"""

import logging
from collections.abc import Sequence
from typing import Final

from PySide6.QtCore import QMimeData, QPoint, QSize, Qt, Signal
from PySide6.QtGui import QDrag, QMouseEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
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
        logger.debug("charge utile de glisser-deposer illisible")
        return None
    for entry in entries:
        if (
            entry.kind.value == kind
            and entry.class_name == class_name
            and (entry.extractor_class or "") == extractor
        ):
            return entry
    return None


class PaletteList(QListWidget):
    """The list itself, split out so it can start a drag."""

    # A PaletteEntry is a plain dataclass, so it travels as an opaque Python object.
    entryActivated = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setIconSize(QSize(LIST_ICON_SIDE, LIST_ICON_SIDE))
        self.setDragEnabled(True)
        self.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.setUniformItemSizes(False)
        self.itemDoubleClicked.connect(self._activate)
        self._press_at = QPoint()

    def _activate(self, item: QListWidgetItem) -> None:
        entry = item.data(_ROLE_ENTRY)
        if isinstance(entry, PaletteEntry):
            self.entryActivated.emit(entry)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        self._press_at = event.pos()
        super().mousePressEvent(event)

    def startDrag(self, supported_actions: Qt.DropAction) -> None:
        item = self.currentItem()
        entry = item.data(_ROLE_ENTRY) if item is not None else None
        if not isinstance(entry, PaletteEntry):
            super().startDrag(supported_actions)
            return
        payload = QMimeData()
        payload.setData(ENTRY_MIME_TYPE, encode_entry(entry))
        drag = QDrag(self)
        drag.setMimeData(payload)
        icon = item.icon()
        if not icon.isNull():
            drag.setPixmap(icon.pixmap(LIST_ICON_SIDE * 2, LIST_ICON_SIDE * 2))
        drag.exec(Qt.DropAction.CopyAction)


class PaletteWidget(QWidget):
    """Search box, filters, and the list of everything that can be placed."""

    entryActivated = Signal(object)
    defaultTransportsChanged = Signal(str, str)

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
        self.events = QCheckBox("Afficher les objets d'evenement (FICSMAS)", self)
        self.events.setChecked(False)

        self.belt_tier = QComboBox(self)
        for class_name, label in transport_choices(game_data, ItemForm.SOLID):
            self.belt_tier.addItem(label, class_name)
        self.pipe_tier = QComboBox(self)
        for class_name, label in transport_choices(game_data, ItemForm.LIQUID):
            self.pipe_tier.addItem(label, class_name)

        self.list = PaletteList(self)
        self.count_label = QLabel(self)
        self.count_label.setStyleSheet(f"color: {theme.TEXT_MUTED};")

        self._build_layout()
        self._connect()
        self.refresh()

    def _build_layout(self) -> None:
        tiers = QFormLayout()
        tiers.setContentsMargins(0, 0, 0, 0)
        tiers.addRow("Convoyeur par defaut", self.belt_tier)
        tiers.addRow("Tuyauterie par defaut", self.pipe_tier)

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
        """Rebuild the list. Cheap: a few hundred rows at worst."""
        entries = self.visible_entries()
        self.list.clear()
        section = ""
        for entry in entries[:MAX_VISIBLE_ENTRIES]:
            heading = SECTION_LABELS[entry.kind]
            if heading != section:
                section = heading
                self.list.addItem(_heading_item(heading))
            self.list.addItem(self._entry_item(entry))
        self.count_label.setText(_count_text(len(entries)))

    def _entry_item(self, entry: PaletteEntry) -> QListWidgetItem:
        # The French labels already say so for most alternates ("Alternative : ..."),
        # so the marker is only added where the game left it out.
        marked = entry.is_alternate and "alternative" not in fold(entry.label)
        suffix = " (alternative)" if marked else ""
        item = QListWidgetItem(f"{entry.label}{suffix}\n{entry.detail}")
        item.setIcon(self.icons.icon_for(entry.icon_class, entry.icon_file, entry.label))
        item.setData(_ROLE_ENTRY, entry)
        item.setToolTip(f"{entry.label}\n{entry.detail}\n{entry.class_name}")
        return item


def _heading_item(text: str) -> QListWidgetItem:
    item = QListWidgetItem(text.upper())
    item.setFlags(Qt.ItemFlag.NoItemFlags)
    item.setForeground(Qt.GlobalColor.gray)
    return item


def _count_text(total: int) -> str:
    if total > MAX_VISIBLE_ENTRIES:
        return f"{total} resultats, {MAX_VISIBLE_ENTRIES} affiches — affinez la recherche"
    return f"{total} resultat(s)"
