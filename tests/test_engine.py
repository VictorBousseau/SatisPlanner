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
    GraphError,
    MachineNode,
    OutputNode,
    SplitterNode,
    StorageNode,
    condensation_order,
)
from satisplanner.core.models import AttachmentRole, GameData, ItemForm, SplitterMode
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
    # Every line goes from one node to one node: nothing to split or merge.
    assert report.shopping_list.attachments == {}


# --------------------------------------------------------------------------- #
# Splitters, mergers and junctions
# --------------------------------------------------------------------------- #


def test_a_splitter_is_counted_where_it_is_placed(game_data: GameData) -> None:
    """Two splitters stand in the recycling loop, so two are on the shopping list.

    Counted from the nodes and no longer deduced from the lines, which is the whole
    of the change: what has to be built is what somebody drew.
    """
    report = solved("recycling_loop", game_data)
    assert report.shopping_list.attachments == {"Build_ConveyorAttachmentSplitter_C": 2}
    # And they are counted in the total, alongside the machines.
    assert report.shopping_list.total_buildings == sum(report.shopping_list.buildings.values()) + 2


def test_a_node_with_a_port_per_machine_needs_no_splitter(game_data: GameData) -> None:
    """Two lines out of a bank of two machines are two ports, not a fan-out.

    The consequence of counting ports rather than lines, and the one that makes the
    shopping list of a balanced factory shorter than it used to be: eight smelters
    feeding eight consumers need nothing between them.
    """
    graph = FactoryGraph()
    graph.add_node(ExternalSourceNode(id="src", item_class="Desc_OreIron_C", rate_per_minute=60))
    graph.add_node(MachineNode(id="bank", recipe_class="Recipe_IngotIron_C", machine_count=2))
    belt = "Build_ConveyorBeltMk3_C"
    graph.connect("src", "bank", "Desc_OreIron_C", belt, game_data)
    for index in (1, 2):
        graph.add_node(OutputNode(id=f"out{index}", item_class="Desc_IronIngot_C"))
        graph.connect("bank", f"out{index}", "Desc_IronIngot_C", belt, game_data)

    assert engine.solve(graph, game_data).shopping_list.attachments == {}

    # A third line has no port left, and the refusal says what to insert.
    graph.add_node(OutputNode(id="out3", item_class="Desc_IronIngot_C"))
    with pytest.raises(GraphError, match="répartiteur"):
        graph.connect("bank", "out3", "Desc_IronIngot_C", belt, game_data)


def test_fluids_use_a_pipe_junction_rather_than_a_splitter(game_data: GameData) -> None:
    """The same node on a pipe is a junction, because that is the building for it."""
    graph = FactoryGraph()
    graph.add_node(ExternalSourceNode(id="well", item_class="Desc_Water_C", rate_per_minute=120))
    graph.add_node(SplitterNode(id="tee"))
    pipe = "Build_PipelineMK2_C"
    graph.connect("well", "tee", "Desc_Water_C", pipe, game_data)
    for index in (1, 2):
        graph.add_node(OutputNode(id=f"out{index}", item_class="Desc_Water_C"))
        graph.connect("tee", f"out{index}", "Desc_Water_C", pipe, game_data)

    report = engine.solve(graph, game_data)
    assert report.shopping_list.attachments == {"Build_PipelineJunction_Cross_C": 1}
    assert report.node("tee").label == "Jonction de pipeline"


def test_a_splitter_serves_three_lines_at_a_time(game_data: GameData) -> None:
    """The branch count the graph validates with is the catalogue's own.

    The graph checks a port budget without a catalogue in hand -- a document is
    migrated before any game data is looked at -- so the figure lives in
    ``core.constants``. This is what stops the two from drifting apart.
    """
    assert all(
        attachment.branches == constants.ATTACHMENT_BRANCHES
        for attachment in game_data.attachments.values()
    )


def test_the_shopping_list_picks_the_attachment_by_form_role_and_mode(
    game_data: GameData,
) -> None:
    solid, fluid = ItemForm.SOLID, ItemForm.LIQUID
    split, merge = AttachmentRole.SPLIT, AttachmentRole.MERGE
    plain = game_data.attachment_for(solid, split, SplitterMode.STANDARD)
    assert plain is not None
    assert plain is not game_data.attachment_for(solid, merge)
    # Three splitters on a belt, one per mode, and they are three buildings.
    modes = {
        mode: game_data.attachment_for(solid, split, mode) for mode in SplitterMode
    }
    assert None not in modes.values()
    assert len({found.class_name for found in modes.values() if found}) == 3

    # A pipe junction does both jobs, so the same class answers either question --
    # and it has no mode at all, because the game has no filtering junction.
    assert game_data.attachment_for(fluid, split) is game_data.attachment_for(fluid, merge)
    assert game_data.attachment_for(fluid, split, SplitterMode.SMART) is None


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
    assert refinery.outputs == {}, "une machine bloquée ne produit rien, pas même son produit"

    assert report.final_outputs == {}, "aucun plastique ne sort"
    assert report.raw_fluids == {}, "et le petrole n'est même pas consomme"

    assert DiagnosticCode.BLOCKED_BYPRODUCT in codes(report)
    blocking = message_for(report, DiagnosticCode.BLOCKED_BYPRODUCT)
    assert "Résidus de pétrole lourd" in blocking
    assert "rejet assumé" in blocking
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
    # The splitters that carry each product back round are part of the cycle: a
    # loop closed through a fitting is still a loop.
    assert cyclic == [
        (
            "recycled_plastic",
            "recycled_plastic-rep1",
            "recycled_rubber",
            "recycled_rubber-rep1",
        )
    ]

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

    # Half of each product loops back, half leaves the factory -- through the
    # splitter that does the halving, which is where the two lines now start.
    assert report.edge("e9").rate_per_minute == 60.0, "tout le plastique passe par le répartiteur"
    assert report.edge("e7").rate_per_minute == 30.0
    assert report.edge("e8").rate_per_minute == 30.0
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
    # The rates keep all three decimals; the percentage derived from them gets one.
    assert "30/min" in message, message
    assert "90/min" in message, message
    assert "66,7 %" in message, message


def test_allocation_shares_equally_rather_than_proportionally(game_data: GameData) -> None:
    """60 ingots for 90 asked, split the way the game's splitter splits.

    A splitter gives each output an equal turn: 30 each. The smaller consumer only
    wants 30, so it is served in full and it is the larger one that absorbs the whole
    shortage. Dividing in proportion to demand would have given 20 and 40 and left
    *both* machines limping, which is not what the game does.
    """
    report = solved("allocation", game_data)
    assert report.node("small").inputs == {"Desc_IronIngot_C": 30.0}
    assert report.node("large").inputs == {"Desc_IronIngot_C": 30.0}
    assert report.node("small").ratio == 1.0
    assert report.node("large").ratio == pytest.approx(0.5, abs=TOLERANCE)
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


def test_a_belt_carries_its_tier_and_backs_the_rest_up(game_data: GameData) -> None:
    """480 items/min offered to a Mk.1 belt: 60 travel, the miner throttles to 12,5 %.

    Capacity is a constraint, not a remark. The rate carried is the belt's, and the
    480 the mine could produce survives as the *demanded* rate, which is what names
    the tier to upgrade to.
    """
    report = solved("belt_saturation", game_data)
    edge = report.edge("e1")
    assert edge.rate_per_minute == 60.0, "un Mk.1 transporte 60/min, pas 480"
    assert edge.capacity_per_minute == 60.0
    assert edge.demanded_rate == 480.0
    assert edge.blocked_rate == 420.0
    assert edge.is_saturated
    assert edge.is_at_capacity
    assert edge.saturation == 8.0

    # The back pressure reaches the extractor, and it is the line that is blamed.
    mine = report.node("mine")
    assert mine.ratio == pytest.approx(0.125, abs=TOLERANCE)
    assert mine.limiting is LimitingFactor.LINE
    assert mine.line_limited_items == ("Desc_OreIron_C",)

    message = message_for(report, DiagnosticCode.LINE_SATURATION)
    assert "Convoyeur Mk.4" in message, message
    assert "480/min" in message, message
    assert "60/min" in message, message
    # No surplus finding on top: the line is the single actionable cause.
    assert DiagnosticCode.SURPLUS not in codes(report)


def test_pipe_saturation_when_no_tier_is_enough(game_data: GameData) -> None:
    """720 m3/min: even a Mk.2 pipe tops out at 600, so the line must be doubled."""
    report = solved("pipe_saturation", game_data)
    edge = report.edge("e1")
    assert edge.rate_per_minute == 600.0
    assert edge.capacity_per_minute == 600.0
    assert edge.demanded_rate == 720.0

    message = message_for(report, DiagnosticCode.LINE_SATURATION)
    assert "2 voies" in message, message
    assert "m³/min" in message, message


def test_a_line_that_fits_exactly_is_not_reported(game_data: GameData) -> None:
    """A Mk.1 belt carrying exactly 60/min is full, not undersized."""
    report = solved("iron_plate", game_data)
    edge = report.edge("e1")
    assert (edge.rate_per_minute, edge.capacity_per_minute) == (60.0, 60.0)
    assert edge.is_at_capacity
    assert not edge.is_saturated
    assert DiagnosticCode.LINE_SATURATION not in codes(report)
    assert report.node("smelter").limiting is LimitingFactor.NONE


def test_a_shortage_is_not_blamed_on_the_line(game_data: GameData) -> None:
    """The deficit chain runs its Mk.1 belts at exactly 60/min: the ore is what is short."""
    report = solved("deficit", game_data)
    assert report.edge("e1").is_at_capacity
    assert not report.edge("e1").is_saturated
    assert report.node("smelter").limiting is LimitingFactor.INPUTS
    assert report.node("smelter").line_limited_items == ()


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


def test_a_draining_factory_is_not_sustainable_and_says_so(game_data: GameData) -> None:
    """The report that would otherwise lie for thirteen minutes.

    "The refinery runs at 100 %" and "the tank empties in 13,333 min" are both true
    and sit in different parts of the report. The flag and the companion resolution
    are what stop the first from being read as a steady state.
    """
    report = solved("buffer_draining", game_data)
    assert report.is_sustainable is False
    assert [buffer.node_id for buffer in report.draining_buffers] == ["buffer"]
    assert report.shortest_autonomy_minutes == pytest.approx(400 / 30, abs=TOLERANCE)

    message = message_for(report, DiagnosticCode.NOT_SUSTAINABLE)
    assert "vivent sur un stock" in message, message
    assert "13,333 min" in message, message
    assert "plus aucune production" in message, message


def test_the_established_regime_is_solved_without_the_stocks(game_data: GameData) -> None:
    """The second set of figures: the same factory once the tank is dry."""
    report = solved("buffer_draining", game_data)
    sustained = report.sustained
    assert sustained is not None
    assert sustained.converged
    # Nothing feeds the tank, so nothing leaves it and the refinery stops.
    assert sustained.node("refinery").ratio == 0.0
    assert sustained.final_outputs == {}
    assert sustained.raw_fluids == {}
    # The established regime is itself sustainable: it invents nothing, so the
    # companion report is never nested a second time.
    assert sustained.is_sustainable
    assert sustained.sustained is None


def test_a_buffer_that_only_passes_material_through_stays_sustainable(
    game_data: GameData,
) -> None:
    """A buffer between a source and a machine is not a stock: it is a pipe with a lid."""
    graph = FactoryGraph()
    graph.add_node(ExternalSourceNode(id="src", item_class="Desc_OreIron_C", rate_per_minute=60))
    graph.add_node(StorageNode(id="buffer", storage_class="Build_StorageContainerMk1_C"))
    graph.add_node(MachineNode(id="smelter", recipe_class="Recipe_IngotIron_C", machine_count=2))
    graph.add_node(OutputNode(id="out", item_class="Desc_IronIngot_C"))
    belt = "Build_ConveyorBeltMk1_C"
    graph.connect("src", "buffer", "Desc_OreIron_C", belt, game_data)
    graph.connect("buffer", "smelter", "Desc_OreIron_C", belt, game_data)
    graph.connect("smelter", "out", "Desc_IronIngot_C", belt, game_data)

    report = engine.solve(graph, game_data)
    assert report.is_sustainable
    assert report.sustained is None
    (buffer,) = report.buffers
    assert (buffer.inflow, buffer.outflow) == (60.0, 60.0)
    assert buffer.state is BufferState.BALANCED
    assert report.final_outputs == {"Desc_IronIngot_C": 60.0}


def test_a_filling_buffer_still_counts_as_sustainable(game_data: GameData) -> None:
    """A buffer that accumulates costs nothing to the steady state: it only fills up."""
    report = solved("buffer_filling", game_data)
    assert report.is_sustainable
    assert report.sustained is None


# --------------------------------------------------------------------------- #
# Buffers feeding something that absorbs without limit
#
# The container put at the end of a line to soak up a surplus is the first thing
# anyone building a factory reaches for, and until this fixture existed no test
# had one: every buffer in the corpus fed a machine. A buffer whose only route
# out absorbed without limit took material in and passed none of it on, through
# two released versions.
# --------------------------------------------------------------------------- #


def test_a_buffer_passes_its_intake_on_to_an_unlimited_absorber(game_data: GameData) -> None:
    """240 in, 240 out, split between the exit and the flare. Not 240 in and 0 out.

    A route that absorbs without limit gives the buffer no figure to match, so the
    buffer offers its own intake -- a quantity that is only known once the round's
    flows have been allocated, which is what the convergence test used to miss.
    """
    report = solved("buffer_to_sink", game_data)
    assert report.converged

    (buffer,) = report.buffers
    assert (buffer.inflow, buffer.outflow) == (240.0, 240.0)
    assert buffer.state is BufferState.BALANCED
    assert report.final_outputs == {"Desc_IronIngot_C": 120.0}
    assert report.discarded_outputs == {"Desc_IronIngot_C": 120.0}
    assert report.is_sustainable, "rien ne se vide : le tampon ne fait que laisser passer"


def test_the_intake_of_a_buffer_is_counted_once_however_many_sinks(
    game_data: GameData,
) -> None:
    """Two containers side by side share what arrives; they do not each get a copy.

    The fixture has an exit and a flare on the same buffer. Summing the intake once
    per route would offer 480 for 240 received, and the buffer would report itself
    as draining twice as fast as it is being filled.
    """
    report = solved("buffer_to_sink", game_data)
    (buffer,) = report.buffers
    assert buffer.outflow == buffer.inflow
    assert buffer.net == 0.0
    assert DiagnosticCode.BUFFER_DRAINING not in codes(report)


def test_a_buffer_serves_its_machines_first_and_the_sink_takes_the_rest(
    game_data: GameData,
) -> None:
    """A consumer with an appetite is not starved by a container standing next to it.

    A buffer has one output port, so the two lines leave through a splitter -- and
    the rule survives the move: the splitter's own branches are shared the same way,
    the machine first and the sink with what is left.
    """
    graph = FactoryGraph()
    graph.add_node(ExternalSourceNode(id="src", item_class="Desc_IronIngot_C", rate_per_minute=240))
    graph.add_node(StorageNode(id="buffer", storage_class="Build_StorageContainerMk2_C"))
    graph.add_node(SplitterNode(id="fork"))
    graph.add_node(MachineNode(id="plates", recipe_class="Recipe_IronPlate_C", machine_count=4))
    graph.add_node(OutputNode(id="plate_out", item_class="Desc_IronPlate_C"))
    graph.add_node(OutputNode(id="surplus", item_class="Desc_IronIngot_C"))
    belt = "Build_ConveyorBeltMk5_C"
    graph.connect("src", "buffer", "Desc_IronIngot_C", belt, game_data)
    graph.connect("buffer", "fork", "Desc_IronIngot_C", belt, game_data)
    graph.connect("fork", "plates", "Desc_IronIngot_C", belt, game_data)
    graph.connect("fork", "surplus", "Desc_IronIngot_C", belt, game_data)
    graph.connect("plates", "plate_out", "Desc_IronPlate_C", belt, game_data)

    report = engine.solve(graph, game_data)

    assert report.node("plates").inputs == {"Desc_IronIngot_C": 120.0}
    assert report.node("surplus").inputs == {"Desc_IronIngot_C": 120.0}
    (buffer,) = report.buffers
    assert (buffer.inflow, buffer.outflow) == (240.0, 240.0)
    assert report.is_sustainable


def test_a_chain_of_buffers_ending_in_a_sink_passes_material_all_the_way(
    game_data: GameData,
) -> None:
    """Each buffer learns its intake a round after the one before it, and must be let.

    The regression guard on the convergence test itself rather than on one factory:
    a sweep visits the buffers in identifier order, so a chain settles one link per
    round. An iteration that stopped as soon as the ratios held still would freeze
    this at the first link.
    """
    graph = FactoryGraph()
    graph.add_node(ExternalSourceNode(id="src", item_class="Desc_IronIngot_C", rate_per_minute=240))
    belt = "Build_ConveyorBeltMk5_C"
    previous = "src"
    # Named backwards on purpose, so sorting them cannot happen to match the flow.
    for name in ("buffer_d", "buffer_c", "buffer_b", "buffer_a"):
        graph.add_node(StorageNode(id=name, storage_class="Build_StorageContainerMk2_C"))
        graph.connect(previous, name, "Desc_IronIngot_C", belt, game_data)
        previous = name
    graph.add_node(OutputNode(id="out", item_class="Desc_IronIngot_C"))
    graph.connect(previous, "out", "Desc_IronIngot_C", belt, game_data)

    report = engine.solve(graph, game_data)

    assert report.converged
    assert report.final_outputs == {"Desc_IronIngot_C": 240.0}
    for buffer in report.buffers:
        assert (buffer.inflow, buffer.outflow) == (240.0, 240.0), buffer.node_id


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
    assert report.node("small").inputs == {"Desc_IronIngot_C": 30.0}
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
