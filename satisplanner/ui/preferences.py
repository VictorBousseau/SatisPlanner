"""What the application remembers between two runs, and the box for changing it.

Every persistent setting goes through :class:`Preferences`. ``QSettings.value``
returns ``object`` -- a boolean written on one run comes back as the string ``"true"``
on the next, and a one-element list comes back as a bare string -- so the conversions
live here once instead of being rediscovered at each call site.

The settings object is injected rather than built in place. That is not ceremony: a
test that writes to the real registry key leaves the developer's own recent-file list
scrambled, and one that reads it depends on what happened to be there.
"""

import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Final

from PySide6.QtCore import QSettings
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from satisplanner import paths
from satisplanner.core.families import FAMILY_LABELS, ItemFamily
from satisplanner.core.models import GameData, ItemForm
from satisplanner.ui import theme
from satisplanner.ui.catalogue import transport_choices
from satisplanner.ui.item_colours import (
    PALETTE_FILE_FILTER,
    PALETTE_FILE_SUFFIX,
    ItemPalette,
    text_colour_on,
)

logger = logging.getLogger(__name__)

ORGANISATION: Final = "SatisPlanner"
APPLICATION: Final = "SatisPlanner"

KEY_BELT: Final = "default_belt"
KEY_PIPE: Final = "default_pipe"
KEY_ICON_DIRECTORY: Final = "icon_directory"
KEY_MAX_RECENT: Final = "max_recent_files"
KEY_SHOW_ALTERNATES: Final = "show_alternates"
KEY_SHOW_EVENTS: Final = "show_events"
KEY_RECENT_FILES: Final = "recent_files"
KEY_DEPLOYED: Final = "deployed_rendering"
KEY_DEPLOYED_CEILING: Final = "deployed_ceiling"
KEY_ITEM_PALETTE: Final = "item_palette"

DEFAULT_MAX_RECENT: Final = 8
MAX_RECENT_LIMIT: Final = 30

# Thumbnails drawn before the drawing gives up and writes "... xN" instead. Twelve
# fits one row at the node's width and is about as many boxes as anyone counts at a
# glance; beyond that a picture of forty-three smelters says less than the number.
DEFAULT_DEPLOYED_CEILING: Final = 12
DEPLOYED_CEILING_RANGE: Final = (1, 60)


def application_settings() -> QSettings:
    """The real, persistent settings store."""
    return QSettings(ORGANISATION, APPLICATION)


class Preferences:
    """Typed access to the stored settings. Writes take effect immediately."""

    def __init__(self, settings: QSettings | None = None) -> None:
        self.settings = settings if settings is not None else application_settings()

    # -------------------------------------------------------------- primitives

    def _string(self, key: str, default: str = "") -> str:
        stored = self.settings.value(key, default)
        return default if stored is None else str(stored)

    def _integer(self, key: str, default: int) -> int:
        try:
            return int(str(self.settings.value(key, default)))
        except (TypeError, ValueError):
            return default

    def _boolean(self, key: str, default: bool) -> bool:
        """``QSettings`` hands booleans back as ``"true"`` / ``"false"`` strings.

        ``bool("false")`` is ``True``, which is the whole reason this method exists.
        """
        stored = self.settings.value(key, default)
        if isinstance(stored, bool):
            return stored
        return str(stored).strip().lower() in {"true", "1", "yes"}

    # ---------------------------------------------------------------- settings

    @property
    def default_belt(self) -> str:
        """Conveyor tier new lines are created with. Empty means "the lowest"."""
        return self._string(KEY_BELT)

    @default_belt.setter
    def default_belt(self, class_name: str) -> None:
        self.settings.setValue(KEY_BELT, class_name)

    @property
    def default_pipe(self) -> str:
        return self._string(KEY_PIPE)

    @default_pipe.setter
    def default_pipe(self, class_name: str) -> None:
        self.settings.setValue(KEY_PIPE, class_name)

    @property
    def icon_directory(self) -> Path | None:
        """Folder the user pointed at, or ``None`` when they never chose one."""
        stored = self._string(KEY_ICON_DIRECTORY)
        return Path(stored) if stored else None

    @icon_directory.setter
    def icon_directory(self, directory: Path | None) -> None:
        self.settings.setValue(KEY_ICON_DIRECTORY, "" if directory is None else str(directory))

    @property
    def effective_icon_directory(self) -> Path:
        """Where the user's own export is looked for, chosen or defaulted."""
        chosen = self.icon_directory
        return chosen if chosen is not None else paths.default_user_icon_directory()

    @property
    def max_recent_files(self) -> int:
        return max(0, min(MAX_RECENT_LIMIT, self._integer(KEY_MAX_RECENT, DEFAULT_MAX_RECENT)))

    @max_recent_files.setter
    def max_recent_files(self, count: int) -> None:
        self.settings.setValue(KEY_MAX_RECENT, max(0, min(MAX_RECENT_LIMIT, count)))
        # Shrinking the list is part of setting the limit; leaving eleven entries
        # behind a limit of ten would show a preference that visibly did nothing.
        self.set_recent_files(self.recent_files())

    @property
    def show_alternates(self) -> bool:
        return self._boolean(KEY_SHOW_ALTERNATES, True)

    @show_alternates.setter
    def show_alternates(self, shown: bool) -> None:
        self.settings.setValue(KEY_SHOW_ALTERNATES, shown)

    @property
    def show_events(self) -> bool:
        return self._boolean(KEY_SHOW_EVENTS, False)

    @show_events.setter
    def show_events(self, shown: bool) -> None:
        self.settings.setValue(KEY_SHOW_EVENTS, shown)

    @property
    def deployed_rendering(self) -> bool:
        """Draw one thumbnail per built machine on every node that has a count.

        Off by default, and deliberately so: it is a way of *looking* at a factory,
        not a better one. A node then carries a row of little boxes on top of --
        never instead of -- the text that says its purity, its clock and its fuel.
        """
        return self._boolean(KEY_DEPLOYED, False)

    @deployed_rendering.setter
    def deployed_rendering(self, shown: bool) -> None:
        self.settings.setValue(KEY_DEPLOYED, shown)

    @property
    def deployed_ceiling(self) -> int:
        low, high = DEPLOYED_CEILING_RANGE
        return max(low, min(high, self._integer(KEY_DEPLOYED_CEILING, DEFAULT_DEPLOYED_CEILING)))

    @deployed_ceiling.setter
    def deployed_ceiling(self, count: int) -> None:
        low, high = DEPLOYED_CEILING_RANGE
        self.settings.setValue(KEY_DEPLOYED_CEILING, max(low, min(high, count)))

    @property
    def item_palette(self) -> ItemPalette:
        """The colours items are drawn in.

        Kept in the settings and **not** in the document, for two reasons that both
        matter. A palette in the document would cost a schema version, exactly as
        ``show_deployed`` did in V1.1; and it would impose its author's taste on
        whoever opened the file, which is the opposite of what a shared factory is
        for. Anyone who does want to hand their palette on can export it, which is a
        deliberate act rather than a side effect of sending a factory.
        """
        return ItemPalette.from_json(self._string(KEY_ITEM_PALETTE, "{}"))

    @item_palette.setter
    def item_palette(self, palette: ItemPalette) -> None:
        self.settings.setValue(KEY_ITEM_PALETTE, palette.to_json())

    # ----------------------------------------------------------- recent files

    def recent_files(self) -> list[Path]:
        """Most recent first. A one-element list comes back as a bare string."""
        stored = self.settings.value(KEY_RECENT_FILES, [])
        if isinstance(stored, str):
            entries: list[object] = [stored]
        elif isinstance(stored, list):
            entries = list(stored)
        else:
            entries = []
        return [Path(str(entry)) for entry in entries if entry][: self.max_recent_files]

    def set_recent_files(self, files: Sequence[Path]) -> None:
        self.settings.setValue(
            KEY_RECENT_FILES, [str(path) for path in files[: self.max_recent_files]]
        )

    def remember_recent(self, path: Path) -> None:
        """Push a file to the front, without duplicates and within the limit."""
        newest = path.resolve()
        kept = [newest]
        kept.extend(entry for entry in self.recent_files() if entry.resolve() != newest)
        self.set_recent_files(kept)

    def forget_recent(self) -> None:
        self.settings.setValue(KEY_RECENT_FILES, [])


class FamilyColourRow(QWidget):
    """One family: a swatch that opens a colour picker, and a way back to the default.

    The swatch is the button. A separate "choose..." next to a preview would be two
    controls for one decision, and the preview is what the user is aiming at anyway.
    """

    def __init__(self, family: ItemFamily, palette: ItemPalette, parent: QWidget | None = None):
        super().__init__(parent)
        self.family = family
        self.colours = palette

        self.swatch = QPushButton(self)
        self.swatch.setFixedWidth(120)
        self.swatch.clicked.connect(self._choose)
        self.reset = QPushButton("Défaut", self)
        self.reset.setFixedWidth(70)
        self.reset.clicked.connect(self._reset)

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.swatch)
        row.addWidget(self.reset)
        row.addStretch(1)
        self._refresh()

    def _refresh(self) -> None:
        colour = self.colours.family_colour(self.family)
        self.swatch.setText(colour)
        # The button shows the colour *and* the text that will be written on it, so
        # an unreadable choice is unreadable in the box before it is unreadable on
        # the canvas.
        self.swatch.setStyleSheet(
            f"background-color: {colour}; color: {text_colour_on(colour)};"
            f" border: 1px solid {theme.SURFACE_RAISED}; padding: 4px;"
        )
        self.reset.setEnabled(self.family in self.colours.families)

    def _choose(self) -> None:
        chosen = QColorDialog.getColor(
            QColor(self.colours.family_colour(self.family)), self, FAMILY_LABELS[self.family]
        )
        if chosen.isValid():
            self.colours.set_family(self.family, chosen.name())
            self._refresh()

    def _reset(self) -> None:
        self.colours.reset_family(self.family)
        self._refresh()


class PreferencesDialog(QDialog):
    """The box. It writes nothing until it is accepted."""

    def __init__(
        self,
        preferences: Preferences,
        game_data: GameData,
        indexed_icons: int = 0,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.preferences = preferences
        self.setWindowTitle("Préférences")
        self.setMinimumWidth(520)

        self.belt = QComboBox(self)
        for class_name, label in transport_choices(game_data, ItemForm.SOLID):
            self.belt.addItem(label, class_name)
        self.pipe = QComboBox(self)
        for class_name, label in transport_choices(game_data, ItemForm.LIQUID):
            self.pipe.addItem(label, class_name)

        self.icon_directory = QLineEdit(self)
        self.icon_directory.setPlaceholderText(str(paths.default_user_icon_directory()))
        browse = QPushButton("Parcourir...", self)
        browse.clicked.connect(self._browse)
        icon_row = QHBoxLayout()
        icon_row.setContentsMargins(0, 0, 0, 0)
        icon_row.addWidget(self.icon_directory, 1)
        icon_row.addWidget(browse)

        self.max_recent = QSpinBox(self)
        self.max_recent.setRange(0, MAX_RECENT_LIMIT)

        self.alternates = QCheckBox("Afficher les recettes alternatives", self)
        self.events = QCheckBox("Afficher les objets d'événement (FICSMAS)", self)
        self.deployed = QCheckBox("Dessiner les machines une par une sur les noeuds", self)
        self.deployed_ceiling = QSpinBox(self)
        self.deployed_ceiling.setRange(*DEPLOYED_CEILING_RANGE)

        # Edited on a copy: the box writes nothing until it is accepted, and a
        # cancelled colour picker must leave the settings exactly as they were.
        self.colours = preferences.item_palette
        self.colour_rows = {
            family: FamilyColourRow(family, self.colours, self) for family in ItemFamily
        }
        self.export_palette = QPushButton("Exporter...", self)
        self.export_palette.clicked.connect(self._export_palette)
        self.import_palette = QPushButton("Importer...", self)
        self.import_palette.clicked.connect(self._import_palette)
        self.reset_palette = QPushButton("Tout remettre par défaut", self)
        self.reset_palette.clicked.connect(self._reset_palette)
        palette_buttons = QHBoxLayout()
        palette_buttons.setContentsMargins(0, 0, 0, 0)
        palette_buttons.addWidget(self.export_palette)
        palette_buttons.addWidget(self.import_palette)
        palette_buttons.addWidget(self.reset_palette)
        palette_buttons.addStretch(1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        form = QFormLayout()
        form.addRow("Convoyeur par défaut", self.belt)
        form.addRow("Tuyauterie par défaut", self.pipe)
        form.addRow("Dossier d'icônes", icon_row)
        form.addRow("Fichiers récents conserves", self.max_recent)
        form.addRow(self.alternates)
        form.addRow(self.events)
        form.addRow(self.deployed)
        form.addRow("Vignettes avant « ... xN »", self.deployed_ceiling)
        for family, row in self.colour_rows.items():
            form.addRow(FAMILY_LABELS[family], row)
        form.addRow("Palette", palette_buttons)

        hint = QLabel(
            f"{indexed_icons} fichier(s) d'icône indexé(s). Les classes sans fichier sont "
            "dessinées par l'application ; un dossier d'icônes est facultatif.\n"
            "Le changement de dossier est pris en compte immédiatement.",
            self,
        )
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {theme.TEXT_MUTED};")

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(hint)
        layout.addWidget(buttons)

        self._load()

    # ------------------------------------------------------------------ values

    def _load(self) -> None:
        _select(self.belt, self.preferences.default_belt)
        _select(self.pipe, self.preferences.default_pipe)
        chosen = self.preferences.icon_directory
        self.icon_directory.setText("" if chosen is None else str(chosen))
        self.max_recent.setValue(self.preferences.max_recent_files)
        self.alternates.setChecked(self.preferences.show_alternates)
        self.events.setChecked(self.preferences.show_events)
        self.deployed.setChecked(self.preferences.deployed_rendering)
        self.deployed_ceiling.setValue(self.preferences.deployed_ceiling)

    def _browse(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self, "Dossier d'icônes", self.icon_directory.text()
        )
        if chosen:
            self.icon_directory.setText(chosen)

    def chosen_icon_directory(self) -> Path | None:
        text = self.icon_directory.text().strip()
        return Path(text) if text else None

    # ---------------------------------------------------------------- palette

    def _refresh_colours(self) -> None:
        for row in self.colour_rows.values():
            row._refresh()

    def _reset_palette(self) -> None:
        self.colours.reset()
        self._refresh_colours()

    def export_palette_to(self, path: Path) -> bool:
        """Write the palette out. Separate from the button so a test can call it."""
        try:
            path.write_text(self.colours.to_json(), encoding="utf-8")
        except OSError as exc:
            QMessageBox.warning(self, "Export impossible", f"{path.name} : {exc.strerror}")
            return False
        return True

    def import_palette_from(self, path: Path) -> bool:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            QMessageBox.warning(self, "Import impossible", f"{path.name} : {exc.strerror}")
            return False
        imported = ItemPalette.from_json(text)
        # Replaced rather than merged. Importing a palette is asking to see somebody
        # else's factory the way they see it, and half of theirs over half of yours
        # is a third palette nobody chose.
        self.colours.families = imported.families
        self.colours.items = imported.items
        for row in self.colour_rows.values():
            row.colours = self.colours
        self._refresh_colours()
        return True

    def _export_palette(self) -> None:
        chosen, _ = QFileDialog.getSaveFileName(
            self, "Exporter la palette", f"palette{PALETTE_FILE_SUFFIX}", PALETTE_FILE_FILTER
        )
        if chosen:
            self.export_palette_to(Path(chosen).with_suffix(PALETTE_FILE_SUFFIX))

    def _import_palette(self) -> None:
        chosen, _ = QFileDialog.getOpenFileName(
            self, "Importer une palette", "", PALETTE_FILE_FILTER
        )
        if chosen:
            self.import_palette_from(Path(chosen))

    def accept(self) -> None:
        """Write everything the box holds, then close."""
        self.preferences.default_belt = str(self.belt.currentData())
        self.preferences.default_pipe = str(self.pipe.currentData())
        self.preferences.icon_directory = self.chosen_icon_directory()
        self.preferences.max_recent_files = self.max_recent.value()
        self.preferences.show_alternates = self.alternates.isChecked()
        self.preferences.show_events = self.events.isChecked()
        self.preferences.deployed_rendering = self.deployed.isChecked()
        self.preferences.deployed_ceiling = self.deployed_ceiling.value()
        self.preferences.item_palette = self.colours
        logger.debug("préférences enregistrées")
        super().accept()


def _select(combo: QComboBox, class_name: str) -> None:
    """Select a stored class, leaving the first entry when it is no longer offered."""
    if not class_name:
        return
    index = combo.findData(class_name)
    if index >= 0:
        combo.setCurrentIndex(index)
