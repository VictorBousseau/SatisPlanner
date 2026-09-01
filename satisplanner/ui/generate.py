"""The dialog that turns "two of these a minute" into a factory.

It asks four things and no more: what, how much, exact ratios or whole buildings,
and which recipe to use where the catalogue offers a choice. That last one is the
only place alternates enter the generator at all -- **by decision, never by
calculation** -- and it is kept, so a player who always smelts iron the same way
says so once.

What comes back is an ordinary factory. There is no generated mode to leave and no
object of a second kind on the canvas: the dialog hands a
:class:`~satisplanner.core.graph.FactoryGraph` to the document, and from that
moment it is a factory somebody drew.
"""

import json
import logging
from typing import Final

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from satisplanner.core import breakdown, planner
from satisplanner.core.graph import AttachmentMode, FactoryGraph
from satisplanner.core.i18n import _
from satisplanner.core.models import GameData
from satisplanner.ui.catalogue import fold
from satisplanner.ui.preferences import Preferences

logger = logging.getLogger(__name__)

# Beyond this the list is unusable and the search box is the way through it, which
# is the same rule the palette follows.
MAX_VISIBLE: Final = 300
DEFAULT_RATE: Final = 2.0

def caveat() -> str:
    """What the generator cannot know, said where the factory is asked for."""
    return _(
        "Les gisements sont posés en pureté normale avec le premier extracteur venu : "
        "ce qui se trouve sur votre carte n'est écrit nulle part dans les données du "
        "jeu. Réglez-les, le reste suivra."
    )


def craftable_items(game_data: GameData) -> list[tuple[str, str]]:
    """Everything a recipe makes, as ``(class, name)`` sorted by name."""
    made = {
        slot.item_class
        for recipe in game_data.recipes.values()
        for slot in recipe.products
        if slot.item_class in game_data.items
    }
    return sorted(
        ((item_class, game_data.items[item_class].name) for item_class in made),
        key=lambda pair: fold(pair[1]),
    )


class GenerateDialog(QDialog):
    """What to make, how much of it, and how to round the machines."""

    def __init__(
        self,
        game_data: GameData,
        preferences: Preferences,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.game_data = game_data
        self.preferences = preferences
        self.setWindowTitle(_("Générer une usine"))
        self.setModal(True)
        self.choices: dict[str, str] = preferences.recipe_choices

        self.search = QLineEdit(self)
        self.search.setPlaceholderText("Rechercher un objet...")
        self.search.setClearButtonEnabled(True)
        self.items = QListWidget(self)
        self._all = craftable_items(game_data)

        self.rate = QDoubleSpinBox(self)
        self.rate.setRange(0.001, 100_000.0)
        self.rate.setDecimals(3)
        self.rate.setValue(DEFAULT_RATE)
        self.rate.setSuffix(" /min")

        self.variant = QComboBox(self)
        self.variant.addItem(_("Ratios exacts — machines en nombre décimal"), userData=False)
        self.variant.addItem(_("Arrondi au supérieur — constructible tel quel"), userData=True)

        self.recipe_item = QComboBox(self)
        self.recipe_choice = QComboBox(self)
        self.pinned = QLabel(self)
        self.pinned.setWordWrap(True)

        self.caveat = QLabel(caveat(), self)
        self.caveat.setWordWrap(True)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText(_("Générer"))

        self._build_layout()
        self._connect()
        self._refresh_items()
        self._refresh_recipe_items()

    def _build_layout(self) -> None:
        form = QFormLayout()
        form.addRow(_("Débit visé"), self.rate)
        form.addRow("Variante", self.variant)

        pinning = QHBoxLayout()
        pinning.addWidget(self.recipe_item, 1)
        pinning.addWidget(self.recipe_choice, 1)
        forget = QPushButton("Oublier", self)
        forget.clicked.connect(self._forget)
        pinning.addWidget(forget)

        layout = QVBoxLayout(self)
        layout.addWidget(self.search)
        layout.addWidget(self.items, 1)
        layout.addLayout(form)
        layout.addWidget(QLabel(_("Recette imposée (les alternatives se choisissent ici)"), self))
        layout.addLayout(pinning)
        layout.addWidget(self.pinned)
        layout.addWidget(self.caveat)
        layout.addWidget(self.buttons)

    def _connect(self) -> None:
        self.search.textChanged.connect(self._refresh_items)
        self.recipe_item.currentIndexChanged.connect(self._refresh_recipe_choices)
        self.recipe_choice.currentIndexChanged.connect(self._pin)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        self.items.itemDoubleClicked.connect(lambda _item: self.accept())

    # ------------------------------------------------------------------ lists

    def _refresh_items(self) -> None:
        query = self.search.text().strip()
        self.items.clear()
        shown = 0
        for item_class, name in self._all:
            key = fold(f"{name} {item_class}")
            if query and not all(word in key for word in fold(query).split()):
                continue
            entry = QListWidgetItem(name)
            entry.setData(Qt.ItemDataRole.UserRole, item_class)
            self.items.addItem(entry)
            shown += 1
            if shown >= MAX_VISIBLE:
                break
        if self.items.count():
            self.items.setCurrentRow(0)

    def _refresh_recipe_items(self) -> None:
        """Only the items with a real choice: one recipe is not a decision."""
        self.recipe_item.clear()
        for item_class, name in self._all:
            if len(breakdown.producers(self.game_data, item_class)) > 1:
                self.recipe_item.addItem(name, userData=item_class)
        self._refresh_recipe_choices()

    def _refresh_recipe_choices(self) -> None:
        item_class = self.recipe_item.currentData()
        self.recipe_choice.blockSignals(True)
        self.recipe_choice.clear()
        if item_class:
            for recipe in breakdown.producers(self.game_data, str(item_class)):
                self.recipe_choice.addItem(recipe.name, userData=recipe.class_name)
            pinned = self.choices.get(str(item_class))
            if pinned:
                index = self.recipe_choice.findData(pinned)
                if index >= 0:
                    self.recipe_choice.setCurrentIndex(index)
        self.recipe_choice.blockSignals(False)
        self._describe_pinned()

    def _pin(self) -> None:
        item_class = self.recipe_item.currentData()
        recipe_class = self.recipe_choice.currentData()
        if not item_class or not recipe_class:
            return
        standard = breakdown.standard_recipe(self.game_data, str(item_class))
        if standard is not None and standard.class_name == recipe_class:
            self.choices.pop(str(item_class), None)
        else:
            self.choices[str(item_class)] = str(recipe_class)
        self._describe_pinned()

    def _forget(self) -> None:
        self.choices.clear()
        self._refresh_recipe_choices()

    def _describe_pinned(self) -> None:
        if not self.choices:
            self.pinned.setText(_("Aucune recette imposée : partout la recette standard."))
            return
        named = ", ".join(
            f"{self.game_data.items[item].name} → "
            f"{self.game_data.recipes[recipe].name}"
            for item, recipe in sorted(self.choices.items())
            if item in self.game_data.items and recipe in self.game_data.recipes
        )
        self.pinned.setText(
            _("{count} recette(s) imposée(s) : {named}").format(
                count=len(self.choices), named=named
            )
        )

    # ----------------------------------------------------------------- result

    def target(self) -> str | None:
        current = self.items.currentItem()
        return None if current is None else str(current.data(Qt.ItemDataRole.UserRole))

    def is_rounded(self) -> bool:
        return bool(self.variant.currentData())

    def accept(self) -> None:
        """Refuse rather than close on nothing: an empty dialog is not a target."""
        if self.target() is None:
            QMessageBox.warning(self, _("Générer une usine"), _("Choisissez d'abord un objet."))
            return
        self.preferences.recipe_choices = self.choices
        super().accept()

    def generate(
        self, mode: AttachmentMode = AttachmentMode.SIMPLE
    ) -> tuple[FactoryGraph, list[str]]:
        """The factory and what the generator has to say about it.

        ``mode`` is the mode of the document being generated into: what comes out
        is an ordinary factory, so it obeys the rule of the document it lands in
        rather than one of its own.

        Raises :class:`~satisplanner.core.planner.PlanError` with a French sentence,
        which the caller shows as it is: a target that cannot be built is an answer,
        not a crash.
        """
        item_class = self.target()
        assert item_class is not None
        made = planner.plan(
            self.game_data, item_class, self.rate.value(), self.choices, rounded=self.is_rounded()
        )
        graph = planner.build(self.game_data, made, mode)
        return graph, planner.report(self.game_data, made, rounded=self.is_rounded())


def encode_choices(choices: dict[str, str]) -> str:
    """Recipe choices as one stored string. JSON, so a class name cannot be split."""
    return json.dumps(dict(sorted(choices.items())), separators=(",", ":"))


def decode_choices(stored: str) -> dict[str, str]:
    """Read them back, and treat anything unreadable as "none chosen".

    A preference is not worth a crash on startup: the worst a corrupt entry can do
    here is send the generator back to the standard recipes, which is where it
    starts from anyway.
    """
    try:
        found = json.loads(stored or "{}")
    except json.JSONDecodeError:
        logger.debug("choix de recettes illisibles, ignorés")
        return {}
    if not isinstance(found, dict):
        return {}
    return {str(key): str(value) for key, value in found.items()}
