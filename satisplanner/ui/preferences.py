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
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from satisplanner import paths
from satisplanner.core.models import GameData, ItemForm
from satisplanner.ui import theme
from satisplanner.ui.catalogue import transport_choices

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

DEFAULT_MAX_RECENT: Final = 8
MAX_RECENT_LIMIT: Final = 30


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
        self.setWindowTitle("Preferences")
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
        self.events = QCheckBox("Afficher les objets d'evenement (FICSMAS)", self)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        form = QFormLayout()
        form.addRow("Convoyeur par defaut", self.belt)
        form.addRow("Tuyauterie par defaut", self.pipe)
        form.addRow("Dossier d'icones", icon_row)
        form.addRow("Fichiers recents conserves", self.max_recent)
        form.addRow(self.alternates)
        form.addRow(self.events)

        hint = QLabel(
            f"{indexed_icons} fichier(s) d'icone indexe(s). Les classes sans fichier sont "
            "dessinees par l'application ; un dossier d'icones est facultatif.\n"
            "Le changement de dossier est pris en compte immediatement.",
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

    def _browse(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self, "Dossier d'icones", self.icon_directory.text()
        )
        if chosen:
            self.icon_directory.setText(chosen)

    def chosen_icon_directory(self) -> Path | None:
        text = self.icon_directory.text().strip()
        return Path(text) if text else None

    def accept(self) -> None:
        """Write everything the box holds, then close."""
        self.preferences.default_belt = str(self.belt.currentData())
        self.preferences.default_pipe = str(self.pipe.currentData())
        self.preferences.icon_directory = self.chosen_icon_directory()
        self.preferences.max_recent_files = self.max_recent.value()
        self.preferences.show_alternates = self.alternates.isChecked()
        self.preferences.show_events = self.events.isChecked()
        logger.debug("preferences enregistrees")
        super().accept()


def _select(combo: QComboBox, class_name: str) -> None:
    """Select a stored class, leaving the first entry when it is no longer offered."""
    if not class_name:
        return
    index = combo.findData(class_name)
    if index >= 0:
        combo.setCurrentIndex(index)
