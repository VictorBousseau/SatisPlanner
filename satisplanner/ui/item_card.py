"""The item card: "how is this thing made, and how much does one machine put out".

The question a player asks constantly and that, until now, this application sent them
to a wiki for. The page is modelled on one: identity, then every recipe that makes it,
then everything that eats it, then what it costs in ore.

Two decisions worth stating.

**Every item name on the page is a link to its own card**, and the dialog keeps a
history with back and forward. That is what turns a lookup into something you can
follow: iron plate, reinforced plate, screw, iron rod, back. Without it the card
answers one question and sends you to the search box for the next.

**The raw cost is labelled as indicative, in the page itself.** It expands standard
recipes only and credits no byproducts (see :mod:`satisplanner.core.breakdown`), so
it is a fair comparison between two items and a poor estimate of a real factory. A
number a reader might mistake for the truth has to say what it is next to the number,
not in a manual.

The card is deliberately **not modal**: it is meant to stay open beside the canvas.
"""

import logging
from html import escape
from typing import Final

from PySide6.QtCore import QUrl, Signal
from PySide6.QtGui import QAction, QKeySequence, QTextDocument
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from satisplanner.core import breakdown, formatting
from satisplanner.core.models import GameData, Item, ItemForm, Recipe
from satisplanner.ui import theme
from satisplanner.ui.catalogue import fold
from satisplanner.ui.icon_provider import IconProvider

logger = logging.getLogger(__name__)

# Link schemes the page uses. Anything else is refused rather than followed.
ITEM_SCHEME: Final = "item"
PLACE_SCHEME: Final = "place"

ICON_SIDE: Final = 48

FORM_LABELS: Final[dict[ItemForm, str]] = {
    ItemForm.SOLID: "Solide",
    ItemForm.LIQUID: "Liquide",
    ItemForm.GAS: "Gazeux",
}


# --------------------------------------------------------------------------- #
# The page
# --------------------------------------------------------------------------- #


def stylesheet() -> str:
    return f"""
    body {{ color: {theme.TEXT}; font-size: 10pt; }}
    h1 {{ color: {theme.TEXT}; font-size: 14pt; margin: 0 0 2px 0; }}
    h2 {{ color: {theme.ACCENT}; font-size: 11pt; margin: 16px 0 4px 0; }}
    h3 {{ color: {theme.TEXT}; font-size: 10pt; margin: 10px 0 2px 0; }}
    table {{ width: 100%; border-collapse: collapse; }}
    td {{ padding: 1px 6px 1px 0; }}
    td.value {{ text-align: right; white-space: nowrap; }}
    td.rate {{ text-align: right; white-space: nowrap; color: {theme.TEXT_MUTED}; }}
    a {{ color: {theme.ACCENT}; text-decoration: none; }}
    .muted {{ color: {theme.TEXT_MUTED}; }}
    .desc {{ color: {theme.TEXT_MUTED}; font-style: italic; }}
    .place {{ color: {theme.STATE_NOMINAL}; }}
    .warn {{ color: {theme.STATE_STARVED}; }}
    """


def card_html(game_data: GameData, item_class: str) -> str:
    """The whole page for one item, as HTML. Testable without opening a window."""
    item = game_data.items.get(item_class)
    if item is None:
        return _page(
            f"<h1>{escape(item_class)}</h1>"
            "<p class='warn'>Cet objet est absent du catalogue embarque.</p>"
        )
    blocks = [
        _identity(item),
        _recipes(game_data, item),
        _used_in(game_data, item),
        _raw_cost(game_data, item),
    ]
    return _page("\n".join(blocks))


def _page(body: str) -> str:
    return f"<html><head><style>{stylesheet()}</style></head><body>{body}</body></html>"


def _identity(item: Item) -> str:
    """Icon, name, the game's own blurb, and the three facts that fit on one line."""
    facts = [FORM_LABELS[item.form]]
    if item.stack_size > 0 and not item.form.is_fluid:
        facts.append(f"pile de {formatting.number(item.stack_size)}")
    if item.sink_points > 0:
        facts.append(f"{item.sink_points} points au collecteur AWESOME")

    # The blurb is shown only when the data carries one, and is never written here.
    description = ""
    if item.description_fr:
        lines = "<br>".join(escape(line) for line in item.description_fr.splitlines() if line)
        description = f"<p class='desc'>{lines}</p>"

    return (
        f"<table><tr>"
        f"<td width='{ICON_SIDE + 12}'><img src='{ITEM_SCHEME}:{escape(item.class_name)}' "
        f"width='{ICON_SIDE}' height='{ICON_SIDE}'></td>"
        f"<td><h1>{escape(item.display_name_fr)}</h1>"
        f"<p class='muted'>{escape(' — '.join(facts))}</p></td>"
        f"</tr></table>{description}"
    )


def _recipes(game_data: GameData, item: Item) -> str:
    made = breakdown.producers(game_data, item.class_name)
    if not made:
        note = (
            "Ressource brute : elle s'extrait, elle ne se fabrique pas."
            if item.is_raw_resource
            else "Aucune recette connue dans le perimetre V1."
        )
        return f"<h2>Fabrication</h2><p class='muted'>{note}</p>"
    return "<h2>Fabrication</h2>" + "".join(
        _one_recipe(game_data, recipe, item.class_name) for recipe in made
    )


def _one_recipe(game_data: GameData, recipe: Recipe, headline: str) -> str:
    """One recipe block: what it runs in, what goes in, what comes out."""
    building = game_data.buildings.get(recipe.building_class)
    machine = building.display_name_fr if building else recipe.building_class
    cycles_per_minute = 60.0 / recipe.cycle_seconds if recipe.cycle_seconds > 0 else 0.0

    rows: list[str] = []
    for slot in recipe.ingredients:
        rows.append(_slot_row(game_data, slot.item_class, slot.amount_per_cycle, cycles_per_minute))
    for slot in recipe.products:
        label = "Produit" if slot.item_class == headline else "Sous-produit"
        rows.append(
            _slot_row(
                game_data,
                slot.item_class,
                slot.amount_per_cycle,
                cycles_per_minute,
                prefix=label,
            )
        )

    power = ""
    if building is not None and building.power_mw > 0:
        power = f" — {formatting.number(building.power_mw)} MW"
    # Most French labels already read "... (alternative)"; the marker is only added
    # where the game left it out, exactly as the palette does.
    marker = ""
    if recipe.is_alternate and "alternative" not in fold(recipe.display_name_fr):
        marker = " <span class='muted'>(alternative)</span>"
    place = (
        f"<a class='place' href='{PLACE_SCHEME}:{escape(recipe.class_name)}'>"
        f"[poser sur le canvas]</a>"
    )
    return (
        f"<h3>{escape(recipe.display_name_fr)}{marker}</h3>"
        f"<p class='muted'>{escape(machine)} — cycle de "
        f"{formatting.number(recipe.cycle_seconds)} s{escape(power)} &nbsp; {place}</p>"
        f"<table>{''.join(rows)}</table>"
    )


def _slot_row(
    game_data: GameData,
    item_class: str,
    amount: float,
    cycles_per_minute: float,
    prefix: str = "",
) -> str:
    """One ingredient or product line: name, amount per cycle, rate per minute."""
    item = game_data.items.get(item_class)
    name = item.display_name_fr if item else item_class
    label = f"{prefix} : " if prefix else ""
    unit = "m³" if item is not None and item.form.is_fluid else ""
    return (
        f"<tr><td>{escape(label)}"
        f"<a href='{ITEM_SCHEME}:{escape(item_class)}'>{escape(name)}</a></td>"
        f"<td class='value'>{formatting.number(amount)} {unit} / cycle</td>"
        f"<td class='rate'>{formatting.rate(amount * cycles_per_minute, item)}</td></tr>"
    )


def _used_in(game_data: GameData, item: Item) -> str:
    used = breakdown.consumers(game_data, item.class_name)
    if not used:
        return (
            "<h2>Recettes qui le consomment</h2>"
            "<p class='muted'>Aucune recette du perimetre V1 ne consomme cet objet.</p>"
        )
    rows = []
    for recipe in used:
        building = game_data.buildings.get(recipe.building_class)
        machine = building.display_name_fr if building else recipe.building_class
        cycles = 60.0 / recipe.cycle_seconds if recipe.cycle_seconds > 0 else 0.0
        amount = sum(
            slot.amount_per_cycle
            for slot in recipe.ingredients
            if slot.item_class == item.class_name
        )
        target = recipe.products[0].item_class if recipe.products else item.class_name
        rows.append(
            f"<tr><td><a href='{ITEM_SCHEME}:{escape(target)}'>"
            f"{escape(recipe.display_name_fr)}</a></td>"
            f"<td class='muted'>{escape(machine)}</td>"
            f"<td class='value'>{formatting.number(amount)} / cycle</td>"
            f"<td class='rate'>{formatting.rate(amount * cycles, item)}</td></tr>"
        )
    return f"<h2>Recettes qui le consomment</h2><table>{''.join(rows)}</table>"


def _raw_cost(game_data: GameData, item: Item) -> str:
    cost = breakdown.raw_cost(game_data, item.class_name)
    if not cost.is_complete:
        return (
            "<h2>Cout en ressources brutes</h2>"
            "<p class='warn'>Calcul abandonne : les recettes standard de cet objet "
            "bouclent sur elles-memes.</p>"
            f"<p class='muted'>{escape(cost.cycle_description)}</p>"
        )
    if list(cost.amounts) == [item.class_name]:
        return (
            "<h2>Cout en ressources brutes</h2><p class='muted'>C'est deja une ressource brute.</p>"
        )
    rows = "".join(
        f"<tr><td><a href='{ITEM_SCHEME}:{escape(name)}'>"
        f"{escape(_name_of(game_data, name))}</a></td>"
        f"<td class='value'>{formatting.number(amount)}</td></tr>"
        for name, amount in cost.amounts.items()
    )
    return (
        f"<h2>Cout en ressources brutes</h2>"
        f"<p class='muted'>Pour un(e) {escape(item.display_name_fr)} :</p>"
        f"<table>{rows}</table>"
        f"<p class='muted'><b>Indicatif.</b> Le calcul ne suit que les recettes standard "
        f"et ne credite pas les sous-produits : une recette alternative ou un sous-produit "
        f"revendu changent le resultat. Pour un chiffre juste, posez l'usine.</p>"
    )


def _name_of(game_data: GameData, item_class: str) -> str:
    item = game_data.items.get(item_class)
    return item.display_name_fr if item else item_class


# --------------------------------------------------------------------------- #
# The window
# --------------------------------------------------------------------------- #


class ItemCard(QDialog):
    """A browsable card, with a history of its own."""

    # A recipe class the user asked to place on the canvas.
    placeRequested = Signal(str)

    def __init__(
        self, game_data: GameData, icons: IconProvider, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.game_data = game_data
        self.icons = icons
        self._history: list[str] = []
        self._position = -1

        self.setWindowTitle("Fiche d'objet")
        self.resize(620, 720)
        # Not modal: the point is to keep it open next to the canvas.
        self.setModal(False)

        self.browser = QTextBrowser(self)
        self.browser.setOpenLinks(False)
        self.browser.setOpenExternalLinks(False)
        self.browser.anchorClicked.connect(self._follow)

        self.back_button = QPushButton("◀ Precedent", self)
        self.forward_button = QPushButton("Suivant ▶", self)
        self.back_button.clicked.connect(self.go_back)
        self.forward_button.clicked.connect(self.go_forward)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        buttons.rejected.connect(self.reject)

        navigation = QHBoxLayout()
        navigation.setContentsMargins(0, 0, 0, 0)
        navigation.addWidget(self.back_button)
        navigation.addWidget(self.forward_button)
        navigation.addStretch(1)
        navigation.addWidget(buttons)

        layout = QVBoxLayout(self)
        layout.addWidget(self.browser, 1)
        layout.addLayout(navigation)

        # Alt+Left and Alt+Right, the bindings a browser has trained everyone on.
        for key, slot in (
            (QKeySequence.StandardKey.Back, self.go_back),
            (QKeySequence.StandardKey.Forward, self.go_forward),
        ):
            action = QAction(self)
            action.setShortcut(key)
            action.triggered.connect(slot)
            self.addAction(action)

        self._refresh_navigation()

    # ------------------------------------------------------------ navigation

    @property
    def current_item(self) -> str | None:
        if 0 <= self._position < len(self._history):
            return self._history[self._position]
        return None

    def show_item(self, item_class: str) -> None:
        """Open a card, pushing it onto the history.

        Following a link after going back discards what was ahead, exactly as a
        browser does: the forward list described a path the reader has left.
        """
        if item_class == self.current_item:
            return
        del self._history[self._position + 1 :]
        self._history.append(item_class)
        self._position = len(self._history) - 1
        self._render()

    def go_back(self) -> bool:
        if self._position <= 0:
            return False
        self._position -= 1
        self._render()
        return True

    def go_forward(self) -> bool:
        if self._position >= len(self._history) - 1:
            return False
        self._position += 1
        self._render()
        return True

    def _refresh_navigation(self) -> None:
        self.back_button.setEnabled(self._position > 0)
        self.forward_button.setEnabled(self._position < len(self._history) - 1)

    # --------------------------------------------------------------- content

    def _render(self) -> None:
        item_class = self.current_item
        if item_class is None:
            return
        item = self.game_data.items.get(item_class)
        self.setWindowTitle(f"Fiche — {item.display_name_fr}" if item else f"Fiche — {item_class}")
        self._register_icon(item)
        self.browser.setHtml(card_html(self.game_data, item_class))
        self._refresh_navigation()

    def _register_icon(self, item: Item | None) -> None:
        """Hand the icon to the document under the URL the page refers to.

        A ``QTextBrowser`` cannot reach the icon provider on its own, so the pixmap
        is registered as a document resource before the HTML that names it is set.
        """
        if item is None:
            return
        pixmap = self.icons.for_item(item).pixmap(ICON_SIDE, ICON_SIDE)
        self.browser.document().addResource(
            QTextDocument.ResourceType.ImageResource,
            QUrl(f"{ITEM_SCHEME}:{item.class_name}"),
            pixmap,
        )

    def _follow(self, url: QUrl) -> None:
        """Handle a click on one of the page's own links, and nothing else."""
        scheme, target = url.scheme(), url.path() or url.toString().partition(":")[2]
        if scheme == ITEM_SCHEME and target:
            self.show_item(target)
        elif scheme == PLACE_SCHEME and target:
            self.placeRequested.emit(target)
        else:
            logger.debug("lien ignore dans la fiche : %s", url.toString())

    # A card is closed, never destroyed: the window reopens the same one, so the
    # history a reader has built up survives being dismissed.
    def closeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        self.hide()
        event.accept()
