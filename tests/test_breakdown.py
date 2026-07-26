"""Catalogue lookups behind the item card, and the raw cost's honest limits.

The cost figures are control values worked out by hand from the fixture, so a change
in how the tree is walked shows up as a wrong number rather than as a different
number.
"""

import pytest

from satisplanner.core import breakdown
from satisplanner.core.models import GameData, Item, ItemForm, Recipe, RecipeSlot


def synthetic_loop() -> GameData:
    """Two items whose standard recipes each need the other.

    The game has no such pair -- recycled plastic and rubber are both alternates --
    so the case is built rather than found.
    """

    def item(name: str) -> Item:
        return Item(
            class_name=name,
            display_name=name,
            display_name_fr=name,
            form=ItemForm.SOLID,
            stack_size=100,
            icon_file=None,
            sink_points=0,
            is_raw_resource=False,
        )

    def recipe(name: str, source: str, product: str) -> Recipe:
        return Recipe(
            class_name=name,
            display_name=name,
            display_name_fr=name,
            building_class="Build_ConstructorMk1_C",
            cycle_seconds=60.0,
            is_alternate=False,
            involves_fluid=False,
            ingredients=(RecipeSlot(item_class=source, amount_per_cycle=1, rate_per_minute=1),),
            products=(RecipeSlot(item_class=product, amount_per_cycle=1, rate_per_minute=1),),
        )

    return GameData.from_rows(
        items=[item("Desc_A_C"), item("Desc_B_C")],
        recipes=[
            recipe("Recipe_A_C", "Desc_B_C", "Desc_A_C"),
            recipe("Recipe_B_C", "Desc_A_C", "Desc_B_C"),
        ],
    )


# ------------------------------------------------------------------ lookups


def test_producers_put_the_standard_recipe_first(game_data: GameData) -> None:
    made = breakdown.producers(game_data, "Desc_IronPlateReinforced_C")
    assert made, "la plaque renforcee a au moins une recette"
    assert made[0].is_alternate is False
    assert [recipe.is_alternate for recipe in made] == sorted(
        recipe.is_alternate for recipe in made
    ), "les alternatives viennent apres, jamais entremelees"


def test_consumers_find_what_eats_an_item(game_data: GameData) -> None:
    used = {recipe.class_name for recipe in breakdown.consumers(game_data, "Desc_IronRod_C")}
    assert "Recipe_Screw_C" in used
    assert "Recipe_IronRod_C" not in used, "produire n'est pas consommer"


def test_a_raw_resource_has_no_recipe(game_data: GameData) -> None:
    assert breakdown.producers(game_data, "Desc_OreIron_C") == []
    assert breakdown.standard_recipe(game_data, "Desc_OreIron_C") is None


def test_the_standard_recipe_ignores_the_alternates(game_data: GameData) -> None:
    standard = breakdown.standard_recipe(game_data, "Desc_Plastic_C")
    assert standard is not None
    assert standard.class_name == "Recipe_Plastic_C"
    assert standard.is_alternate is False


# --------------------------------------------------------------- raw cost


def test_one_iron_plate_costs_one_and_a_half_ore(game_data: GameData) -> None:
    """Three ingots make two plates, and one ingot is one ore."""
    cost = breakdown.raw_cost(game_data, "Desc_IronPlate_C")
    assert cost.is_complete
    assert cost.amounts == {"Desc_OreIron_C": 1.5}


def test_a_reinforced_plate_costs_twelve_ore(game_data: GameData) -> None:
    """Worked out by hand: 6 plates at 1,5 plus 12 screws at 0,25.

    A screw is a quarter of a rod, a rod is an ingot, an ingot is an ore.
    """
    cost = breakdown.raw_cost(game_data, "Desc_IronPlateReinforced_C")
    assert cost.amounts == {"Desc_OreIron_C": 12.0}


def test_plastic_costs_crude_oil_and_the_byproduct_is_not_credited(game_data: GameData) -> None:
    """Three crude oil make two plastic, plus heavy oil residue nobody counts.

    The residue is worth something and this figure ignores it. That is the
    documented limit of the calculation, and it is asserted rather than glossed.
    """
    cost = breakdown.raw_cost(game_data, "Desc_Plastic_C")
    assert cost.amounts == {"Desc_LiquidOil_C": 1.5}


def test_a_raw_resource_costs_itself(game_data: GameData) -> None:
    cost = breakdown.raw_cost(game_data, "Desc_OreIron_C")
    assert cost.is_complete
    assert cost.amounts == {"Desc_OreIron_C": 1.0}


def test_an_item_with_only_alternate_recipes_stops_there(game_data: GameData) -> None:
    """Not expanded through an alternate: the item counts as its own leaf."""
    cost = breakdown.raw_cost(game_data, "Desc_LiquidTurboFuel_C")
    assert cost.is_complete
    assert cost.amounts == {"Desc_LiquidTurboFuel_C": 1.0}


def test_an_unknown_item_is_its_own_cost(game_data: GameData) -> None:
    cost = breakdown.raw_cost(game_data, "Desc_Inexistant_C")
    assert cost.amounts == {"Desc_Inexistant_C": 1.0}


def test_a_cycle_is_abandoned_and_named_rather_than_looped() -> None:
    """The requirement in as many words: abandon, and say so."""
    catalogue = synthetic_loop()
    cost = breakdown.raw_cost(catalogue, "Desc_A_C")

    assert cost.is_complete is False
    assert cost.amounts == {}, "un chiffre partiel serait pire que pas de chiffre"
    assert cost.cycle[0] == "Desc_A_C"
    assert cost.cycle[-1] == "Desc_A_C", "la chaine se referme sur son point de depart"
    assert "Desc_B_C" in cost.cycle
    assert "→" in cost.cycle_description


def test_the_cycle_guard_does_not_fire_on_a_diamond(game_data: GameData) -> None:
    """Two branches needing the same ore is not a cycle, however it looks."""
    cost = breakdown.raw_cost(game_data, "Desc_IronPlateReinforced_C")
    assert cost.is_complete


def test_a_computer_expands_through_several_layers(game_data: GameData) -> None:
    """The deepest chain the fixture has: it must terminate and reach real resources.

    Copper and crude oil, and no iron at all -- the 1.2 recipe is circuit boards,
    cables and plastic, none of which touches iron.
    """
    cost = breakdown.raw_cost(game_data, "Desc_Computer_C")
    assert cost.is_complete
    assert set(cost.amounts) == {"Desc_OreCopper_C", "Desc_LiquidOil_C"}
    assert all(amount > 0 for amount in cost.amounts.values())


def test_output_per_cycle_reads_the_right_slot(game_data: GameData) -> None:
    recipe = game_data.recipe("Recipe_Plastic_C")
    assert breakdown.output_per_cycle(recipe, "Desc_Plastic_C") == pytest.approx(2.0)
    assert breakdown.output_per_cycle(recipe, "Desc_HeavyOilResidue_C") == pytest.approx(1.0)
    assert breakdown.output_per_cycle(recipe, "Desc_OreIron_C") == 0.0
