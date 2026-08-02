"""The generator, and above all the cross-check that says it is right.

One test here matters more than the rest. A generated factory is **solved by the
engine** and its raw consumption compared to what :mod:`satisplanner.core.breakdown`
says one unit of the target costs. The two answers come from code that shares
nothing: one expands a recipe tree and multiplies, the other lays out machines and
belts and finds a fixed point over them. Agreeing to the last decimal on four items
of four different depths is not a coincidence either of them could produce alone.

Everything else is what the specification asks the generator not to get wrong: a
byproduct with no exit, a rounding that starves the chain it was meant to make
buildable, a factory that breaks the port rule the rest of the application
enforces, and a target that cannot be built at all.
"""

import math

import pytest

from satisplanner.core import breakdown, engine, planner
from satisplanner.core.graph import (
    FactoryGraph,
    MachineNode,
    MergerNode,
    OutputNode,
    ResourceNode,
    SplitterNode,
    StorageNode,
    port_line_budget,
)
from satisplanner.core.models import GameData, Purity
from satisplanner.core.results import FactoryReport

# Four depths, from a two-step chain to a seven-level one.
TARGETS: list[tuple[str, float]] = [
    ("Desc_Cable_C", 60.0),
    ("Desc_IronPlateReinforced_C", 10.0),
    ("Desc_Computer_C", 5.0),
    ("Desc_ModularFrameHeavy_C", 2.0),
]


def consumed(report: FactoryReport) -> dict[str, float]:
    """Raw resources the solved factory really eats, solids and fluids together."""
    return {
        name: round(rate, 6)
        for name, rate in sorted({**report.raw_solids, **report.raw_fluids}.items())
    }


def port_breaches(graph: FactoryGraph) -> list[str]:
    """Nodes with more lines on a port than the port has room for."""
    found: list[str] = []
    for node in graph.sorted_nodes():
        for is_output in (True, False):
            budget = port_line_budget(node, is_output=is_output)
            if budget is None:
                continue
            lines = graph.outgoing(node.id) if is_output else graph.incoming(node.id)
            counted: dict[str, int] = {}
            for edge in lines:
                counted[edge.item_class] = counted.get(edge.item_class, 0) + 1
            if counted and max(counted.values()) > budget:
                found.append(node.id)
    return found


# --------------------------------------------------------------------------- #
# The cross-check
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(("item_class", "rate"), TARGETS)
def test_a_generated_factory_eats_exactly_what_the_card_says(
    game_data: GameData, item_class: str, rate: float
) -> None:
    """Two independent calculations of the same quantity, held against each other.

    The card multiplies a recipe tree; the engine solves a factory of machines and
    belts. Neither knows about the other. If the generator sized a bank wrongly, or
    dropped an ingredient, or wired something to the wrong place, the two figures
    part company -- and no amount of internal consistency would hide it.
    """
    made = planner.plan(game_data, item_class, rate)
    graph = planner.build(game_data, made)
    report = engine.solve(graph, game_data)

    cost = breakdown.raw_cost(game_data, item_class)
    assert cost.is_complete, "le coût brut de la fiche doit être calculable"
    expected = {name: round(amount * rate, 6) for name, amount in cost.amounts.items()}
    assert consumed(report) == expected


@pytest.mark.parametrize(("item_class", "rate"), TARGETS)
def test_a_generated_factory_runs_at_full_speed_with_nothing_to_report(
    game_data: GameData, item_class: str, rate: float
) -> None:
    """Everything at a hundred per cent and not one finding.

    A generator that produced a factory needing a fix would be asking the user to
    do the part it was written to do.
    """
    graph = planner.build(game_data, planner.plan(game_data, item_class, rate))
    report = engine.solve(graph, game_data)

    assert report.converged
    assert {round(node.ratio, 9) for node in report.nodes} == {1.0}
    assert report.diagnostics == ()
    assert report.final_outputs[item_class] == pytest.approx(rate)


@pytest.mark.parametrize(("item_class", "rate"), TARGETS)
def test_a_generated_factory_obeys_the_port_rule(
    game_data: GameData, item_class: str, rate: float
) -> None:
    """A generated factory that broke the rule would be an admission it is decorative."""
    for rounded in (False, True):
        graph = planner.build(game_data, planner.plan(game_data, item_class, rate, rounded=rounded))
        assert port_breaches(graph) == [], f"{item_class}, arrondi={rounded}"


# --------------------------------------------------------------------------- #
# Byproducts
# --------------------------------------------------------------------------- #


def test_a_byproduct_gets_an_exit_or_the_whole_chain_stops(game_data: GameData) -> None:
    """The computer chain makes plastic, and plastic comes with heavy oil residue.

    In this engine a product with nowhere to go stops its machine dead, so a
    generator that left the residue dangling would hand back a factory computing
    zeroes -- which is exactly what it did before this test existed.
    """
    made = planner.plan(game_data, "Desc_Computer_C", 5.0)
    assert "Desc_HeavyOilResidue_C" in made.byproducts

    graph = planner.build(game_data, made)
    report = engine.solve(graph, game_data)
    exits = [node for node in graph.nodes if isinstance(node, OutputNode)]
    assert {node.item_class for node in exits} == {"Desc_Computer_C", "Desc_HeavyOilResidue_C"}
    assert report.node("plastique").ratio == 1.0
    assert report.final_outputs["Desc_HeavyOilResidue_C"] > 0


# --------------------------------------------------------------------------- #
# The two variants
# --------------------------------------------------------------------------- #


def test_the_exact_variant_leaves_the_machine_counts_fractional(game_data: GameData) -> None:
    """Decimal counts, and the whole number to build written beside them."""
    made = planner.plan(game_data, "Desc_Computer_C", 5.0)
    wire = made.step("Desc_Wire_C")
    assert wire.machines == pytest.approx(8 / 3)
    assert math.ceil(wire.machines) == 3
    assert wire.surplus_per_minute == 0.0


def test_the_rounded_variant_is_buildable_and_still_makes_the_target(
    game_data: GameData,
) -> None:
    """Rounding up is not a final pass on the figures: it descends with the demand.

    A bank rounded up eats more than the exact plan gave it. Round only at the end
    and every bank above is under-sized, so the factory that was supposed to be
    buildable as it stands comes out starved from one end to the other -- which is
    what this pins down.
    """
    made = planner.plan(game_data, "Desc_Computer_C", 5.0, rounded=True)
    assert all(step.machines == int(step.machines) for step in made.steps if not step.is_source)

    report = engine.solve(planner.build(game_data, made), game_data)
    assert report.final_outputs["Desc_Computer_C"] >= 5.0
    # Nothing is starved: every machine has what it needs.
    assert not [item for item in report.diagnostics if item.severity.value == "warning"]


def test_a_container_goes_where_the_rounding_creates_a_surplus_and_nowhere_else(
    game_data: GameData,
) -> None:
    """Both halves of the claim, on two targets chosen because they differ.

    Reinforced iron plates come out in whole machines on their own, so the rounded
    variant has nothing to store; the computer does not, and every one of its
    surpluses gets a container -- one each, not one everywhere.
    """
    exact = planner.build(game_data, planner.plan(game_data, "Desc_Computer_C", 5.0))
    assert not [node for node in exact.nodes if isinstance(node, StorageNode)]

    made = planner.plan(game_data, "Desc_Computer_C", 5.0, rounded=True)
    graph = planner.build(game_data, made)
    stored = {
        node.item_class for node in graph.nodes if isinstance(node, StorageNode)
    }
    spare = {step.item_class for step in made.steps if step.surplus_per_minute > 1e-9}
    assert stored == spare
    assert stored, "le chemin de l'ordinateur ne tombe pas juste"

    tidy = planner.build(
        game_data, planner.plan(game_data, "Desc_IronPlateReinforced_C", 10.0, rounded=True)
    )
    assert not [node for node in tidy.nodes if isinstance(node, StorageNode)]


# --------------------------------------------------------------------------- #
# Recipes, and what the tool cannot know
# --------------------------------------------------------------------------- #


def test_pinning_an_alternate_recipe_changes_the_tree(game_data: GameData) -> None:
    """Where the alternates come in: by decision, never by calculation."""
    plain = planner.plan(game_data, "Desc_Plastic_C", 60.0)
    assert plain.step("Desc_Plastic_C").recipe_class == "Recipe_Plastic_C"
    assert set(plain.raw) == {"Desc_LiquidOil_C"}

    chosen = planner.plan(
        game_data,
        "Desc_Plastic_C",
        60.0,
        {"Desc_Plastic_C": "Recipe_Alternate_Plastic_1_C"},
    )
    assert chosen.step("Desc_Plastic_C").recipe_class == "Recipe_Alternate_Plastic_1_C"
    # A different tree: recycled plastic is made of rubber and fuel, so the chain
    # gains two stages the standard one never had.
    assert {step.item_class for step in plain.steps} < {step.item_class for step in chosen.steps}
    assert "Desc_Rubber_C" in {step.item_class for step in chosen.steps}
    assert chosen.byproducts != plain.byproducts
    # And the factory it draws is still right: the cross-check follows the choice.
    report = engine.solve(planner.build(game_data, chosen), game_data)
    assert {round(node.ratio, 9) for node in report.nodes} == {1.0}


def test_a_recipe_that_does_not_make_the_item_is_refused(game_data: GameData) -> None:
    with pytest.raises(planner.PlanError, match="ne produit pas"):
        planner.plan(game_data, "Desc_IronPlate_C", 60.0, {"Desc_IronPlate_C": "Recipe_Cable_C"})


def test_a_deposit_is_placed_at_a_stated_default_and_reported(game_data: GameData) -> None:
    """The one thing no catalogue holds is what is on the player's map.

    So the deposit is placed at a value that is stated rather than guessed, and the
    generation report names it. Silence here would let a figure nobody verified
    pass for one that was.
    """
    made = planner.plan(game_data, "Desc_IronPlate_C", 60.0)
    assert made.to_settle == ("Desc_OreIron_C",)

    graph = planner.build(game_data, made)
    (deposit,) = [node for node in graph.nodes if isinstance(node, ResourceNode)]
    assert deposit.purity is Purity.NORMAL
    assert deposit.extractor_class == "Build_MinerMk1_C"

    written = " ".join(planner.report(game_data, made, rounded=False))
    assert "À RÉGLER" in written
    assert "Minerai de fer" in written


def test_an_item_nothing_makes_is_refused_with_its_reason(game_data: GameData) -> None:
    with pytest.raises(planner.PlanError, match="ne se fabrique pas"):
        planner.plan(game_data, "Desc_OreIron_C", 60.0)
    with pytest.raises(planner.PlanError, match="objet inconnu"):
        planner.plan(game_data, "Desc_Inexistant_C", 60.0)
    with pytest.raises(planner.PlanError, match="positif"):
        planner.plan(game_data, "Desc_IronPlate_C", 0.0)


def test_recipes_that_make_each_other_are_named_rather_than_expanded(
    game_data: GameData,
) -> None:
    """Recycled plastic out of rubber and recycled rubber out of plastic.

    A user may pin both, and the honest answer is the ring rather than a recursion
    that never ends.
    """
    with pytest.raises(planner.PlanError, match="l'une l'autre"):
        planner.plan(
            game_data,
            "Desc_Plastic_C",
            60.0,
            {
                "Desc_Plastic_C": "Recipe_Alternate_Plastic_1_C",
                "Desc_Rubber_C": "Recipe_Alternate_RecycledRubber_C",
            },
        )


# --------------------------------------------------------------------------- #
# What comes back is an ordinary factory
# --------------------------------------------------------------------------- #


def test_the_result_is_a_factory_like_any_other(game_data: GameData) -> None:
    """No generated mode to leave, and nothing on the canvas of a second kind."""
    graph = planner.build(game_data, planner.plan(game_data, "Desc_Computer_C", 5.0))
    kinds = {type(node) for node in graph.nodes}
    assert kinds <= {MachineNode, ResourceNode, OutputNode, SplitterNode, MergerNode, StorageNode}
    # It round-trips through the ordinary document format, unchanged.
    assert FactoryGraph.model_validate_json(graph.model_dump_json()) == graph


def test_the_layout_puts_each_stage_in_its_own_column(game_data: GameData) -> None:
    """Automatic and readable, which for a chain of recipes means by level.

    Checked on the columns rather than on the pixels: what must hold is that an
    ingredient is drawn to the left of what it goes into, and that two nodes never
    land on the same spot.
    """
    made = planner.plan(game_data, "Desc_ModularFrameHeavy_C", 2.0)
    graph = planner.build(game_data, made)
    column = {
        step.item_class: graph.node(planner.node_id_for(game_data, step)).position[0]
        for step in made.steps
    }
    for step in made.steps:
        if step.recipe_class is None:
            continue
        for ingredient in game_data.recipes[step.recipe_class].ingredient_rates():
            if ingredient not in column:
                continue
            assert column[ingredient] < column[step.item_class], (
                f"{ingredient} doit être dessiné à gauche de {step.item_class}"
            )

    places = [node.position for node in graph.sorted_nodes()]
    assert len(set(places)) == len(places), "deux nœuds au même endroit"
