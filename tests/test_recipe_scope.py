"""What the catalogue keeps of the game, and what it says about the rest.

The subject of these tests is an **absence**. Until the Blender lot, a recipe whose
machine was out of scope was dropped at parse time, and the item card that followed
could not tell "the game has no such recipe" from "this version cannot place it".
The encased uranium cell was the case that surfaced it: its standard recipe is a
Blender one, its alternate is a Manufacturer one, so the card showed a lone line
marked "alternate" and read as a hole in the data.

Two things are checked here and they pull in opposite directions. The catalogue must
**keep** every recipe the game has, and the engine must **never see** the ones no
node can place. One mapping each, and the tests below hold the wall between them.
"""

import pytest

from satisplanner.core import breakdown
from satisplanner.core.models import GameData, RecipeAvailability
from satisplanner.data import db


@pytest.fixture(scope="module")
def catalogue() -> GameData:
    """The database this application ships, not a fixture slice."""
    return db.load_game_data_from_file(db.default_database_path())


# --------------------------------------------------------------------------- #
# The wall between the two mappings
# --------------------------------------------------------------------------- #


def test_nothing_unplaceable_reaches_the_engine(catalogue: GameData) -> None:
    """The one guarantee that matters: a factory can only be built from `recipes`."""
    assert all(recipe.is_placeable for recipe in catalogue.recipes.values())


def test_nothing_placeable_hides_in_the_second_mapping(catalogue: GameData) -> None:
    assert all(not recipe.is_placeable for recipe in catalogue.unavailable_recipes.values())


def test_the_two_mappings_share_no_recipe(catalogue: GameData) -> None:
    assert not set(catalogue.recipes) & set(catalogue.unavailable_recipes)


def test_every_unplaceable_recipe_names_its_machine(catalogue: GameData) -> None:
    """A card that cannot name the machine explains nothing, so the row must carry it."""
    assert all(recipe.building_name_fr for recipe in catalogue.unavailable_recipes.values())


def test_every_unplaceable_recipe_still_carries_its_figures(catalogue: GameData) -> None:
    """Out of scope is not the same as unknown: the rates are the game's and are kept."""
    for recipe in catalogue.unavailable_recipes.values():
        assert recipe.cycle_seconds > 0
        assert recipe.products
        assert all(slot.rate_per_minute > 0 for slot in recipe.products)


def test_every_slot_of_every_recipe_resolves(catalogue: GameData) -> None:
    """Including the unplaceable ones, whose ingredients the card links to."""
    for recipe in (*catalogue.recipes.values(), *catalogue.unavailable_recipes.values()):
        for slot in (*recipe.ingredients, *recipe.products):
            assert slot.item_class in catalogue.items, recipe.class_name


# --------------------------------------------------------------------------- #
# The Blender
# --------------------------------------------------------------------------- #


def test_the_blender_is_in_the_catalogue(catalogue: GameData) -> None:
    blender = catalogue.buildings["Build_Blender_C"]
    assert blender.display_name_fr == "Mélangeur"
    assert blender.power_mw == 75
    # Same exponent as every other production machine: overclocking is priced the
    # same way, which is precisely why this machine needed no engine work.
    assert blender.power_exponent == catalogue.buildings["Build_ManufacturerMk1_C"].power_exponent


def test_the_blender_recipes_fit_the_slots_the_engine_already_had(
    catalogue: GameData,
) -> None:
    """Four inputs and two outputs, which is what a Manufacturer already needed."""
    made = [r for r in catalogue.recipes.values() if r.building_class == "Build_Blender_C"]
    assert len(made) == 17
    assert max(len(recipe.ingredients) for recipe in made) <= 4
    assert max(len(recipe.products) for recipe in made) <= 2


# --------------------------------------------------------------------------- #
# The case that started it
# --------------------------------------------------------------------------- #


def test_the_uranium_cell_has_both_its_recipes(catalogue: GameData) -> None:
    """Standard at the Blender, alternate at the Manufacturer, and both placeable."""
    made = breakdown.producers(catalogue, "Desc_UraniumCell_C")
    machines = {recipe.building_class for recipe in made}
    assert machines == {"Build_Blender_C", "Build_ManufacturerMk1_C"}
    assert [recipe.is_alternate for recipe in made] == [False, True]
    assert not breakdown.unavailable_producers(catalogue, "Desc_UraniumCell_C")


# --------------------------------------------------------------------------- #
# The three ways a recipe can be missing
# --------------------------------------------------------------------------- #


def test_no_recipe_waits_on_a_machine_any_more(catalogue: GameData) -> None:
    """The end of the series, stated as a test rather than as a claim.

    Plutonium pellets were the example when this file was written: nothing could
    make them, and the card said the Particle Accelerator was why. That machine is
    in the catalogue now, and so is every other one the game manufactures parts in.
    What is left unplaceable is hand crafting, which never will be anything else.
    """
    assert not any(
        recipe.availability is RecipeAvailability.MACHINE_OUT_OF_SCOPE
        for recipe in catalogue.unavailable_recipes.values()
    )
    made = breakdown.producers(catalogue, "Desc_PlutoniumPellet_C")
    assert [recipe.building_class for recipe in made] == ["Build_HadronCollider_C"]


def test_hand_crafting_is_marked_as_permanent(catalogue: GameData) -> None:
    """The equipment workshop will never be a node: it is a pair of hands."""
    by_hand = [
        recipe
        for recipe in catalogue.unavailable_recipes.values()
        if recipe.availability is RecipeAvailability.HAND_CRAFTED
    ]
    assert by_hand
    assert {recipe.building_name_fr for recipe in by_hand} == {"Atelier d'équipement"}


def test_the_frozen_label_emptied_itself_when_the_plant_arrived(
    catalogue: GameData,
) -> None:
    """``byproduct_of_fr`` was keyed on the exclusion, not on the item. Here is the proof.

    Uranium waste has no recipe at all in the game -- it drops out of a nuclear
    plant. While the plant was out of scope the item carried the building's name as
    a written label, so its card would not read as "picked up off the ground" like
    wood and leaves. The plant is in the catalogue now and the label is gone on its
    own: nothing had to be cleaned up, because it was filled from the exclusion list
    and that list is empty.

    What the card says instead is read live from the generator -- see
    :func:`~satisplanner.core.breakdown.generator_sources` -- which is a better
    answer, because it carries the rate as well as the name.
    """
    waste = catalogue.items["Desc_NuclearWaste_C"]
    assert not breakdown.producers(catalogue, waste.class_name)
    assert not breakdown.unavailable_producers(catalogue, waste.class_name)
    assert not waste.byproduct_of_fr, "l'étiquette figée doit s'être vidée d'elle-même"
    assert not [item for item in catalogue.items.values() if item.byproduct_of_fr]

    sources = breakdown.generator_sources(catalogue, waste.class_name)
    assert [generator.class_name for generator, _ in sources] == ["Build_GeneratorNuclear_C"]


def test_what_is_merely_gathered_names_nothing(catalogue: GameData) -> None:
    """And the ones that really are picked up off the ground stay silent about it."""
    for class_name in ("Desc_Wood_C", "Desc_Leaves_C", "Desc_Mycelia_C"):
        item = catalogue.items[class_name]
        assert not breakdown.producers(catalogue, class_name)
        assert item.byproduct_of_fr == ""


def test_a_raw_resource_names_nothing_either(catalogue: GameData) -> None:
    assert catalogue.items["Desc_OreIron_C"].byproduct_of_fr == ""


# --------------------------------------------------------------------------- #
# The palette, which is where a reader would try to place one
# --------------------------------------------------------------------------- #


def test_the_palette_offers_nothing_it_cannot_place(catalogue: GameData) -> None:
    """The card names the out-of-scope recipes; the palette must not offer them.

    Reading the wrong mapping here would put an entry in the drawer that the
    engine refuses on drop, which is a worse answer than the silence this lot
    replaced.
    """
    from satisplanner.ui.catalogue import EntryKind, build_entries

    offered = {
        entry.class_name
        for entry in build_entries(catalogue)
        if entry.kind is EntryKind.RECIPE
    }
    assert not offered & set(catalogue.unavailable_recipes)
    assert "Recipe_UraniumCell_C" in offered, "la recette du Mélangeur doit être posable"
