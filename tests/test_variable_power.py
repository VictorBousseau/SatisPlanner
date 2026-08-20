"""Machines whose draw depends on what they are making.

Three of them: the Converter, the Particle Accelerator and the Quantum Encoder.
Their ``mPowerConsumption`` is **zero** and the figure lives on the recipe instead,
as a constant and a factor between which the consumption swings. This was the last
real gap in the model -- everything else the series added was catalogue data.

The shape of the fix is the point. Power becomes a property of the pair
machine-and-recipe, and a fixed nameplate on the building becomes the *particular*
case of that, exactly as a standard splitter is the case of the programmable one
where nothing has been written on any branch. Every figure below therefore has a
twin in the fixed world that must not have moved.
"""

import pytest

from satisplanner.core import engine
from satisplanner.core.graph import (
    ExternalSourceNode,
    FactoryGraph,
    MachineNode,
    OutputNode,
    draw_of,
)
from satisplanner.core.models import GameData
from satisplanner.data import db, docs_parser

CONVERTER = "Build_Converter_C"
COLLIDER = "Build_HadronCollider_C"
ENCODER = "Build_QuantumEncoder_C"
VARIABLE = (CONVERTER, COLLIDER, ENCODER)

# The Converter recipe that takes nothing at all, and the Encoder recipe that eats
# what it makes. Named once: they are the two cases this lot had to prove.
PHOTONIC = "Recipe_QuantumEnergy_C"
OSCILLATOR = "Recipe_SuperpositionOscillator_C"
PHOTONIC_MATTER = "Desc_QuantumEnergy_C"
RESIDUE = "Desc_DarkEnergy_C"

PIPE = "Build_PipelineMK2_C"
BELT = "Build_ConveyorBeltMk5_C"


@pytest.fixture(scope="module")
def catalogue() -> GameData:
    return db.load_game_data_from_file(db.default_database_path())


# --------------------------------------------------------------------------- #
# What the game files say
# --------------------------------------------------------------------------- #


def test_the_three_declare_no_power_of_their_own(catalogue: GameData) -> None:
    """Which is why the model had nowhere to put their figure until now."""
    for class_name in VARIABLE:
        assert catalogue.buildings[class_name].power_mw == 0


def test_they_overclock_exactly_like_every_other_machine(catalogue: GameData) -> None:
    """Asked for explicitly, and the answer is that there is nothing to report.

    The exponent is the same 1,321929 the whole catalogue uses. Overclocking is
    priced by the machine and not by what it is making, so the variable draw
    changes the base and leaves the law alone.
    """
    ordinary = catalogue.buildings["Build_ManufacturerMk1_C"].power_exponent
    assert ordinary == pytest.approx(1.321929)
    for class_name in VARIABLE:
        assert catalogue.buildings[class_name].power_exponent == ordinary


def test_the_steady_figure_is_the_middle_of_the_swing(catalogue: GameData) -> None:
    """The reduction, stated as a test because it is a decision and not a reading.

    The data gives two ends and no nominal. This engine solves a steady state and
    has no notion of time, so one figure is all it can hold; the midpoint is the
    mean of any oscillation symmetric about its middle, and two parameters with no
    third to describe a shape say symmetric.
    """
    encoder = catalogue.recipes[OSCILLATOR]
    assert encoder.power_range_mw == (0.0, 2000.0)
    assert encoder.power_mw == 1000.0

    converter = catalogue.recipes[PHOTONIC]
    assert converter.power_range_mw == (100.0, 400.0)
    assert converter.power_mw == 250.0


def test_the_accelerator_is_why_this_had_to_be_per_recipe(catalogue: GameData) -> None:
    """The other two are uniform; this one is not, and one figure would be wrong.

    Every Converter recipe swings 100 to 400 and every Encoder recipe 0 to 2000, so
    a per-building figure would have served them. The Particle Accelerator has two
    tiers -- diamonds at 500 MW, dark matter at 1000 -- and no figure on the
    building can be right for both.
    """
    made = [r for r in catalogue.recipes.values() if r.building_class == COLLIDER]
    assert {recipe.power_mw for recipe in made} == {500.0, 1000.0}


def test_the_declared_range_agrees_with_the_recipes(catalogue: GameData) -> None:
    """Two independent statements in the game files, held against each other.

    A variable-power machine announces the span it expects to draw; its recipes each
    announce their own. The building's floor must be the lowest constant among them
    and its ceiling the highest sum. Nothing forces the two to agree in the files,
    which is what makes the agreement worth checking -- it is how a misread field
    would show up rather than shipping a bill nobody can trace.
    """
    expected = {CONVERTER: (100.0, 400.0), COLLIDER: (250.0, 1500.0), ENCODER: (0.0, 2000.0)}
    for class_name, declared in expected.items():
        made = [r for r in catalogue.recipes.values() if r.building_class == class_name]
        low = min(recipe.power_range_mw[0] for recipe in made)
        high = max(recipe.power_range_mw[1] for recipe in made)
        assert (low, high) == declared, class_name


# --------------------------------------------------------------------------- #
# The fixed case, which must not have moved
# --------------------------------------------------------------------------- #


def test_a_machine_with_a_nameplate_is_priced_by_its_nameplate(catalogue: GameData) -> None:
    node = MachineNode(id="four", recipe_class="Recipe_IngotIron_C")
    assert draw_of(node, "Build_SmelterMk1_C", 1.0, catalogue) == 4.0


def test_no_recipe_of_a_fixed_machine_carries_a_draw(catalogue: GameData) -> None:
    """The game writes a vestigial factor of one on ordinary recipes; it is not a draw."""
    for recipe in catalogue.recipes.values():
        if recipe.building_class not in VARIABLE:
            assert not recipe.has_own_power, recipe.class_name


def test_only_the_three_have_recipes_that_carry_one(catalogue: GameData) -> None:
    carriers = {r.building_class for r in catalogue.recipes.values() if r.has_own_power}
    assert carriers == set(VARIABLE)


# --------------------------------------------------------------------------- #
# What the engine bills
# --------------------------------------------------------------------------- #


def test_the_recipe_is_what_prices_the_machine(catalogue: GameData) -> None:
    node = MachineNode(id="encodeur", recipe_class=OSCILLATOR)
    assert draw_of(node, ENCODER, 1.0, catalogue) == 1000.0


def test_overclocking_raises_the_recipe_figure_by_the_building_exponent(
    catalogue: GameData,
) -> None:
    node = MachineNode(id="encodeur", recipe_class=OSCILLATOR, clock_speed=2.5)
    exponent = catalogue.buildings[ENCODER].power_exponent
    assert draw_of(node, ENCODER, 2.5, catalogue) == pytest.approx(1000.0 * 2.5**exponent)


def test_two_recipes_in_the_same_machine_do_not_cost_the_same(catalogue: GameData) -> None:
    """The whole reason the figure could not stay on the building."""
    diamonds = MachineNode(id="a", recipe_class="Recipe_Diamond_C")
    dark = MachineNode(id="b", recipe_class="Recipe_DarkMatter_C")
    assert draw_of(diamonds, COLLIDER, 1.0, catalogue) == 500.0
    assert draw_of(dark, COLLIDER, 1.0, catalogue) == 1000.0


# --------------------------------------------------------------------------- #
# The machine that takes nothing
# --------------------------------------------------------------------------- #


def test_a_recipe_with_no_ingredient_is_not_a_starved_machine(catalogue: GameData) -> None:
    """Excited photonic matter comes out of a Converter fed with nothing at all.

    Nothing in the game did that before, so "no input" and "no input satisfied"
    had never had to be told apart. A machine waiting on an empty list is waiting
    on nothing, and runs.
    """
    graph = FactoryGraph()
    graph.add_node(MachineNode(id="conv", recipe_class=PHOTONIC, machine_count=1))
    graph.add_node(OutputNode(id="sortie", item_class=PHOTONIC_MATTER))
    graph.connect("conv", "sortie", PHOTONIC_MATTER, PIPE, game_data=catalogue)

    report = engine.solve(graph, catalogue)
    solution = next(node for node in report.nodes if node.node_id == "conv")
    assert solution.ratio == 1.0
    assert solution.starved_items == ()
    assert solution.outputs[PHOTONIC_MATTER] == pytest.approx(200.0)
    assert solution.power_mw == 250.0


# --------------------------------------------------------------------------- #
# The two machines that only work together
# --------------------------------------------------------------------------- #


def quantum_chain(catalogue: GameData, *, flare: bool) -> FactoryGraph:
    """A Converter feeding an Encoder, which is the only way either of them runs.

    Every Quantum Encoder recipe eats excited photonic matter, and only the
    Converter makes it. Every one of them also returns the same volume as dark
    matter residue, so an Encoder with nowhere to send it is a machine with a
    blocked byproduct -- ``flare`` is what decides which of the two this is.
    """
    graph = FactoryGraph()
    graph.add_node(MachineNode(id="conv", recipe_class=PHOTONIC, machine_count=0.625))
    graph.add_node(MachineNode(id="enco", recipe_class=OSCILLATOR, machine_count=1))
    graph.connect("conv", "enco", PHOTONIC_MATTER, PIPE, game_data=catalogue)
    for item, rate in (
        ("Desc_DarkMatter_C", 30.0),
        ("Desc_CrystalOscillator_C", 5.0),
        ("Desc_AluminumPlate_C", 45.0),
    ):
        graph.add_node(ExternalSourceNode(id=f"e{item}", item_class=item, rate_per_minute=rate))
        graph.connect(f"e{item}", "enco", item, BELT, game_data=catalogue)
    graph.add_node(OutputNode(id="sortie", item_class="Desc_QuantumOscillator_C"))
    graph.connect("enco", "sortie", "Desc_QuantumOscillator_C", BELT, game_data=catalogue)
    if flare:
        graph.add_node(OutputNode(id="torchere", item_class=RESIDUE, is_sink=True))
        graph.connect("enco", "torchere", RESIDUE, PIPE, game_data=catalogue)
    return graph


def test_the_converter_feeds_the_encoder_and_both_run(catalogue: GameData) -> None:
    report = engine.solve(quantum_chain(catalogue, flare=True), catalogue)
    solutions = {node.node_id: node for node in report.nodes}
    assert solutions["conv"].ratio == 1.0
    assert solutions["enco"].ratio == 1.0
    assert report.final_outputs["Desc_QuantumOscillator_C"] == pytest.approx(5.0)
    assert report.discarded_outputs[RESIDUE] == pytest.approx(125.0)


def test_the_bill_of_the_pair_is_the_two_recipes(catalogue: GameData) -> None:
    """1000 for the Encoder, and 250 times five eighths of a Converter."""
    report = engine.solve(quantum_chain(catalogue, flare=True), catalogue)
    assert report.power_total_mw == pytest.approx(1000.0 + 250.0 * 0.625)


def test_an_encoder_with_nowhere_to_put_its_residue_stops(catalogue: GameData) -> None:
    """The byproduct rule, on a machine that returns as much as it swallows."""
    report = engine.solve(quantum_chain(catalogue, flare=False), catalogue)
    solutions = {node.node_id: node for node in report.nodes}
    assert solutions["enco"].ratio == 0.0
    assert solutions["enco"].blocked_products == (RESIDUE,)
    # And the Converter behind it backs up rather than pouring into a dead end.
    assert solutions["conv"].ratio == 0.0


def test_a_stopped_encoder_still_costs_what_it_costs(catalogue: GameData) -> None:
    """Consumption does not follow the operating ratio; production does.

    A machine standing idle still draws its share, which is the physical asymmetry
    the engine has always modelled. It is worth a test here because the figure is
    now a gigawatt: a blocked Encoder is the most expensive way in this catalogue
    to produce nothing at all.
    """
    stalled = engine.solve(quantum_chain(catalogue, flare=False), catalogue)
    running = engine.solve(quantum_chain(catalogue, flare=True), catalogue)
    assert stalled.power_total_mw == running.power_total_mw
    assert not stalled.final_outputs


# --------------------------------------------------------------------------- #
# What the game data gets wrong, and what this parser does about it
# --------------------------------------------------------------------------- #


def test_a_draw_written_on_a_machine_that_prices_itself_is_refused() -> None:
    """Two recipes in Satisfactory 1.2 carry a swing their machine does not use.

    The Biochemical Sculptor at the Blender and the Ballistic Warp Drive at the
    Manufacturer both declare 500 + 1000. Neither machine is a variable-power one,
    so the game ignores the fields -- and believing them would bill a Blender at a
    gigawatt instead of the seventy-five megawatts it draws.

    The field is dropped and the recipe is **named**, which is the same rule this
    project applies to any figure that contradicts another: report, never correct
    in silence.
    """
    warnings: list[str] = []
    entry = {
        "ClassName": "Recipe_SpaceElevatorPart_10_C",
        "mVariablePowerConsumptionConstant": "500.000000",
        "mVariablePowerConsumptionFactor": "1000.000000",
    }
    assert docs_parser._variable_power(entry, "Build_Blender_C", frozenset(), warnings) == (
        0.0,
        0.0,
    )
    assert len(warnings) == 1
    assert "Recipe_SpaceElevatorPart_10_C" in warnings[0]
    assert "Build_Blender_C" in warnings[0]


def test_the_vestigial_factor_of_one_is_not_a_draw() -> None:
    """Every ordinary recipe carries constant 0 and factor 1. That is "nothing"."""
    warnings: list[str] = []
    entry = {
        "ClassName": "Recipe_IngotIron_C",
        "mVariablePowerConsumptionConstant": "0.000000",
        "mVariablePowerConsumptionFactor": "1.000000",
    }
    assert docs_parser._variable_power(entry, "Build_SmelterMk1_C", frozenset(), warnings) == (
        0.0,
        0.0,
    )
    assert warnings == []


def test_a_variable_machine_whose_recipe_says_nothing_is_named() -> None:
    """Otherwise it would silently become a machine that runs on no power at all."""
    warnings: list[str] = []
    entry = {"ClassName": "Recipe_Imaginaire_C"}
    assert docs_parser._variable_power(entry, CONVERTER, frozenset({CONVERTER}), warnings) == (
        0.0,
        0.0,
    )
    assert len(warnings) == 1
    assert "consommation nulle" in warnings[0]


def test_every_machine_the_game_manufactures_in_is_in_the_catalogue(
    catalogue: GameData,
) -> None:
    """The end of the series: 291 recipes, and none left waiting on a machine."""
    assert len(catalogue.recipes) == 291
    assert not docs_parser.EXCLUDED_MACHINES
    assert len(catalogue.buildings) >= len(docs_parser.PRODUCTION_MACHINES)
