"""What a factory costs to build, and the one figure it refuses to give.

The costs are read from the game's own build recipes, so the test that matters is
the one done by hand: take a factory small enough to add up on paper, add it up,
and compare. Every other test here is about what happens when the data is *not*
there -- a building with no recipe, a line whose length nobody knows -- because
those are the cases where a total can quietly become wrong instead of absent.
"""

import pytest

from satisplanner.core import construction
from satisplanner.core.models import BuildingCost, GameData
from satisplanner.core.results import ShoppingList

# Read from the game files, and the arithmetic below is done against them by hand.
SMELTER = "Build_SmelterMk1_C"  # 5 barres de fer + 8 fils
CONSTRUCTOR = "Build_ConstructorMk1_C"  # 2 plaques renforcées + 8 câbles
SPLITTER = "Build_ConveyorAttachmentSplitter_C"  # 2 plaques de fer + 2 câbles
BELT = "Build_ConveyorBeltMk3_C"

IRON_ROD = "Desc_IronRod_C"
WIRE = "Desc_Wire_C"
CABLE = "Desc_Cable_C"
IRON_PLATE = "Desc_IronPlate_C"
REINFORCED = "Desc_IronPlateReinforced_C"


# --------------------------------------------------------------------------- #
# The catalogue really carries the costs
# --------------------------------------------------------------------------- #


def test_every_building_in_scope_has_a_build_cost(game_data: GameData) -> None:
    """If this ever fails, a building has to be **named** rather than estimated."""
    missing = sorted(set(game_data.buildings) - set(game_data.building_costs))
    assert missing == [], f"bâtiments sans recette de construction : {missing}"


def test_a_cost_is_the_recipe_the_game_declares(game_data: GameData) -> None:
    """Checked against the game files, not against the parser's own output."""
    cost = game_data.building_costs[SMELTER]
    assert cost.amounts == {IRON_ROD: 5.0, WIRE: 8.0}
    assert cost.recipe_class == "Recipe_SmelterBasicMk1_C", (
        "la recette d'origine doit rester traçable"
    )


# --------------------------------------------------------------------------- #
# The sum, done on paper first
# --------------------------------------------------------------------------- #


def test_a_factory_small_enough_to_add_up_by_hand(game_data: GameData) -> None:
    """Two smelters, three constructors, one splitter.

    Fonderie x2      : 10 barres de fer, 16 fils
    Constructeur x3  : 6 plaques renforcées, 24 câbles
    Répartiteur x1   : 2 plaques de fer, 2 câbles
    ---------------------------------------------
    câbles 26, fils 16, barres de fer 10, plaques renforcées 6, plaques de fer 2
    """
    shopping = ShoppingList(
        buildings={SMELTER: 2, CONSTRUCTOR: 3},
        attachments={SPLITTER: 1},
    )

    materials = construction.materials_for(shopping, game_data)

    assert materials.amounts == {
        CABLE: 26.0,
        IRON_PLATE: 2.0,
        IRON_ROD: 10.0,
        REINFORCED: 6.0,
        WIRE: 16.0,
    }
    assert materials.is_complete


def test_an_empty_factory_costs_nothing(game_data: GameData) -> None:
    materials = construction.materials_for(ShoppingList(), game_data)
    assert materials.amounts == {}
    assert materials.is_complete
    assert materials.line_count == 0


def test_attachments_are_priced_like_any_other_building(game_data: GameData) -> None:
    """They are deduced rather than placed, but they still have to be built."""
    alone = construction.materials_for(ShoppingList(attachments={SPLITTER: 4}), game_data)
    assert alone.amounts == {CABLE: 8.0, IRON_PLATE: 8.0}


# --------------------------------------------------------------------------- #
# What is deliberately not answered
# --------------------------------------------------------------------------- #


def test_lines_are_counted_and_left_unpriced(game_data: GameData) -> None:
    """The Phase 1 limit, stated as a number of lines rather than as silence."""
    shopping = ShoppingList(
        buildings={SMELTER: 1},
        belts_by_tier={3: 7, 1: 2},
        pipes_by_tier={2: 4},
    )

    materials = construction.materials_for(shopping, game_data)

    assert materials.belt_lines == 9
    assert materials.pipe_lines == 4
    assert materials.line_count == 13
    assert materials.amounts == {IRON_ROD: 5.0, WIRE: 8.0}, (
        "aucun coût de ligne ne doit se glisser dans le total"
    )


def test_a_line_counted_as_a_building_still_goes_to_the_blank(game_data: GameData) -> None:
    """Belts are counted by tier today. If one ever arrives by the other door, it
    must still land in the blank rather than be priced by the metre as if it were
    one metre long."""
    materials = construction.materials_for(ShoppingList(buildings={BELT: 5}), game_data)

    assert materials.amounts == {}
    assert materials.belt_lines == 5


def test_a_building_without_a_recipe_is_named_not_skipped(game_data: GameData) -> None:
    """A total that quietly omits a building is a total nobody can trust.

    The catalogue currently prices everything, so the case is built rather than
    found: the guarantee is about what the code does when a cost is missing, and
    it has to hold on the day a game update adds a building.
    """
    stripped = game_data.model_copy(
        update={
            "building_costs": {
                name: cost for name, cost in game_data.building_costs.items() if name != CONSTRUCTOR
            }
        }
    )

    materials = construction.materials_for(
        ShoppingList(buildings={SMELTER: 2, CONSTRUCTOR: 3}), stripped
    )

    assert materials.unpriced == (CONSTRUCTOR,)
    assert not materials.is_complete
    assert materials.amounts == {IRON_ROD: 10.0, WIRE: 16.0}, (
        "le reste du total reste juste ; c'est le manque qui est signale"
    )


def test_an_unknown_building_class_is_reported_rather_than_crashing(
    game_data: GameData,
) -> None:
    """A file from a newer version can name a building this catalogue has not got."""
    materials = construction.materials_for(
        ShoppingList(buildings={"Build_Inconnu_C": 2}), game_data
    )
    assert materials.unpriced == ("Build_Inconnu_C",)


# --------------------------------------------------------------------------- #
# Arithmetic that would be easy to get subtly wrong
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("count", [1, 3, 17])
def test_the_cost_is_proportional_to_the_count(game_data: GameData, count: int) -> None:
    materials = construction.materials_for(ShoppingList(buildings={SMELTER: count}), game_data)
    assert materials.amounts == {IRON_ROD: 5.0 * count, WIRE: 8.0 * count}


def test_two_buildings_sharing_an_item_add_up(game_data: GameData) -> None:
    """The whole point of aggregating: cables come from two different buildings."""
    both = construction.materials_for(
        ShoppingList(buildings={CONSTRUCTOR: 1}, attachments={SPLITTER: 1}), game_data
    )
    assert both.amounts[CABLE] == 10.0, "8 du constructeur + 2 du répartiteur"


def test_a_recipe_yielding_several_units_is_divided(game_data: GameData) -> None:
    """The parser divides by what one craft yields, so the model is per building.

    Nothing in the game does this today; the guard is here because a cost that is
    silently multiplied by two is the kind of error a total hides perfectly.
    """
    doubled = game_data.model_copy(
        update={
            "building_costs": {
                SMELTER: BuildingCost(
                    class_name=SMELTER, recipe_class="Recipe_Essai_C", amounts={IRON_ROD: 2.5}
                )
            }
        }
    )
    materials = construction.materials_for(ShoppingList(buildings={SMELTER: 4}), doubled)
    assert materials.amounts == {IRON_ROD: 10.0}
