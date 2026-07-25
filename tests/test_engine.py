"""Engine tests: every case of the specification, checked to the exact rate.

Each factory is a JSON fixture under ``tests/fixtures/graphs``. The expected numbers
are derived by hand from the control table and asserted exactly, not approximately:
a planner whose figures drift by a percent is worse than useless.
"""

import pytest

from satisplanner.core import constants, engine
from satisplanner.core.graph import (
    ExternalSourceNode,
    FactoryGraph,
    MachineNode,
    OutputNode,
    condensation_order,
)
from satisplanner.core.models import GameData
from satisplanner.core.results import (
    BufferState,
    DiagnosticCode,
    FactoryReport,
    LimitingFactor,
    Severity,
)
from tests.conftest import load_graph

TOLERANCE = 1e-9


def solved(name: str, game_data: GameData) -> FactoryReport:
    return engine.solve(load_graph(name), game_data)


def codes(report: FactoryReport) -> set[DiagnosticCode]:
    return {item.code for item in report.diagnostics}


def message_for(report: FactoryReport, code: DiagnosticCode) -> str:
    for item in report.diagnostics:
        if item.code is code:
            return item.message
    msg = f"aucun diagnostic {code} ; obtenus : {sorted(codes(report))}"
    raise AssertionError(msg)


# --------------------------------------------------------------------------- #
# Straight chains
# --------------------------------------------------------------------------- #


def test_iron_plate_chain(game_data: GameData) -> None:
    """60 ore -> 60 ingots -> 40 plates, with nothing wasted anywhere."""
    report = solved("iron_plate", game_data)
    assert report.converged

    assert report.node("mine").outputs == {"Desc_OreIron_C": 60.0}
    smelter = report.node("smelter")
    assert smelter.ratio == 1.0
    assert smelter.inputs == {"Desc_OreIron_C": 60.0}
    assert smelter.outputs == {"Desc_IronIngot_C": 60.0}
    assert smelter.useful_machine_count == 2.0
    assert smelter.idle_machine_count == 0.0

    constructor = report.node("constructor")
    assert constructor.ratio == 1.0
    assert constructor.outputs == {"Desc_IronPlate_C": 40.0}
    assert constructor.limiting is LimitingFactor.NONE

    assert report.final_outputs == {"Desc_IronPlate_C": 40.0}
    assert report.raw_solids == {"Desc_OreIron_C": 60.0}
    assert report.raw_fluids == {}
    assert not report.has_errors()


def test_iron_plate_power_and_shopping_list(game_data: GameData) -> None:
    report = solved("iron_plate", game_data)
    # Miner Mk.1 5 MW + 2 smelters at 4 MW + 2 constructors at 4 MW.
    assert report.power_total_mw == 21.0
    assert report.power_by_building == {
        "Build_ConstructorMk1_C": 8.0,
        "Build_MinerMk1_C": 5.0,
        "Build_SmelterMk1_C": 8.0,
    }
    assert report.shopping_list.buildings == {
        "Build_ConstructorMk1_C": 2,
        "Build_MinerMk1_C": 1,
        "Build_SmelterMk1_C": 2,
    }
    assert report.shopping_list.belts_by_tier == {1: 3}
    assert report.shopping_list.pipes_by_tier == {}


def test_screw_and_reinforced_plate_chain(game_data: GameData) -> None:
    """60 ingots split three ways feed exactly 5 reinforced plates a minute."""
    report = solved("screws_reinforced_plate", game_data)
    assert report.converged

    assert report.node("plates").inputs == {"Desc_IronIngot_C": 45.0}
    assert report.node("rods").inputs == {"Desc_IronIngot_C": 15.0}
    assert report.node("screws").outputs == {"Desc_IronScrew_C": 60.0}

    assembler = report.node("assembler")
    assert assembler.ratio == 1.0
    assert assembler.inputs == {"Desc_IronPlate_C": 30.0, "Desc_IronScrew_C": 60.0}
    assert report.final_outputs == {"Desc_IronPlateReinforced_C": 5.0}
    assert not report.has_errors()


def test_steel_chain(game_data: GameData) -> None:
    """Purity and multiple extractors per node: 3 miners each side, 180 steel out."""
    report = solved("steel_chain", game_data)
    assert report.node("iron").outputs == {"Desc_OreIron_C": 180.0}
    foundry = report.node("foundry")
    assert foundry.ratio == 1.0
    assert foundry.inputs == {"Desc_Coal_C": 180.0, "Desc_OreIron_C": 180.0}
    assert report.final_outputs == {"Desc_SteelIngot_C": 180.0}
    assert report.raw_solids == {"Desc_Coal_C": 180.0, "Desc_OreIron_C": 180.0}
    assert not report.has_errors()


def test_computer_chain(game_data: GameData) -> None:
    """Circuit boards then computers, with plastic shared between the two stages."""
    report = solved("computer_chain", game_data)
    boards = report.node("circuit_boards")
    assert boards.ratio == 1.0
    assert boards.inputs == {"Desc_CopperSheet_C": 60.0, "Desc_Plastic_C": 120.0}
    assert boards.outputs == {"Desc_CircuitBoard_C": 30.0}

    computers = report.node("computers")
    assert computers.ratio == 1.0
    assert computers.inputs == {
        "Desc_Cable_C": 60.0,
        "Desc_CircuitBoard_C": 30.0,
        "Desc_Plastic_C": 120.0,
    }
    assert report.final_outputs == {"Desc_Computer_C": 7.5}
    assert not report.has_errors()


# --------------------------------------------------------------------------- #
# Fluids and byproducts
# --------------------------------------------------------------------------- #


def test_plastic_chain_with_heavy_oil_residue(game_data: GameData) -> None:
    """The reference fluid case: 120 m3 of oil, 80 plastic, 40 m3 of residue flared."""
    report = solved("plastic_chain", game_data)
    assert report.converged

    refinery = report.node("refinery")
    assert refinery.ratio == 1.0
    assert refinery.inputs == {"Desc_LiquidOil_C": 120.0}
    assert refinery.outputs == {"Desc_HeavyOilResidue_C": 40.0, "Desc_Plastic_C": 80.0}

    assert report.raw_fluids == {"Desc_LiquidOil_C": 120.0}
    assert report.raw_solids == {}
    assert report.final_outputs == {"Desc_Plastic_C": 80.0}
    assert report.discarded_outputs == {"Desc_HeavyOilResidue_C": 40.0}

    (residue,) = report.byproducts
    assert residue.item_class == "Desc_HeavyOilResidue_C"
    assert (residue.produced, residue.discarded) == (40.0, 40.0)
    assert (residue.recycled, residue.exported) == (0.0, 0.0)
    assert not report.has_errors()


def test_a_byproduct_with_nowhere_to_go_stops_the_machine_dead(game_data: GameData) -> None:
    """The rule that separates an honest planner from a lying one."""
    report = solved("blocked_byproduct", game_data)
    refinery = report.node("refinery")
    assert refinery.ratio == 0.0
    assert refinery.blocked_products == ("Desc_HeavyOilResidue_C",)
    assert refinery.limiting is LimitingFactor.BLOCKED
    assert refinery.outputs == {}, "une machine bloquee ne produit rien, pas meme son produit"

    assert report.final_outputs == {}, "aucun plastique ne sort"
    assert report.raw_fluids == {}, "et le petrole n'est meme pas consomme"

    assert DiagnosticCode.BLOCKED_BYPRODUCT in codes(report)
    blocking = message_for(report, DiagnosticCode.BLOCKED_BYPRODUCT)
    assert "Résidus de pétrole lourd" in blocking
    assert "rejet assume" in blocking
    assert report.has_errors()


def test_a_partly_absorbed_byproduct_throttles_instead_of_blocking(
    game_data: GameData,
) -> None:
    """Back pressure, at the exact rate.

    Twelve refineries would make 120 m3/min of residue; the single residual-fuel
    refinery downstream swallows 60. The plastic refineries therefore run at exactly
    half speed -- which is what the game averages out to as they stutter.
    """
    report = solved("backpressure", game_data)
    assert report.converged

    plastic = report.node("plastic")
    assert plastic.ratio == pytest.approx(0.5, abs=TOLERANCE)
    assert plastic.limiting is LimitingFactor.OUTPUTS
    assert plastic.blocked_products == (), "une sortie existe : ce n'est pas un blocage"
    assert plastic.inputs == {"Desc_LiquidOil_C": 180.0}
    assert plastic.outputs == {"Desc_HeavyOilResidue_C": 60.0, "Desc_Plastic_C": 120.0}
    assert plastic.useful_machine_count == pytest.approx(6.0, abs=TOLERANCE)
    assert plastic.idle_machine_count == pytest.approx(6.0, abs=TOLERANCE)

    fuel = report.node("residual_fuel")
    assert fuel.ratio == 1.0
    assert fuel.outputs == {"Desc_LiquidFuel_C": 40.0}

    assert report.final_outputs == {"Desc_LiquidFuel_C": 40.0, "Desc_Plastic_C": 120.0}
    (residue,) = report.byproducts
    assert (residue.produced, residue.recycled) == (60.0, 60.0)

    assert DiagnosticCode.BACKPRESSURE in codes(report)
    assert "50 %" in message_for(report, DiagnosticCode.BACKPRESSURE)
    # The oil field is oversized as a result: that is a separate finding.
    assert DiagnosticCode.SURPLUS in codes(report)
    assert "180" in message_for(report, DiagnosticCode.SURPLUS)


# --------------------------------------------------------------------------- #
# Cycles
# --------------------------------------------------------------------------- #


def test_the_recycling_loop_runs_at_full_speed(game_data: GameData) -> None:
    """Recycled plastic and recycled rubber feed each other and both run at 100 %.

    This is the case that rules out starting the iteration at zero: "everything
    stopped" is a perfectly self-consistent state for this loop, and a solver that
    starts there never leaves it. The physical answer is the largest steady state.
    """
    graph = load_graph("recycling_loop")
    # The two refineries really do form one strongly connected component.
    cyclic = [component for component in condensation_order(graph) if len(component) > 1]
    assert cyclic == [("recycled_plastic", "recycled_rubber")]

    report = engine.solve(graph, game_data)
    assert report.converged

    plastic = report.node("recycled_plastic")
    rubber = report.node("recycled_rubber")
    assert plastic.ratio == 1.0
    assert rubber.ratio == 1.0
    assert plastic.inputs == {"Desc_LiquidFuel_C": 30.0, "Desc_Rubber_C": 30.0}
    assert plastic.outputs == {"Desc_Plastic_C": 60.0}
    assert rubber.inputs == {"Desc_LiquidFuel_C": 30.0, "Desc_Plastic_C": 30.0}
    assert rubber.outputs == {"Desc_Rubber_C": 60.0}

    # Half of each product loops back, half leaves the factory.
    assert report.edge("e3").rate_per_minute == 30.0
    assert report.edge("e4").rate_per_minute == 30.0
    assert report.final_outputs == {"Desc_Plastic_C": 30.0, "Desc_Rubber_C": 30.0}
    assert report.raw_fluids == {"Desc_LiquidFuel_C": 60.0}
    assert not report.has_errors()


# --------------------------------------------------------------------------- #
# Shortages and allocation
# --------------------------------------------------------------------------- #


def test_input_deficit(game_data: GameData) -> None:
    """Three smelters on one Mk.1 miner: two thirds of the capacity is idle."""
    report = solved("deficit", game_data)
    smelter = report.node("smelter")
    assert smelter.ratio == pytest.approx(2 / 3, abs=TOLERANCE)
    assert smelter.limiting is LimitingFactor.INPUTS
    assert smelter.inputs == {"Desc_OreIron_C": 60.0}
    assert smelter.outputs == {"Desc_IronIngot_C": 60.0}
    assert smelter.useful_machine_count == pytest.approx(2.0, abs=TOLERANCE)
    assert smelter.integer_machine_count == 3

    message = message_for(report, DiagnosticCode.DEFICIT)
    assert "30/min" in message, message
    assert "90/min" in message, message
    assert "66,667 %" in message, message


def test_allocation_is_proportional_to_demand(game_data: GameData) -> None:
    """60 ingots for 90 asked: each consumer gets its share of the shortage."""
    report = solved("allocation", game_data)
    assert report.node("small").inputs == {"Desc_IronIngot_C": 20.0}
    assert report.node("large").inputs == {"Desc_IronIngot_C": 40.0}
    assert report.node("small").ratio == pytest.approx(2 / 3, abs=TOLERANCE)
    assert report.node("large").ratio == pytest.approx(2 / 3, abs=TOLERANCE)
    # Nothing is lost: the source is fully drained.
    assert report.node("ingots").outputs == {"Desc_IronIngot_C": 60.0}


def test_surplus_is_redistributed_to_whoever_can_take_it(game_data: GameData) -> None:
    """A blocked consumer must not hoard flow it cannot use."""
    report = solved("allocation_redistribution", game_data)
    blocked = report.node("blocked")
    working = report.node("working")

    assert blocked.ratio == 0.0
    assert blocked.inputs == {}, "un consommateur bloque ne prend rien"
    assert working.inputs == {"Desc_IronIngot_C": 45.0}, "l'autre obtient toute sa demande"
    assert working.ratio == 1.0
    assert report.final_outputs == {"Desc_IronPlate_C": 30.0}
    # 15 of the 60 imported ingots find no taker.
    assert report.node("ingots").outputs == {"Desc_IronIngot_C": 45.0}
    assert DiagnosticCode.SURPLUS in codes(report)


# --------------------------------------------------------------------------- #
# Lines
# --------------------------------------------------------------------------- #


def test_belt_saturation_suggests_the_smallest_sufficient_tier(game_data: GameData) -> None:
    """480 items/min on a Mk.1 belt. Capacity is diagnosed, never silently enforced."""
    report = solved("belt_saturation", game_data)
    edge = report.edge("e1")
    assert edge.rate_per_minute == 480.0
    assert edge.capacity_per_minute == 60.0
    assert edge.is_saturated
    assert edge.saturation == 8.0

    message = message_for(report, DiagnosticCode.LINE_SATURATION)
    assert "Convoyeur Mk.4" in message, message


def test_pipe_saturation_when_no_tier_is_enough(game_data: GameData) -> None:
    """720 m3/min: even a Mk.2 pipe tops out at 600, so the line must be doubled."""
    report = solved("pipe_saturation", game_data)
    edge = report.edge("e1")
    assert edge.rate_per_minute == 720.0
    assert edge.capacity_per_minute == 600.0

    message = message_for(report, DiagnosticCode.LINE_SATURATION)
    assert "2 voies" in message, message
    assert "m³/min" in message, message


# --------------------------------------------------------------------------- #
# Buffers
# --------------------------------------------------------------------------- #


def test_a_buffer_that_fills_up(game_data: GameData) -> None:
    """60 ore in, 30 consumed: 24 slots of 100 fill up in 80 minutes."""
    report = solved("buffer_filling", game_data)
    (buffer,) = report.buffers
    assert buffer.item_class == "Desc_OreIron_C"
    assert (buffer.inflow, buffer.outflow) == (60.0, 30.0)
    assert buffer.net == 30.0
    assert buffer.state is BufferState.FILLING
    assert buffer.capacity == 2400.0
    assert buffer.minutes_to_full == 80.0
    assert buffer.minutes_to_empty is None
    assert DiagnosticCode.BUFFER_FILLING in codes(report)


def test_a_buffer_that_drains(game_data: GameData) -> None:
    """A full 400 m3 tank feeding one refinery lasts 13 minutes and 20 seconds."""
    report = solved("buffer_draining", game_data)
    (buffer,) = report.buffers
    assert (buffer.inflow, buffer.outflow) == (0.0, 30.0)
    assert buffer.state is BufferState.DRAINING
    assert buffer.minutes_to_empty == pytest.approx(400 / 30, abs=TOLERANCE)
    # The refinery downstream does run, on stock.
    assert report.node("refinery").ratio == 1.0
    assert report.final_outputs == {"Desc_Plastic_C": 20.0}

    message = message_for(report, DiagnosticCode.BUFFER_DRAINING)
    assert "13,333 min" in message, message
    severities = {item.code: item.severity for item in report.diagnostics}
    assert severities[DiagnosticCode.BUFFER_DRAINING] is Severity.WARNING


# --------------------------------------------------------------------------- #
# Robustness
# --------------------------------------------------------------------------- #


def test_the_result_does_not_depend_on_insertion_order(game_data: GameData) -> None:
    """Same factory, assembled backwards: byte-identical report."""
    forward = load_graph("screws_reinforced_plate")
    backward = FactoryGraph(
        schema_version=forward.schema_version,
        nodes=list(reversed(forward.nodes)),
        edges=list(reversed(forward.edges)),
    )
    assert engine.solve(forward, game_data) == engine.solve(backward, game_data)


def test_non_convergence_is_reported_instead_of_hanging(
    game_data: GameData, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With a budget of one iteration, the engine gives up cleanly and says so."""
    monkeypatch.setattr(constants, "MAX_ITERATIONS", 1)
    report = solved("allocation", game_data)

    assert report.converged is False
    assert report.iterations == 1
    assert DiagnosticCode.NOT_CONVERGED in codes(report)
    assert report.has_errors()
    # The last iteration's figures are still returned, flagged as unstable.
    assert report.node("small").inputs == {"Desc_IronIngot_C": 20.0}
    assert "instables" in message_for(report, DiagnosticCode.NOT_CONVERGED)


def test_an_empty_factory_solves_to_nothing(game_data: GameData) -> None:
    report = engine.solve(FactoryGraph(), game_data)
    assert report.converged
    assert report.nodes == ()
    assert report.power_total_mw == 0.0
    assert report.diagnostics == ()


def test_a_machine_with_zero_machines_consumes_nothing(game_data: GameData) -> None:
    graph = FactoryGraph()
    graph.add_node(ExternalSourceNode(id="src", item_class="Desc_IronIngot_C", rate_per_minute=30))
    graph.add_node(MachineNode(id="idle", recipe_class="Recipe_IronPlate_C", machine_count=0))
    graph.add_node(OutputNode(id="out", item_class="Desc_IronPlate_C"))
    graph.connect("src", "idle", "Desc_IronIngot_C", "Build_ConveyorBeltMk1_C", game_data)
    graph.connect("idle", "out", "Desc_IronPlate_C", "Build_ConveyorBeltMk1_C", game_data)

    report = engine.solve(graph, game_data)
    assert report.node("idle").inputs == {}
    assert report.node("idle").outputs == {}
    assert report.node("idle").power_mw == 0.0


# --------------------------------------------------------------------------- #
# Machine count assistance
# --------------------------------------------------------------------------- #


def test_suggest_machine_count_sizes_a_node_to_its_inputs(game_data: GameData) -> None:
    """The "adjust this node" action: 60 ore/min feeds exactly two smelters."""
    graph = load_graph("deficit")
    assert engine.suggest_machine_count(graph, game_data, "smelter") == pytest.approx(
        2.0, abs=TOLERANCE
    )
    # Sizing does not mutate the graph it was asked about.
    smelter = graph.node("smelter")
    assert isinstance(smelter, MachineNode)
    assert smelter.machine_count == 3


def test_suggest_machine_count_can_also_grow_a_node(game_data: GameData) -> None:
    """An undersized node is told it could be bigger, not left as it is."""
    graph = load_graph("iron_plate")
    smelter = graph.node("smelter")
    assert isinstance(smelter, MachineNode)
    smelter.machine_count = 0.5
    assert engine.suggest_machine_count(graph, game_data, "smelter") == pytest.approx(
        2.0, abs=TOLERANCE
    )


def test_suggest_machine_count_refuses_anything_but_a_machine(game_data: GameData) -> None:
    graph = load_graph("iron_plate")
    with pytest.raises(TypeError, match="n'est pas une machine"):
        engine.suggest_machine_count(graph, game_data, "mine")
