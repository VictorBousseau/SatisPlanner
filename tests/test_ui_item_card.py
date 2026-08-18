"""The item card: what the page says, and the two gestures that open it.

Links are followed by emitting ``anchorClicked``, which is the signal a
``QTextBrowser`` emits when a hyperlink is clicked. A synthetic mouse press cannot
be aimed at a link -- the widget exposes no rectangle for one -- so this is the same
allowance as the modal dialogs: the click is simulated, the code behind it is real.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import QMenu
from pytestqt.qtbot import QtBot

from satisplanner.core import breakdown
from satisplanner.core.graph import MachineNode
from satisplanner.core.models import GameData
from satisplanner.data import db
from satisplanner.ui.catalogue import EntryKind, PaletteEntry, build_entries
from satisplanner.ui.item_card import ITEM_SCHEME, PLACE_SCHEME, ItemCard, card_html
from satisplanner.ui.main_window import MainWindow
from tests.conftest import temporary_settings
from tests.test_breakdown import synthetic_loop


@pytest.fixture
def window(qtbot: QtBot, game_data: GameData, tmp_path: Path) -> Iterator[MainWindow]:
    """See ``test_ui_smoke`` for why this is not handed to ``qtbot.addWidget``."""
    del qtbot
    built = MainWindow(game_data, settings=temporary_settings(tmp_path))
    yield built
    if built.item_card is not None:
        built.item_card.close()
    built.dispose()
    built.close()
    built.deleteLater()


def entry_of(window: MainWindow, kind: EntryKind, class_name: str) -> PaletteEntry:
    return next(e for e in window.entries if e.kind is kind and e.class_name == class_name)


def click_link(card: ItemCard, scheme: str, target: str) -> None:
    card.browser.anchorClicked.emit(QUrl(f"{scheme}:{target}"))


# ------------------------------------------------------------------ the page


def test_the_page_carries_the_identity_of_the_item(game_data: GameData) -> None:
    page = card_html(game_data, "Desc_IronPlate_C")
    assert "Plaque de fer" in page
    assert "Solide" in page
    assert "pile de" in page
    assert "points au collecteur AWESOME" in page


def test_the_description_comes_from_the_game_and_only_from_it(game_data: GameData) -> None:
    """Present when the data has one, absent otherwise -- never written here."""
    item = game_data.item("Desc_IronPlate_C")
    assert item.description_fr, "la fixture doit porter une description"
    assert item.description_fr.splitlines()[0] in card_html(game_data, "Desc_IronPlate_C")

    stripped = game_data.model_copy(
        update={
            "items": {
                **game_data.items,
                "Desc_IronPlate_C": item.model_copy(update={"description_fr": ""}),
            }
        }
    )
    assert "class='desc'" not in card_html(stripped, "Desc_IronPlate_C")


def test_an_item_with_alternates_lists_them_after_the_standard_recipes(
    game_data: GameData,
) -> None:
    """Plastic has both kinds in the fixture, so the order can be read off the page."""
    made = breakdown.producers(game_data, "Desc_Plastic_C")
    assert any(recipe.is_alternate for recipe in made), "la fixture doit avoir une alternative"
    assert any(not recipe.is_alternate for recipe in made)

    page = card_html(game_data, "Desc_Plastic_C")
    positions = [(page.index(recipe.display_name_fr), recipe.is_alternate) for recipe in made]
    assert positions == sorted(positions), "les blocs suivent l'ordre annonce"
    last_standard = max(place for place, alternate in positions if not alternate)
    first_alternate = min(place for place, alternate in positions if alternate)
    assert last_standard < first_alternate


def test_a_recipe_shows_its_machine_its_cycle_and_its_power(game_data: GameData) -> None:
    page = card_html(game_data, "Desc_IronPlate_C")
    assert "Constructeur" in page
    assert "cycle de" in page
    assert "MW" in page
    assert "/ cycle" in page, "les quantités par cycle"
    assert "/min" in page, "et les débits"


def test_a_byproduct_is_named_as_one(game_data: GameData) -> None:
    page = card_html(game_data, "Desc_Plastic_C")
    assert "Sous-produit" in page
    assert "sidus de p" in page  # "Résidus de pétrole lourd", accents aside


# --------------------------------------------- what the catalogue cannot make
#
# These read the **shipped** database rather than the fixture slice: the slice
# holds no Converter recipe and no nuclear plant, and the whole point here is what
# the card says about machines this version does not model.


@pytest.fixture(scope="module")
def catalogue() -> GameData:
    return db.load_game_data_from_file(db.default_database_path())


def test_an_out_of_scope_recipe_is_shown_with_what_stops_it(catalogue: GameData) -> None:
    """The plutonium pellet: no placeable recipe, and the game's own recipe below."""
    page = card_html(catalogue, "Desc_PlutoniumPellet_C")
    assert "Aucune recette posable dans cette version" in page
    assert "Recettes hors du périmètre de cette version" in page
    assert "Accélérateur de particules" in page
    assert "machine que cette version ne modélise pas encore" in page


def test_an_out_of_scope_recipe_cannot_be_put_on_the_canvas(catalogue: GameData) -> None:
    """No button for it: offering one would promise something the engine refuses."""
    page = card_html(catalogue, "Desc_PlutoniumPellet_C")
    assert f"{PLACE_SCHEME}:" not in page


def test_the_uranium_cell_shows_its_standard_recipe_and_its_alternate(
    catalogue: GameData,
) -> None:
    """The case that started the lot, read the way a reader reads it.

    Before the Blender, this page carried one line marked "alternate" and nothing
    else, which reads as a catalogue that has lost a recipe rather than one that
    is narrower than the game.
    """
    page = card_html(catalogue, "Desc_UraniumCell_C")
    assert "Mélangeur" in page
    assert "Façonneuse" in page
    assert "Recettes hors du périmètre" not in page
    assert page.count(f"{PLACE_SCHEME}:") == 2


def test_an_item_with_no_recipe_at_all_names_the_building_it_falls_out_of(
    catalogue: GameData,
) -> None:
    page = card_html(catalogue, "Desc_NuclearWaste_C")
    assert "Aucune recette dans le jeu" in page
    assert "Centrale nucléaire" in page
    assert "se ramasse dans le monde" not in page


def test_a_gathered_item_says_it_is_gathered(catalogue: GameData) -> None:
    page = card_html(catalogue, "Desc_Wood_C")
    assert "se ramasse dans le monde" in page
    assert "C'est déjà une ressource brute" not in page


def test_only_a_raw_resource_is_called_a_raw_resource(catalogue: GameData) -> None:
    """The raw-cost section stops at anything it cannot make; it must not rename it."""
    assert "C'est déjà une ressource brute" in card_html(catalogue, "Desc_OreIron_C")
    assert "C'est déjà une ressource brute" not in card_html(catalogue, "Desc_NuclearWaste_C")


def test_an_out_of_scope_consumer_is_listed_apart(catalogue: GameData) -> None:
    """Knowing the Particle Accelerator eats this is the answer, not noise."""
    page = card_html(catalogue, "Desc_NuclearWaste_C")
    assert "Hors du périmètre de cette version :" in page
    assert "Accélérateur de particules" in page


def test_a_raw_ore_says_it_is_extracted_not_made(game_data: GameData) -> None:
    page = card_html(game_data, "Desc_OreIron_C")
    assert "Ressource brute" in page
    assert "ressource brute" in page.lower()
    assert "Recettes qui le consomment" in page


def test_the_used_in_section_names_the_machine_and_the_quantity(game_data: GameData) -> None:
    page = card_html(game_data, "Desc_IronRod_C")
    used = page[page.index("Recettes qui le consomment") :]
    assert "Vis" in used
    assert "Constructeur" in used
    assert "/ cycle" in used


def test_the_raw_cost_is_shown_and_labelled_indicative(game_data: GameData) -> None:
    page = card_html(game_data, "Desc_IronPlate_C")
    cost = page[page.index("Coût en ressources brutes") :]
    assert "Minerai de fer" in cost
    assert "1,5" in cost
    assert "Indicatif" in cost
    assert "recettes standard" in cost
    assert "sous-produits" in cost


def test_a_cycle_is_reported_instead_of_a_number() -> None:
    page = card_html(synthetic_loop(), "Desc_A_C")
    assert "abandonn" in page.lower()
    assert "Desc_A_C" in page
    assert "Desc_B_C" in page


def test_an_unknown_item_gives_a_page_and_not_a_crash(game_data: GameData) -> None:
    page = card_html(game_data, "Desc_Inexistant_C")
    assert "absent du catalogue" in page


def test_every_ingredient_is_a_link(game_data: GameData) -> None:
    """What makes the card browsable rather than a dead end."""
    page = card_html(game_data, "Desc_IronPlate_C")
    assert f"{ITEM_SCHEME}:Desc_IronIngot_C" in page
    assert f"{PLACE_SCHEME}:Recipe_IronPlate_C" in page


# ------------------------------------------------------------------- window


def test_the_card_remembers_where_the_reader_has_been(
    qtbot: QtBot, game_data: GameData, window: MainWindow
) -> None:
    del qtbot
    window.show_item_card("Desc_IronPlateReinforced_C")
    card = window.item_card
    assert card is not None
    assert card.current_item == "Desc_IronPlateReinforced_C"
    assert card.back_button.isEnabled() is False

    click_link(card, ITEM_SCHEME, "Desc_IronPlate_C")
    assert card.current_item == "Desc_IronPlate_C"
    assert card.back_button.isEnabled() is True

    assert card.go_back() is True
    assert card.current_item == "Desc_IronPlateReinforced_C"
    assert card.forward_button.isEnabled() is True

    assert card.go_forward() is True
    assert card.current_item == "Desc_IronPlate_C"
    assert card.go_forward() is False


def test_following_a_new_link_forgets_the_way_forward(window: MainWindow) -> None:
    """A browser's rule: the forward list described a path the reader has left."""
    window.show_item_card("Desc_IronPlateReinforced_C")
    card = window.item_card
    assert card is not None
    click_link(card, ITEM_SCHEME, "Desc_IronPlate_C")
    card.go_back()

    click_link(card, ITEM_SCHEME, "Desc_IronRod_C")
    assert card.current_item == "Desc_IronRod_C"
    assert card.forward_button.isEnabled() is False


def test_the_title_follows_the_item(window: MainWindow) -> None:
    window.show_item_card("Desc_IronPlate_C")
    card = window.item_card
    assert card is not None
    assert "Plaque de fer" in card.windowTitle()


def test_the_place_link_puts_the_recipe_on_the_canvas(window: MainWindow) -> None:
    window.show_item_card("Desc_IronPlate_C")
    card = window.item_card
    assert card is not None
    before = len(window.document.graph.nodes)

    click_link(card, PLACE_SCHEME, "Recipe_IronPlate_C")

    assert len(window.document.graph.nodes) == before + 1
    placed = window.document.graph.sorted_nodes()[-1]
    assert isinstance(placed, MachineNode)
    assert placed.recipe_class == "Recipe_IronPlate_C"
    assert window.document.undo_stack.count() == 1, "et c'est une seule annulation"


def test_a_link_the_page_never_writes_is_ignored(window: MainWindow) -> None:
    """The browser is told not to follow links; nothing else must sneak through."""
    window.show_item_card("Desc_IronPlate_C")
    card = window.item_card
    assert card is not None
    card.browser.anchorClicked.emit(QUrl("https://example.invalid/"))
    assert card.current_item == "Desc_IronPlate_C"


def test_the_card_is_hidden_rather_than_destroyed(window: MainWindow) -> None:
    """Closing it must not throw away the trail the reader built."""
    window.show_item_card("Desc_IronPlate_C")
    card = window.item_card
    assert card is not None
    click_link(card, ITEM_SCHEME, "Desc_IronIngot_C")
    card.close()

    window.show_item_card("Desc_IronPlate_C")
    assert window.item_card is card, "la même fenêtre est reutilisee"
    assert card.back_button.isEnabled() is True


# ----------------------------------------------------------------- gestures


def test_a_double_click_in_the_palette_opens_the_card(window: MainWindow) -> None:
    """The real path: the list's own double-click signal, on a real row."""
    window.show()
    palette = window.palette_widget
    palette.search.setText("Plaque de fer")
    index = next(
        palette.model.index(row, 0)
        for row in range(palette.model.rowCount())
        if palette.model.entry_at(palette.model.index(row, 0)) is not None
    )
    palette.list.doubleClicked.emit(index)

    assert window.item_card is not None
    assert window.item_card.isVisible()
    assert window.item_card.current_item is not None


def test_a_double_click_no_longer_places_a_node(window: MainWindow) -> None:
    """The gesture changed on purpose; the card's button covers the loss."""
    window.show()
    palette = window.palette_widget
    palette.search.setText("Plaque de fer")
    index = next(
        palette.model.index(row, 0)
        for row in range(palette.model.rowCount())
        if palette.model.entry_at(palette.model.index(row, 0)) is not None
    )
    palette.list.doubleClicked.emit(index)
    assert window.document.graph.nodes == []


def test_a_storage_entry_has_no_card_and_says_so(window: MainWindow) -> None:
    entry = entry_of(window, EntryKind.STORAGE, "Build_StorageContainerMk1_C")
    window.show_entry_card(entry)
    assert window.item_card is None
    assert "Aucune fiche" in window.statusBar().currentMessage()


def test_the_node_menu_offers_the_card_of_what_it_makes(window: MainWindow) -> None:
    """Right-click a smelter and the card offered is the ingot's, not the ore's."""
    entry = entry_of(window, EntryKind.RECIPE, "Recipe_IngotIron_C")
    window.palette_widget.entryActivated.emit(entry)
    node_id = window.document.graph.sorted_nodes()[-1].id
    item = window.scene.nodes[node_id]

    assert window.scene.headline_item(node_id) == "Desc_IronIngot_C"
    menu = window.scene.build_context_menu(item.sceneBoundingRect().center(), window)
    assert menu is not None
    card_action = next(a for a in menu.actions() if a.text().startswith("Fiche de"))
    assert "Lingot de fer" in card_action.text()

    card_action.trigger()
    assert window.item_card is not None
    assert window.item_card.current_item == "Desc_IronIngot_C"


def test_a_deposit_node_offers_the_card_of_its_ore(window: MainWindow) -> None:
    entry = entry_of(window, EntryKind.EXTRACTOR, "Desc_OreIron_C")
    window.palette_widget.entryActivated.emit(entry)
    node_id = window.document.graph.sorted_nodes()[-1].id
    assert window.scene.headline_item(node_id) == "Desc_OreIron_C"


def test_every_palette_entry_knows_what_it_is_about(game_data: GameData) -> None:
    """No entry may raise on the way to its card; a storage simply has none."""
    for entry in build_entries(game_data):
        subject = entry.subject_item(game_data)
        assert subject is None or subject in game_data.items


def test_a_menu_built_for_an_empty_spot_is_still_nothing(window: MainWindow) -> None:
    assert window.scene.build_context_menu(window.view.mapToScene(0, 0), window) is None


def test_the_menu_is_a_menu(window: MainWindow) -> None:
    entry = entry_of(window, EntryKind.OUTPUT, "Desc_IronIngot_C")
    window.palette_widget.entryActivated.emit(entry)
    node_id = window.document.graph.sorted_nodes()[-1].id
    item = window.scene.nodes[node_id]
    menu = window.scene.build_context_menu(item.sceneBoundingRect().center(), window)
    assert isinstance(menu, QMenu)
