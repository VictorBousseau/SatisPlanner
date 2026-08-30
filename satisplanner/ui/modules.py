"""The module library on screen: choose one, read what it does, use it.

Two windows. A small box to name what is being saved, and the library itself --
kept open rather than modal, like the item card, because inserting three modules in
a row is the gesture this feature exists for and a box that closes after each one
would make it three gestures.

Two things are written on the screen rather than only in the code, because both are
things a reader will otherwise assume the other way round:

* the rates are those of the module **on its own**. Inserted into a factory that
  starves it, it will do less. It is a label, not a promise.
* an inserted module is a **copy**. Changing it afterwards does not change the
  module, and changing the module does not change the factories it is already in.
"""

import logging
from pathlib import Path
from typing import Final

from PySide6.QtCore import Qt, Signal, SignalInstance
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from satisplanner.core import formatting
from satisplanner.core.i18n import _
from satisplanner.core.models import GameData
from satisplanner.data.module_file import (
    MAX_NAME_LENGTH,
    FactoryModule,
    ModuleError,
    delete_module,
    load_library,
    rename_module,
)
from satisplanner.ui import theme

logger = logging.getLogger(__name__)

_ROLE_MODULE: Final = int(Qt.ItemDataRole.UserRole)

def label_caveat() -> str:
    """What the rates on a module's card do and do not promise."""
    return _(
        "Débits du module <b>seul</b>. Inséré dans une usine qui l'affame, il en fera "
        "moins : c'est une étiquette, pas une promesse."
    )


def copy_caveat() -> str:
    """That an inserted module stops being tied to the module it came from."""
    return _(
        "Un module inséré est une <b>copie</b> : le modifier ensuite ne change pas le "
        "module, et modifier le module ne change pas les usines où il est déjà."
    )


class SaveModuleDialog(QDialog):
    """Name and describe what is about to be saved."""

    def __init__(self, suggestion: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Enregistrer comme module")
        self.resize(460, 260)

        self.name = QLineEdit(suggestion, self)
        self.name.setMaxLength(MAX_NAME_LENGTH)
        self.name.setPlaceholderText("40 plaques de fer/min")
        self.name.selectAll()
        self.description = QPlainTextEdit(self)
        self.description.setPlaceholderText(
            _("À quoi il sert, ce qu'il faut lui brancher, ce qu'il en sort.")
        )

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self
        )
        buttons.accepted.connect(self._accept_if_named)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Nom", self))
        layout.addWidget(self.name)
        layout.addWidget(QLabel("Description", self))
        layout.addWidget(self.description, 1)
        layout.addWidget(_muted(copy_caveat(), self))
        layout.addWidget(buttons)

    def _accept_if_named(self) -> None:
        """A module with no name is a module nobody will ever find again."""
        if self.chosen_name():
            self.accept()

    def chosen_name(self) -> str:
        return self.name.text().strip()

    def chosen_description(self) -> str:
        return self.description.toPlainText().strip()


class ModuleLibraryDialog(QDialog):
    """The library: search, read, and act on the chosen module."""

    insertRequested = Signal(object)
    openRequested = Signal(object)
    newFromRequested = Signal(object)

    def __init__(
        self,
        game_data: GameData,
        directory: Path | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.game_data = game_data
        # Injectable so a test never writes into the developer's own library.
        self.directory = directory
        self.setWindowTitle(_("Bibliothèque de modules"))
        self.resize(860, 560)
        self._modules: list[FactoryModule] = []
        self._problems: list[str] = []

        self.search = QLineEdit(self)
        self.search.setPlaceholderText("Filtrer par nom ou par objet produit...")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._refill)

        self.list = QListWidget(self)
        self.list.currentItemChanged.connect(self._show_current)
        self.list.itemActivated.connect(lambda _item: self._insert())

        self.thumbnail = QLabel(self)
        self.thumbnail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumbnail.setMinimumHeight(140)
        self.details = QLabel(self)
        self.details.setWordWrap(True)
        self.details.setTextFormat(Qt.TextFormat.RichText)
        self.details.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)

        self.insert_button = QPushButton(_("Insérer dans l'usine"), self)
        self.insert_button.clicked.connect(self._insert)
        self.open_button = QPushButton("Ouvrir dans un onglet", self)
        self.open_button.clicked.connect(lambda: self._emit(self.openRequested))
        self.new_button = QPushButton("Nouveau depuis ce module", self)
        self.new_button.clicked.connect(lambda: self._emit(self.newFromRequested))
        self.rename_button = QPushButton("Renommer...", self)
        self.rename_button.clicked.connect(self._rename)
        self.describe_button = QPushButton(_("Décrire..."), self)
        self.describe_button.clicked.connect(self._describe)
        self.delete_button = QPushButton("Supprimer", self)
        self.delete_button.clicked.connect(self._delete)
        self.buttons = (
            self.insert_button,
            self.open_button,
            self.new_button,
            self.rename_button,
            self.describe_button,
            self.delete_button,
        )

        self.problems = QLabel(self)
        self.problems.setWordWrap(True)
        self.problems.setStyleSheet(f"color: {theme.STATE_STARVED};")
        self.problems.hide()

        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        close.rejected.connect(self.reject)

        left = QVBoxLayout()
        left.addWidget(self.search)
        left.addWidget(self.list, 1)

        right = QVBoxLayout()
        right.addWidget(self.thumbnail)
        right.addWidget(self.details, 1)
        for button in self.buttons:
            right.addWidget(button)

        columns = QHBoxLayout()
        columns.addLayout(left, 3)
        columns.addLayout(right, 2)

        layout = QVBoxLayout(self)
        layout.addLayout(columns, 1)
        layout.addWidget(self.problems)
        layout.addWidget(_muted(copy_caveat(), self))
        layout.addWidget(close)

        self.reload()

    # ------------------------------------------------------------------ data

    def reload(self) -> None:
        """Read the library again, keeping the module that was selected."""
        wanted = self.current_module()
        self._modules, self._problems = load_library(self.directory)
        self._refill()
        if wanted is not None:
            self.select_named(wanted.name)
        self._show_problems()

    def _refill(self) -> None:
        needle = self.search.text().strip().casefold()
        self.list.clear()
        for module in self._modules:
            if needle and not self._matches(module, needle):
                continue
            item = QListWidgetItem(self._row_text(module))
            item.setData(_ROLE_MODULE, module)
            self.list.addItem(item)
        if self.list.count():
            self.list.setCurrentRow(0)
        else:
            self._show_current()

    def _matches(self, module: FactoryModule, needle: str) -> bool:
        """By name, by description, or by what it makes -- in French, as displayed."""
        haystack = [module.name, module.description]
        haystack.extend(self._item_name(item_class) for item_class in module.outputs)
        return any(needle in text.casefold() for text in haystack)

    def _row_text(self, module: FactoryModule) -> str:
        made = ", ".join(self._item_name(item) for item in module.outputs) or "rien en sortie"
        return f"{module.name}\n{made}"

    def visible_modules(self) -> list[FactoryModule]:
        """What the list is showing, for the tests and for the buttons."""
        return [self.list.item(row).data(_ROLE_MODULE) for row in range(self.list.count())]

    def current_module(self) -> FactoryModule | None:
        item = self.list.currentItem()
        found = item.data(_ROLE_MODULE) if item is not None else None
        return found if isinstance(found, FactoryModule) else None

    def select_named(self, name: str) -> bool:
        for row in range(self.list.count()):
            module = self.list.item(row).data(_ROLE_MODULE)
            if isinstance(module, FactoryModule) and module.name == name:
                self.list.setCurrentRow(row)
                return True
        return False

    # --------------------------------------------------------------- display

    def _show_current(self, *_args: object) -> None:
        module = self.current_module()
        for button in self.buttons:
            button.setEnabled(module is not None)
        if module is None:
            self.thumbnail.clear()
            self.details.setText(
                f"<p style='color:{theme.TEXT_MUTED}'>"
                + _(
                    "Aucun module. Sélectionnez un morceau d'usine et « Enregistrer "
                    "la sélection comme module »."
                )
                + "</p>"
            )
            return
        self.details.setText(self.details_html(module))
        self._show_thumbnail(module)

    def details_html(self, module: FactoryModule) -> str:
        """Everything the reader needs to choose, caveat included."""
        parts = [f"<b>{module.name}</b>"]
        if module.description:
            parts.append(module.description)
        if module.saved_at:
            parts.append(
                f"<span style='color:{theme.TEXT_MUTED}'>"
                + _("Enregistré le {date}").format(date=_readable_date(module.saved_at))
                + "</span>"
            )
        parts.append(
            _("<b>Entrées</b> : {rates}").format(rates=self._rates(module.inputs))
        )
        parts.append(
            _("<b>Sorties</b> : {rates}").format(rates=self._rates(module.outputs))
        )
        parts.append(f"<span style='color:{theme.TEXT_MUTED}'>{label_caveat()}</span>")
        return "".join(f"<p>{part}</p>" for part in parts)

    def _rates(self, rates: dict[str, float]) -> str:
        if not rates:
            return _("aucune")
        return ", ".join(
            f"{self._item_name(item_class)} "
            f"{formatting.rate(rate, self.game_data.items.get(item_class))}"
            for item_class, rate in sorted(rates.items())
        )

    def _item_name(self, class_name: str) -> str:
        item = self.game_data.items.get(class_name)
        return item.name if item else class_name

    def _show_thumbnail(self, module: FactoryModule) -> None:
        if not module.thumbnail:
            self.thumbnail.clear()
            return
        picture = QPixmap()
        if not picture.loadFromData(module.thumbnail):
            self.thumbnail.clear()
            return
        self.thumbnail.setPixmap(
            picture.scaled(
                self.thumbnail.width() or 240,
                self.thumbnail.minimumHeight(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    def _show_problems(self) -> None:
        if not self._problems:
            self.problems.hide()
            return
        self.problems.setText(
            _("{count} fichier(s) illisible(s), ignoré(s) : ").format(
                count=len(self._problems)
            )
            + " ".join(self._problems)
        )
        self.problems.show()

    # --------------------------------------------------------------- actions

    def _emit(self, signal: SignalInstance) -> None:
        module = self.current_module()
        if module is not None:
            signal.emit(module)

    def _insert(self) -> None:
        self._emit(self.insertRequested)

    def _rename(self) -> None:
        module = self.current_module()
        if module is None:
            return
        name, accepted = QInputDialog.getText(
            self, "Renommer le module", "Nouveau nom", QLineEdit.EchoMode.Normal, module.name
        )
        if not accepted or not name.strip():
            return
        self._apply(lambda: rename_module(module, name.strip()))

    def _describe(self) -> None:
        module = self.current_module()
        if module is None:
            return
        text, accepted = QInputDialog.getMultiLineText(
            self, _("Décrire le module"), module.name, module.description
        )
        if not accepted:
            return
        self._apply(lambda: rename_module(module, module.name, text.strip()))

    def _delete(self) -> None:
        module = self.current_module()
        if module is None:
            return
        answer = QMessageBox.question(
            self,
            _("Supprimer le module"),
            _(
                "Supprimer « {name} » de la bibliothèque ?\n\n"
                "Les usines où il a déjà été inséré ne changent pas : elles en ont "
                "une copie."
            ).format(name=module.name),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._apply(lambda: delete_module(module))

    def _apply(self, action: object) -> None:
        """Run a library change, turning a refusal into a box rather than a crash."""
        assert callable(action)
        try:
            action()
        except ModuleError as exc:
            QMessageBox.warning(self, _("Bibliothèque"), str(exc))
        self.reload()


def _muted(text: str, parent: QWidget) -> QLabel:
    label = QLabel(text, parent)
    label.setWordWrap(True)
    label.setStyleSheet(f"color: {theme.TEXT_MUTED};")
    return label


def _readable_date(stamp: str) -> str:
    """``2026-07-27T18:10:02+00:00`` -> ``27/07/2026``, or the raw text if it is not one."""
    head = stamp.split("T", 1)[0]
    parts = head.split("-")
    if len(parts) != 3:
        return stamp
    year, month, day = parts
    return f"{day}/{month}/{year}"
