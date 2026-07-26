"""Over- and underclocking: throughput, the power bill, and the shards it costs.

Two things are checked that a reading of the code would not catch. The first is the
**control value the user set**: a Mk.3 miner on a pure node at 250 % puts out exactly
1200 a minute, which is exactly what a Mk.6 conveyor carries. The second is that a
clock of 100 % leaves every existing answer untouched, on every graph fixture -- the
whole of this feature is meant to be invisible until someone asks for it.
"""

import pytest
from pydantic import ValidationError

from satisplanner.core import constants, engine
from satisplanner.core.graph import (
    FactoryGraph,
    MachineNode,
    OutputNode,
    ResourceNode,
    WaterExtractorNode,
)
from satisplanner.core.models import GameData, Purity
from tests.conftest import load_graph

# Every graph fixture, so "100 % changes nothing" is asserted on all of them.
GRAPH_FIXTURES = [
    "allocation",
    "allocation_redistribution",
    "backpressure",
    "belt_saturation",
    "blocked_byproduct",
    "buffer_draining",
    "buffer_filling",
    "computer_chain",
    "deficit",
    "iron_plate",
    "pipe_saturation",
    "plastic_chain",
    "recycling_loop",
    "screws_reinforced_plate",
    "steel_chain",
]


def deposit_graph(clock_speed: float, purity: Purity = Purity.PURE) -> FactoryGraph:
    """A Mk.3 miner on one deposit, feeding an exit that absorbs everything."""
    graph = FactoryGraph()
    graph.add_node(
        ResourceNode(
            id="gisement",
            item_class="Desc_OreIron_C",
            extractor_class="Build_MinerMk3_C",
            purity=purity,
            clock_speed=clock_speed,
        )
    )
    graph.add_node(OutputNode(id="sortie", item_class="Desc_OreIron_C"))
    return graph


def connected(graph: FactoryGraph, game_data: GameData, transport: str) -> FactoryGraph:
    graph.connect("gisement", "sortie", "Desc_OreIron_C", transport, game_data)
    return graph


# --------------------------------------------------------------------- rates


def test_a_mk3_miner_on_a_pure_node_at_250_percent_puts_out_1200(game_data: GameData) -> None:
    """The control value: 480 a minute at 100 %, and 1200 at 250 %.

    Which is, to the item, what a Mk.6 conveyor carries -- the reason the number is
    worth locking down.
    """
    graph = connected(deposit_graph(2.5), game_data, "Build_ConveyorBeltMk6_C")
    report = engine.solve(graph, game_data)

    assert report.node("gisement").outputs["Desc_OreIron_C"] == pytest.approx(1200.0)
    assert game_data.transport_capacity("Build_ConveyorBeltMk6_C") == pytest.approx(1200.0)
    assert not report.edge("e1").is_saturated, "1200 sur un Mk.6 : plein, pas insuffisant"


def test_the_same_miner_at_100_percent_puts_out_480(game_data: GameData) -> None:
    graph = connected(deposit_graph(1.0), game_data, "Build_ConveyorBeltMk6_C")
    report = engine.solve(graph, game_data)
    assert report.node("gisement").outputs["Desc_OreIron_C"] == pytest.approx(480.0)


def test_underclocking_scales_down_in_proportion(game_data: GameData) -> None:
    graph = connected(deposit_graph(0.5), game_data, "Build_ConveyorBeltMk6_C")
    report = engine.solve(graph, game_data)
    assert report.node("gisement").outputs["Desc_OreIron_C"] == pytest.approx(240.0)


def test_a_machine_eats_and_makes_more_when_overclocked(game_data: GameData) -> None:
    """Both sides of a recipe scale: an overclocked smelter is hungrier too."""
    graph = FactoryGraph()
    graph.add_node(MachineNode(id="fonderie", recipe_class="Recipe_IngotIron_C", clock_speed=2.0))
    graph.add_node(
        ResourceNode(id="mine", item_class="Desc_OreIron_C", extractor_class="Build_MinerMk3_C")
    )
    graph.add_node(OutputNode(id="sortie", item_class="Desc_IronIngot_C"))
    graph.connect("mine", "fonderie", "Desc_OreIron_C", "Build_ConveyorBeltMk6_C", game_data)
    graph.connect("fonderie", "sortie", "Desc_IronIngot_C", "Build_ConveyorBeltMk6_C", game_data)

    solution = engine.solve(graph, game_data).node("fonderie")
    # One smelter at 100 % takes 30 ore and makes 30 ingots; at 200 %, double.
    assert solution.inputs["Desc_OreIron_C"] == pytest.approx(60.0)
    assert solution.outputs["Desc_IronIngot_C"] == pytest.approx(60.0)
    assert solution.ratio == pytest.approx(1.0)


def test_a_water_pump_follows_its_clock_too(game_data: GameData) -> None:
    graph = FactoryGraph()
    graph.add_node(
        WaterExtractorNode(id="pompe", extractor_class="Build_WaterPump_C", clock_speed=1.5)
    )
    graph.add_node(OutputNode(id="sortie", item_class="Desc_Water_C"))
    graph.connect("pompe", "sortie", "Desc_Water_C", "Build_PipelineMK2_C", game_data)

    nominal = game_data.extractor("Build_WaterPump_C").rate_per_minute
    solution = engine.solve(graph, game_data).node("pompe")
    assert solution.outputs["Desc_Water_C"] == pytest.approx(nominal * 1.5)


# --------------------------------------------------------------------- power


def test_power_follows_the_exponent_read_from_the_data(game_data: GameData) -> None:
    """Not proportional: the draw is raised to the building's own exponent."""
    building = game_data.building("Build_MinerMk3_C")
    assert building.power_exponent > 1.0, "l'exposant doit venir des donnees, pas valoir 1"

    graph = connected(deposit_graph(2.5), game_data, "Build_ConveyorBeltMk6_C")
    solution = engine.solve(graph, game_data).node("gisement")

    expected = building.power_mw * 2.5**building.power_exponent
    assert solution.power_mw == pytest.approx(expected)
    # The order of magnitude the specification quotes: about 3.36 times nominal.
    assert solution.power_mw / building.power_mw == pytest.approx(3.36, abs=0.01)


def test_underclocking_saves_more_than_proportionally(game_data: GameData) -> None:
    """The other end of the same curve, and the reason people underclock at all."""
    building = game_data.building("Build_MinerMk3_C")
    graph = connected(deposit_graph(0.5), game_data, "Build_ConveyorBeltMk6_C")
    solution = engine.solve(graph, game_data).node("gisement")

    assert solution.power_mw < building.power_mw * 0.5
    assert solution.power_mw == pytest.approx(building.power_mw * 0.5**building.power_exponent)


def test_doubling_the_clock_costs_two_and_a_half_times(game_data: GameData) -> None:
    """A consequence of the exponent the game chose, worth pinning down."""
    building = game_data.building("Build_SmelterMk1_C")
    assert building.power_at(2.0) == pytest.approx(building.power_mw * 2.5, rel=1e-6)


def test_an_idle_overclocked_machine_still_draws_its_power(game_data: GameData) -> None:
    """It is built and it is powered, whether or not anything reaches it."""
    graph = FactoryGraph()
    graph.add_node(MachineNode(id="fonderie", recipe_class="Recipe_IngotIron_C", clock_speed=2.0))
    graph.add_node(OutputNode(id="sortie", item_class="Desc_IronIngot_C"))
    graph.connect("fonderie", "sortie", "Desc_IronIngot_C", "Build_ConveyorBeltMk1_C", game_data)

    solution = engine.solve(graph, game_data).node("fonderie")
    assert solution.ratio == pytest.approx(0.0), "rien n'arrive"
    building = game_data.building("Build_SmelterMk1_C")
    assert solution.power_mw == pytest.approx(building.power_at(2.0))


# -------------------------------------------------------------------- shards


@pytest.mark.parametrize(
    ("clock_speed", "expected"),
    [(1.0, 0), (1.5, 1), (2.0, 2), (2.5, 3), (1.01, 1)],
)
def test_shards_needed_per_machine(game_data: GameData, clock_speed: float, expected: int) -> None:
    shard = game_data.overclock_shard()
    assert shard is not None, "le catalogue doit porter l'eclat de surcadencage"
    assert shard.shards_for(clock_speed) == expected


def test_shards_are_counted_per_whole_machine(game_data: GameData) -> None:
    """Half a machine at 150 % is still a machine, and it still takes a shard."""
    assert game_data.shards_for(1.5, 4.33) == {"Desc_CrystalShard_C": 5}
    assert game_data.shards_for(1.0, 10) == {}


def test_the_shopping_list_carries_the_shards(game_data: GameData) -> None:
    graph = connected(deposit_graph(2.5), game_data, "Build_ConveyorBeltMk6_C")
    report = engine.solve(graph, game_data)

    shopping = report.shopping_list
    assert shopping.power_shards == {"Desc_CrystalShard_C": 3}
    assert report.node("gisement").power_shards == 3
    # Consumables, not buildings: the building count must not swallow them.
    assert shopping.total_buildings == 1


def test_a_factory_at_nominal_speed_buys_no_shards(game_data: GameData) -> None:
    report = engine.solve(load_graph("iron_plate"), game_data)
    assert report.shopping_list.power_shards == {}
    assert all(node.power_shards == 0 for node in report.nodes)


def test_the_ceiling_matches_what_the_shards_can_actually_buy(game_data: GameData) -> None:
    """The constant and the data must not drift apart.

    ``MAX_CLOCK_SPEED`` is spelled out in ``constants`` because the graph validates
    its field without a catalogue. This is what keeps that number honest.
    """
    shard = game_data.overclock_shard()
    assert shard is not None
    derived = 1.0 + constants.POWER_SHARD_SLOTS * shard.extra_potential
    assert derived == pytest.approx(constants.MAX_CLOCK_SPEED)


# --------------------------------------------------------------------- range


@pytest.mark.parametrize("clock_speed", [0.0, -1.0, 0.009, 2.51, 4.0])
def test_a_clock_outside_the_game_range_is_refused(clock_speed: float) -> None:
    with pytest.raises(ValidationError):
        MachineNode(id="m", recipe_class="Recipe_IngotIron_C", clock_speed=clock_speed)


@pytest.mark.parametrize("clock_speed", [0.01, 0.5, 1.0, 2.5])
def test_the_ends_of_the_range_are_accepted(clock_speed: float) -> None:
    assert MachineNode(id="m", recipe_class="Recipe_IngotIron_C", clock_speed=clock_speed)


def test_a_node_defaults_to_a_hundred_percent() -> None:
    assert MachineNode(id="m", recipe_class="Recipe_IngotIron_C").clock_speed == 1.0
    assert ResourceNode(id="r", item_class="i", extractor_class="b").clock_speed == 1.0


def test_the_range_holds_against_assignment_too() -> None:
    """Every edit in the application sets an attribute by name, so it must.

    Without validation on assignment, a clock of 400 % would be refused when it
    comes from a file and accepted when it comes from a widget.
    """
    node = MachineNode(id="m", recipe_class="Recipe_IngotIron_C")
    with pytest.raises(ValidationError):
        node.clock_speed = 4.0
    assert node.clock_speed == 1.0, "le refus laisse la valeur en place"

    node.clock_speed = 2.5
    assert node.clock_speed == 2.5


# ------------------------------------------------------- nothing else changed


@pytest.mark.parametrize("name", GRAPH_FIXTURES)
def test_a_clock_of_one_hundred_percent_changes_nothing(game_data: GameData, name: str) -> None:
    """Stating the clock explicitly must give exactly the pre-existing answer.

    The rest of the suite already pins those answers down; what this adds is that
    the new field is genuinely inert at its default rather than merely close.
    """
    before = engine.solve(load_graph(name), game_data)

    explicit = load_graph(name)
    for node in explicit.nodes:
        if hasattr(node, "clock_speed"):
            node.clock_speed = 1.0
    after = engine.solve(explicit, game_data)

    assert after.model_dump() == before.model_dump()


def test_sizing_a_node_accounts_for_its_clock(game_data: GameData) -> None:
    """ "Adjust to inputs" answers in machines, and an overclocked machine eats more.

    60 ore a minute feeds two smelters at 100 %, and one at 200 %.
    """
    graph = FactoryGraph()
    graph.add_node(
        ResourceNode(
            id="mine",
            item_class="Desc_OreIron_C",
            extractor_class="Build_MinerMk1_C",
            purity=Purity.NORMAL,
        )
    )
    graph.add_node(MachineNode(id="fonderie", recipe_class="Recipe_IngotIron_C"))
    graph.add_node(OutputNode(id="sortie", item_class="Desc_IronIngot_C"))
    graph.connect("mine", "fonderie", "Desc_OreIron_C", "Build_ConveyorBeltMk6_C", game_data)
    graph.connect("fonderie", "sortie", "Desc_IronIngot_C", "Build_ConveyorBeltMk6_C", game_data)

    assert engine.suggest_machine_count(graph, game_data, "fonderie") == pytest.approx(2.0)

    overclocked = graph.node("fonderie")
    assert isinstance(overclocked, MachineNode)
    overclocked.clock_speed = 2.0
    assert engine.suggest_machine_count(graph, game_data, "fonderie") == pytest.approx(1.0)
