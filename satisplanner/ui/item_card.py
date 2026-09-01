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
from satisplanner.core.i18n import _
from satisplanner.core.models import GameData, Item, ItemForm, Recipe, RecipeAvailability
from satisplanner.ui import theme
from satisplanner.ui.catalogue import says_alternate
from satisplanner.ui.icon_provider import IconProvider

logger = logging.getLogger(__name__)

# Link schemes the page uses. Anything else is refused rather than followed.
ITEM_SCHEME: Final = "item"
PLACE_SCHEME: Final = "place"

ICON_SIDE: Final = 48

def form_label(form: ItemForm) -> str:
    """Solid, liquid or gas, in the language in force."""
    match form:
        case ItemForm.SOLID:
            return _("Solide")
        case ItemForm.LIQUID:
            return _("Liquide")
        case ItemForm.GAS:
            return _("Gazeux")

# Why a recipe the game has cannot be put on the canvas. Two reasons, and the card
# must tell them apart: one will go away as the catalogue widens, the other never
# will, and a reader deciding whether to wait needs to know which is which.
def unavailable_reason(availability: RecipeAvailability) -> str | None:
    """Why a recipe the game has cannot be placed, or ``None`` when it can."""
    if availability is RecipeAvailability.MACHINE_OUT_OF_SCOPE:
        return _("machine que cette version ne modélise pas encore")
    if availability is RecipeAvailability.HAND_CRAFTED:
        return _("fabrication à la main : cette station ne sera jamais un nœud d'usine")
    return None


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
    h3.outside {{ color: {theme.TEXT_MUTED}; }}
    .warn {{ color: {theme.STATE_STARVED}; }}
    """


def card_html(game_data: GameData, item_class: str) -> str:
    """The whole page for one item, as HTML. Testable without opening a window."""
    item = game_data.items.get(item_class)
    if item is None:
        return _page(
            f"<h1>{escape(item_class)}</h1>"
            f"<p class='warn'>{_('Cet objet est absent du catalogue embarqué.')}</p>"
        )
    blocks = [
        _identity(item),
        _recipes(game_data, item),
        _out_of_scope(game_data, item),
        _used_in(game_data, item),
        _raw_cost(game_data, item),
    ]
    return _page("\n".join(blocks))


def _page(body: str) -> str:
    return f"<html><head><style>{stylesheet()}</style></head><body>{body}</body></html>"


def _identity(item: Item) -> str:
    """Icon, name, the game's own blurb, and the three facts that fit on one line."""
    facts = [form_label(item.form)]
    if item.stack_size > 0 and not item.form.is_fluid:
        facts.append(
            _("pile de {size}").format(size=formatting.number(item.stack_size))
        )
    if item.sink_points > 0:
        facts.append(
            _("{points} points au collecteur AWESOME").format(points=item.sink_points)
        )

    # The blurb is shown only when the data carries one, and is never written here.
    description = ""
    if item.description:
        lines = "<br>".join(escape(line) for line in item.description.splitlines() if line)
        description = f"<p class='desc'>{lines}</p>"

    return (
        f"<table><tr>"
        f"<td width='{ICON_SIDE + 12}'><img src='{ITEM_SCHEME}:{escape(item.class_name)}' "
        f"width='{ICON_SIDE}' height='{ICON_SIDE}'></td>"
        f"<td><h1>{escape(item.name)}</h1>"
        f"<p class='muted'>{escape(' — '.join(facts))}</p></td>"
        f"</tr></table>{description}"
    )


def _recipes(game_data: GameData, item: Item) -> str:
    made = breakdown.producers(game_data, item.class_name)
    if made:
        return f"<h2>{_('Fabrication')}</h2>" + "".join(
            _one_recipe(game_data, recipe, item.class_name) for recipe in made
        )
    return (
        f"<h2>{_('Fabrication')}</h2>"
        f"<p class='muted'>{_nothing_makes_it(game_data, item)}</p>"
    )


def _nothing_makes_it(game_data: GameData, item: Item) -> str:
    """Why no node produces this item -- four answers, and only one is a dead end.

    Saying "no recipe" and stopping is what sent readers to a wiki: it reads as a
    gap in the data. Each case here names its own cause, and the section that
    follows lists the recipes the game does have when there are any.
    """
    if item.is_raw_resource:
        return _("Ressource brute : elle s'extrait, elle ne se fabrique pas.")
    if breakdown.unavailable_producers(game_data, item.class_name):
        return _(
            "Aucune recette posable dans cette version. "
            "Ce que le jeu propose est ci-dessous."
        )
    sources = breakdown.generator_sources(game_data, item.class_name)
    if sources:
        # No recipe makes it because it is not made: it falls out of a generator.
        # Read from the catalogue rather than written on the item, so it answers
        # with the rate as well as with the building.
        burning = _("{building} brûlant {fuel} ({rate})")
        written = ", ".join(
            burning.format(
                building=escape(_building_name(game_data, generator.class_name)),
                fuel=escape(_name_of(game_data, fuel)),
                rate=formatting.rate(generator.byproducts(fuel)[item.class_name], item),
            )
            for generator, fuel in sources
        )
        return _(
            "Aucune recette : cet objet est un <b>sous-produit</b>. "
            "Il sort d'une {sources}."
        ).format(sources=written)
    if item.byproduct_of_fr:
        return _(
            "Aucune recette dans le jeu : cet objet tombe de la {building}, un "
            "bâtiment que cette version ne modélise pas encore. Il entre donc dans "
            "l'usine par un apport extérieur."
        ).format(building=escape(item.byproduct_of_fr))
    return _(
        "Aucune recette dans le jeu : cet objet se ramasse dans le monde. "
        "Il entre dans l'usine par un apport extérieur."
    )


def _building_name(game_data: GameData, class_name: str) -> str:
    building = game_data.buildings.get(class_name)
    return building.name if building else class_name


def _out_of_scope(game_data: GameData, item: Item) -> str:
    """The recipes the game has for this item and that no node can place."""
    outside = breakdown.unavailable_producers(game_data, item.class_name)
    if not outside:
        return ""
    return f"<h2>{_('Recettes hors du périmètre de cette version')}</h2>" + "".join(
        _one_recipe(game_data, recipe, item.class_name, placeable=False) for recipe in outside
    )


def _machine_label(game_data: GameData, recipe: Recipe) -> str:
    """The machine's name, wherever the catalogue happens to keep it."""
    if recipe.building_name:
        return recipe.building_name
    building = game_data.buildings.get(recipe.building_class)
    return building.name if building else recipe.building_class


def _one_recipe(
    game_data: GameData, recipe: Recipe, headline: str, *, placeable: bool = True
) -> str:
    """One recipe block: what it runs in, what goes in, what comes out.

    An unplaceable recipe is shown with the same figures -- they are the game's,
    and hiding them would answer half the question -- but greyed, without the
    button that would put it on the canvas, and followed by what stops it.
    """
    building = game_data.buildings.get(recipe.building_class)
    machine = _machine_label(game_data, recipe)
    cycles_per_minute = 60.0 / recipe.cycle_seconds if recipe.cycle_seconds > 0 else 0.0

    rows: list[str] = []
    for slot in recipe.ingredients:
        rows.append(_slot_row(game_data, slot.item_class, slot.amount_per_cycle, cycles_per_minute))
    for slot in recipe.products:
        label = _("Produit : ") if slot.item_class == headline else _("Sous-produit : ")
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
    if recipe.has_own_power:
        # This machine's draw depends on what it is making, so the figure belongs
        # on the recipe line and nowhere else. Both ends of the swing are shown
        # next to the mean, because a reader sizing a power plant wants to know
        # that an Encoder touches two gigawatts on the way.
        low, high = recipe.power_range_mw
        power = _(" — {power} MW en moyenne ({low} à {high})").format(
            power=formatting.number(recipe.power_mw),
            low=formatting.number(low),
            high=formatting.number(high),
        )
    elif building is not None and building.power_mw > 0:
        power = _(" — {power} MW").format(power=formatting.number(building.power_mw))
    # Most of the game's own labels already say so; the marker is only added
    # where the game left it out, exactly as the palette does.
    marker = ""
    if recipe.is_alternate and not says_alternate(recipe.name):
        marker = f" <span class='muted'>{_('(alternative)')}</span>"
    if placeable:
        tail = (
            f" &nbsp; <a class='place' href='{PLACE_SCHEME}:{escape(recipe.class_name)}'>"
            f"{_('[poser sur le canvas]')}</a>"
        )
    else:
        tail = f"<br>{escape(unavailable_reason(recipe.availability) or '')}"
    heading = "h3 class='outside'" if not placeable else "h3"
    cycle = _("{machine} — cycle de {seconds} s")
    return (
        f"<{heading}>{escape(recipe.name)}{marker}</h3>"
        f"<p class='muted'>"
        f"{escape(cycle.format(machine=machine, seconds=formatting.number(recipe.cycle_seconds)))}"
        f"{escape(power)}{tail}</p>"
        f"<table>{''.join(rows)}</table>"
    )


def _slot_row(
    game_data: GameData,
    item_class: str,
    amount: float,
    cycles_per_minute: float,
    prefix: str = "",
) -> str:
    """One ingredient or product line: name, amount per cycle, rate per minute.

    The prefix arrives with its own punctuation -- "Produit : ", *Product: * -- and
    is not built here from a word and a colon: the space before a colon is French
    typography and English wants none, so the pair travels together.
    """
    item = game_data.items.get(item_class)
    name = item.name if item else item_class
    unit = "m³" if item is not None and item.form.is_fluid else ""
    return (
        f"<tr><td>{escape(prefix)}"
        f"<a href='{ITEM_SCHEME}:{escape(item_class)}'>{escape(name)}</a></td>"
        f"<td class='value'>{formatting.number(amount)} {unit} / cycle</td>"
        f"<td class='rate'>{formatting.rate(amount * cycles_per_minute, item)}</td></tr>"
    )


def _used_in(game_data: GameData, item: Item) -> str:
    used = breakdown.consumers(game_data, item.class_name)
    outside = breakdown.unavailable_consumers(game_data, item.class_name)
    if not used and not outside:
        return (
            f"<h2>{_('Recettes qui le consomment')}</h2>"
            f"<p class='muted'>{_('Aucune recette du jeu ne consomme cet objet.')}</p>"
        )
    rows = [_consumer_row(game_data, recipe, item) for recipe in used]
    if outside:
        rows.append(
            f"<tr><td colspan='4' class='muted'><br>"
            f"{_('Hors du périmètre de cette version :')}</td></tr>"
        )
        rows += [_consumer_row(game_data, recipe, item, placeable=False) for recipe in outside]
    return (
        f"<h2>{_('Recettes qui le consomment')}</h2><table>{''.join(rows)}</table>"
    )


def _consumer_row(
    game_data: GameData, recipe: Recipe, item: Item, *, placeable: bool = True
) -> str:
    """One line of the consumers table. Out-of-scope ones carry their machine too:
    knowing that the Hadron Collider eats this is the answer, not noise."""
    machine = _machine_label(game_data, recipe)
    cycles = 60.0 / recipe.cycle_seconds if recipe.cycle_seconds > 0 else 0.0
    amount = sum(
        slot.amount_per_cycle for slot in recipe.ingredients if slot.item_class == item.class_name
    )
    # An unplaceable recipe is not a link: following it would open the card of a
    # product this version cannot make, which is a dead end dressed as a path.
    target = recipe.products[0].item_class if recipe.products else item.class_name
    name = (
        f"<a href='{ITEM_SCHEME}:{escape(target)}'>{escape(recipe.name)}</a>"
        if placeable
        else f"<span class='muted'>{escape(recipe.name)}</span>"
    )
    return (
        f"<tr><td>{name}</td>"
        f"<td class='muted'>{escape(machine)}</td>"
        f"<td class='value'>{formatting.number(amount)} / cycle</td>"
        f"<td class='rate'>{formatting.rate(amount * cycles, item)}</td></tr>"
    )


def _raw_cost(game_data: GameData, item: Item) -> str:
    cost = breakdown.raw_cost(game_data, item.class_name)
    if not cost.is_complete:
        looping = _(
            "Calcul abandonné : les recettes standard de cet objet bouclent sur "
            "elles-mêmes."
        )
        return (
            f"<h2>{_('Coût en ressources brutes')}</h2>"
            f"<p class='warn'>{looping}</p>"
            f"<p class='muted'>{escape(cost.cycle_description)}</p>"
        )
    if list(cost.amounts) == [item.class_name]:
        # The expansion stops at anything it cannot make, and until now it called
        # all of them raw resources. Uranium waste is not a raw resource, and the
        # section just above says so: two answers on one page, one of them wrong.
        if item.is_raw_resource:
            note = _("C'est déjà une ressource brute.")
        elif breakdown.generator_sources(game_data, item.class_name):
            note = _(
                "Le calcul s'arrête ici : cet objet n'est pas fabriqué mais rejeté, "
                "et son coût est celui du carburant qui le produit."
            )
        else:
            note = _(
                "Le calcul s'arrête ici : rien dans le catalogue ne fabrique cet objet, "
                "il entre dans l'usine tel quel."
            )
        return (
            f"<h2>{_('Coût en ressources brutes')}</h2><p class='muted'>{note}</p>"
        )
    rows = "".join(
        f"<tr><td><a href='{ITEM_SCHEME}:{escape(name)}'>"
        f"{escape(_name_of(game_data, name))}</a></td>"
        f"<td class='value'>{formatting.number(amount)}</td></tr>"
        for name, amount in cost.amounts.items()
    )
    caveat = _(
        "<b>Indicatif.</b> Le calcul ne suit que les recettes standard et ne crédite "
        "pas les sous-produits : une recette alternative ou un sous-produit revendu "
        "changent le résultat. Pour un chiffre juste, posez l'usine."
    )
    return (
        f"<h2>{_('Coût en ressources brutes')}</h2>"
        f"<p class='muted'>{_('Pour un(e) {item} :').format(item=escape(item.name))}</p>"
        f"<table>{rows}</table>"
        f"<p class='muted'>{caveat}</p>"
    )


def _name_of(game_data: GameData, item_class: str) -> str:
    item = game_data.items.get(item_class)
    return item.name if item else item_class


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

        self.setWindowTitle(_("Fiche d'objet"))
        self.resize(620, 720)
        # Not modal: the point is to keep it open next to the canvas.
        self.setModal(False)

        self.browser = QTextBrowser(self)
        self.browser.setOpenLinks(False)
        self.browser.setOpenExternalLinks(False)
        self.browser.anchorClicked.connect(self._follow)

        self.back_button = QPushButton(_("◀ Précédent"), self)
        self.forward_button = QPushButton(_("Suivant ▶"), self)
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
        card = _("Fiche — {item}")
        self.setWindowTitle(card.format(item=item.name if item else item_class))
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
