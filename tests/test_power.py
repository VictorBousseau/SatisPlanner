"""Electricity production: what the data says, and what the engine does with it.

Two halves. The first reads the catalogue and locks the control values against the
game files -- power, burn rates, make-up water -- and pins the one trap of the
subject: a fluid's energy value is stored per litre, so it takes the same factor of
a thousand as every other fluid quantity, in the other direction.

The second solves whole factories. The point that deserves the most attention is not
that the numbers are right but that a power deficit **changes nothing**: electricity
is counted, never allocated, and the test that proves it compares a factory that can
pay its bill with the same factory that cannot.
"""

import pytest

from satisplanner.core import engine
from satisplanner.core.graph import (
    ExternalSourceNode,
    FactoryGraph,
    GeneratorNode,
    WaterExtractorNode,
)
from satisplanner.core.models import GameData, ItemForm
from satisplanner.core.results import DiagnosticCode, FactoryReport, LimitingFactor, Severity
from satisplanner.data import conversions
from satisplanner.data.docs_parser import EXCLUDED_GENERATORS, GENERATORS
from tests.conftest import load_graph

TOLERANCE = 1e-9

BIOMASS = "Build_GeneratorBiomass_Automated_C"
COAL = "Build_GeneratorCoal_C"
FUEL = "Build_GeneratorFuel_C"


def solved(name: str, game_data: GameData) -> FactoryReport:
    return engine.solve(load_graph(name), game_data)


def codes(report: FactoryReport) -> set[DiagnosticCode]:
    return {item.code for item in report.diagnostics}


# --------------------------------------------------------------------------- #
# The catalogue: control values, read from the game files
# --------------------------------------------------------------------------- #


def test_the_three_generators_are_there_and_no_others(game_data: GameData) -> None:
    assert set(game_data.generators) == GENERATORS
    for excluded in EXCLUDED_GENERATORS:
        assert excluded not in game_data.generators
        assert excluded not in game_data.buildings


def test_the_biomass_burner_produces_thirty_megawatts(game_data: GameData) -> None:
    assert game_data.generator(BIOMASS).power_mw == 30.0


def test_the_coal_generator_matches_the_control_table(game_data: GameData) -> None:
    """75 MW, 15 coal a minute, 45 m3 of water a minute."""
    generator = game_data.generator(COAL)
    assert generator.power_mw == 75.0
    coal = generator.fuel("Desc_Coal_C")
    assert coal is not None
    assert coal.rate_per_minute == pytest.approx(15.0, abs=TOLERANCE)
    assert coal.supplemental_class == "Desc_Water_C"
    assert coal.supplemental_per_minute == pytest.approx(45.0, abs=TOLERANCE)


def test_the_fuel_generator_reads_two_hundred_and_fifty_megawatts(game_data: GameData) -> None:
    """The one figure the specification left open, taken from the files."""
    generator = game_data.generator(FUEL)
    assert generator.power_mw == 250.0
    fuel = generator.fuel("Desc_LiquidFuel_C")
    assert fuel is not None
    assert fuel.rate_per_minute == pytest.approx(20.0, abs=TOLERANCE)


def test_the_fuel_generator_needs_no_water(game_data: GameData) -> None:
    """Stated as a test because the specification expected the opposite.

    ``mRequiresSupplementalResource`` is False on the fuel generator and every one
    of its fuel entries names no supplemental class; only the coal generator does.
    The files win, and this test is what stops the assumption creeping back in.
    """
    for fuel in game_data.generator(FUEL).fuels:
        assert fuel.supplemental_class is None
        assert fuel.supplemental_per_minute == 0.0


def test_turbofuel_buys_the_same_power_for_less_volume(game_data: GameData) -> None:
    """The reason the fuel is a property of the node and not of the palette entry."""
    generator = game_data.generator(FUEL)
    turbo = generator.fuel("Desc_LiquidTurboFuel_C")
    assert turbo is not None
    assert turbo.rate_per_minute == pytest.approx(7.5, abs=TOLERANCE)


def test_a_fluid_energy_value_carries_the_litre_factor(game_data: GameData) -> None:
    """The trap: ``mEnergyValue`` is per litre for a fluid, per item for a solid.

    Fuel stores 0.75 and coal stores 300. Read naively, a cubic metre of fuel would
    hold four hundred times less energy than a lump of coal. The packaged twin
    settles it: one canister holds exactly one cubic metre and declares 750 outright.
    """
    assert game_data.item("Desc_Coal_C").energy_mj == 300.0
    assert game_data.item("Desc_LiquidFuel_C").form is ItemForm.LIQUID
    assert game_data.item("Desc_LiquidFuel_C").energy_mj == 750.0
    assert conversions.energy_mj(0.75, ItemForm.LIQUID) == 750.0
    assert conversions.energy_mj(300.0, ItemForm.SOLID) == 300.0


def test_every_burn_rate_is_derived_and_never_written_down(game_data: GameData) -> None:
    """Power over energy, for all three generators and all their fuels at once."""
    for generator in game_data.generators.values():
        for fuel in generator.fuels:
            energy = game_data.item(fuel.item_class).energy_mj
            assert energy > 0
            assert fuel.rate_per_minute == pytest.approx(
                generator.power_mw / energy * 60.0, abs=TOLERANCE
            )


def test_a_fuel_outside_the_catalogue_is_not_offered(game_data: GameData) -> None:
    """The fixture deliberately omits rocket fuel, which the game lists.

    A fuel whose item is not in the catalogue would be a choice that could never be
    supplied, so it is dropped rather than shown. That the *real* database keeps it
    is the same rule applied to a catalogue that does have the item.
    """
    assert "Desc_RocketFuel_C" not in game_data.items
    assert not game_data.generator(FUEL).accepts("Desc_RocketFuel_C")
    assert game_data.generator(FUEL).fuels  # and the ones it does have survived


def test_a_generator_is_a_building_of_its_own_kind(game_data: GameData) -> None:
    for class_name in GENERATORS:
        building = game_data.building(class_name)
        assert building.kind.value == "generator"
        # `mPowerConsumption` is zero on a generator: it produces, it does not draw.
        assert building.power_mw == 0.0


# --------------------------------------------------------------------------- #
# Whole factories
# --------------------------------------------------------------------------- #


def test_the_coal_chain_balances_exactly(game_data: GameData) -> None:
    """One Mk.2 miner and three water pumps feed eight coal generators.

    120 coal a minute is 8 x 15, and 360 m3 of water is 8 x 45: nothing is left over
    on either line, which is what makes every figure below exact rather than close.
    """
    report = solved("coal_power", game_data)
    assert report.converged

    generators = report.node("generators")
    assert generators.ratio == 1.0
    assert generators.limiting is LimitingFactor.NONE
    assert generators.inputs == {"Desc_Coal_C": 120.0, "Desc_Water_C": 360.0}
    assert generators.outputs == {}
    assert generators.power_produced_mw == 600.0

    assert report.power_production_mw == 600.0
    assert report.power_production_by_building == {COAL: 600.0}
    # Miner Mk.2 at 15 MW and three water pumps at 20.
    assert report.power_total_mw == 75.0
    assert report.power_balance_mw == 525.0
    assert not report.has_power_deficit
    assert not report.has_errors()


def test_generators_are_ordinary_lines_in_the_shopping_list(game_data: GameData) -> None:
    """Counted like any other building, and never with a shard: they have no clock."""
    report = solved("coal_power", game_data)
    assert report.shopping_list.buildings == {
        COAL: 8,
        "Build_MinerMk2_C": 1,
        "Build_WaterPump_C": 3,
    }
    assert report.shopping_list.power_shards == {}


def test_the_fuel_chain_turns_a_byproduct_into_power(game_data: GameData) -> None:
    """Oil, plastic, and the heavy residue burnt as fuel instead of flared."""
    report = solved("fuel_power", game_data)
    assert report.converged

    assert report.node("plastic").outputs == {
        "Desc_HeavyOilResidue_C": 40.0,
        "Desc_Plastic_C": 80.0,
    }
    # 40 m3 of residue on a refinery that could take 60: two thirds of a machine.
    residual = report.node("residual_fuel")
    assert residual.ratio == pytest.approx(2 / 3, abs=1e-9)
    assert residual.outputs["Desc_LiquidFuel_C"] == pytest.approx(80 / 3, abs=1e-9)

    generator = report.node("generator")
    assert generator.inputs == {"Desc_LiquidFuel_C": 20.0}
    assert generator.power_produced_mw == 250.0
    # Oil pump 40 MW, five refineries at 30.
    assert report.power_total_mw == 190.0
    assert report.power_balance_mw == 60.0
    assert not report.has_power_deficit


def _fed_generator(fuel_class: str, rate: float, game_data: GameData) -> FactoryReport:
    """One import feeding one fuel generator, and nothing else."""
    graph = FactoryGraph(
        nodes=[
            ExternalSourceNode(id="supply", item_class=fuel_class, rate_per_minute=rate),
            GeneratorNode(id="gen", generator_class=FUEL, fuel_class=fuel_class, count=1.0),
        ]
    )
    graph.connect("supply", "gen", fuel_class, "Build_Pipeline_C", game_data)
    return engine.solve(graph, game_data)


def test_changing_the_fuel_changes_the_appetite_and_not_the_power(
    game_data: GameData,
) -> None:
    """Turbofuel buys the same 250 MW on 7.5 m3/min where fuel needs 20.

    This is the whole reason the fuel belongs to the node and not to the palette
    entry: swapping it rewrites every rate on the thing without touching what it
    puts on the grid.
    """
    on_fuel = _fed_generator("Desc_LiquidFuel_C", 20.0, game_data)
    on_turbo = _fed_generator("Desc_LiquidTurboFuel_C", 7.5, game_data)

    assert on_fuel.node("gen").inputs == {"Desc_LiquidFuel_C": 20.0}
    assert on_turbo.node("gen").inputs == {"Desc_LiquidTurboFuel_C": 7.5}
    assert on_fuel.node("gen").ratio == on_turbo.node("gen").ratio == 1.0
    assert on_fuel.power_production_mw == on_turbo.power_production_mw == 250.0


# --------------------------------------------------------------------------- #
# The balance, and the one error that throttles nothing
# --------------------------------------------------------------------------- #


def _with_generator_count(count: float) -> FactoryGraph:
    graph = load_graph("coal_power")
    node = graph.node("generators")
    assert isinstance(node, GeneratorNode)
    node.count = count
    return graph


def test_a_balance_of_exactly_zero_is_not_a_deficit(game_data: GameData) -> None:
    """One coal generator produces 75 MW, which is what this factory draws."""
    report = engine.solve(_with_generator_count(1.0), game_data)
    assert report.power_production_mw == 75.0
    assert report.power_total_mw == 75.0
    assert report.power_balance_mw == 0.0
    assert not report.has_power_deficit
    assert DiagnosticCode.POWER_DEFICIT not in codes(report)


def test_a_deficit_is_an_error_and_says_so(game_data: GameData) -> None:
    report = engine.solve(_with_generator_count(0.5), game_data)
    assert report.power_production_mw == 37.5
    assert report.power_balance_mw == -37.5
    assert report.has_power_deficit
    finding = next(item for item in report.diagnostics if item.code is DiagnosticCode.POWER_DEFICIT)
    assert finding.severity is Severity.ERROR
    assert "37,5 MW manquants" in finding.message
    assert "ne sont donc pas brides" in finding.message


def test_a_deficit_brides_absolutely_nothing(game_data: GameData) -> None:
    """The point of the whole block, and the only place an error changes no rate.

    The same factory is solved with eight generators and with half of one. The
    second cannot pay its own bill, and both the consumption and the rate of the
    very node that is short of current come out unchanged -- which is exactly what
    would *not* happen if power were a term of the ``min()``.
    """
    healthy = engine.solve(_with_generator_count(8.0), game_data)
    starved = engine.solve(_with_generator_count(0.5), game_data)
    assert starved.has_power_deficit and not healthy.has_power_deficit

    # Consumption does not move: a miner short of current is not a slower miner
    # here, it is a miner in a factory whose grid has tripped.
    assert starved.power_total_mw == healthy.power_total_mw == 75.0
    assert starved.node("coal").power_mw == healthy.node("coal").power_mw == 15.0
    # Every node still runs at the rate its own materials allow, the generator
    # included: it has coal and water to spare, so it runs flat out.
    assert starved.node("generators").ratio == 1.0
    assert starved.node("generators").inputs == {"Desc_Coal_C": 7.5, "Desc_Water_C": 22.5}
    # And the mine simply has a surplus, which is a material finding and not an
    # electrical one.
    assert starved.node("coal").outputs == {"Desc_Coal_C": 7.5}


def test_a_factory_without_a_generator_is_not_in_deficit(game_data: GameData) -> None:
    """Drawing an unpowered factory is the normal way to use the tool."""
    report = solved("iron_plate", game_data)
    assert report.power_total_mw == 21.0
    assert report.power_production_mw == 0.0
    assert not report.has_generators
    assert not report.has_power_deficit
    assert DiagnosticCode.POWER_DEFICIT not in codes(report)


# --------------------------------------------------------------------------- #
# Make-up water is an input like any other
# --------------------------------------------------------------------------- #


def test_a_generator_short_of_water_is_short_of_an_input(game_data: GameData) -> None:
    """One pump instead of three: 120 m3 for 360 wanted, so a third of the power.

    Nothing about this is special-cased. The water is on a pipe, it is allocated by
    the same rule as an ore on a belt, and the shortfall reads as a plain deficit.
    """
    graph = load_graph("coal_power")
    pump = graph.node("pump")
    assert isinstance(pump, WaterExtractorNode)
    pump.count = 1

    report = engine.solve(graph, game_data)
    generators = report.node("generators")
    assert generators.ratio == pytest.approx(1 / 3, abs=1e-9)
    assert generators.limiting is LimitingFactor.INPUTS
    assert generators.starved_items == ("Desc_Water_C",)
    assert generators.inputs["Desc_Water_C"] == 120.0
    assert generators.inputs["Desc_Coal_C"] == pytest.approx(40.0, abs=1e-9)
    assert generators.power_produced_mw == pytest.approx(200.0, abs=1e-9)

    finding = next(item for item in report.diagnostics if item.code is DiagnosticCode.DEFICIT)
    assert finding.node_id == "generators"
    assert "Le generateur tourne a" in finding.message
    assert "240 m³/min manquants sur 360 m³/min requis" in finding.message


def test_an_unwired_fuel_line_is_reported_like_a_missing_ingredient(
    game_data: GameData,
) -> None:
    graph = load_graph("coal_power")
    graph.edges = [edge for edge in graph.edges if edge.item_class != "Desc_Water_C"]
    report = engine.solve(graph, game_data)
    messages = [item.message for item in report.diagnostics if item.node_id == "generators"]
    assert any("Aucune ligne n'apporte Eau" in message for message in messages)
    assert report.node("generators").ratio == 0.0


def test_a_fuel_the_building_refuses_is_an_error_not_a_crash(game_data: GameData) -> None:
    """Only a hand-edited or foreign file can produce this; it must still open."""
    graph = load_graph("coal_power")
    node = graph.node("generators")
    assert isinstance(node, GeneratorNode)
    node.fuel_class = "Desc_LiquidFuel_C"

    report = engine.solve(graph, game_data)
    finding = next(
        item for item in report.diagnostics if item.code is DiagnosticCode.INCOMPATIBLE_RECIPE
    )
    assert finding.node_id == "generators"
    assert "ne brule pas" in finding.message
    assert report.node("generators").power_produced_mw == 0.0
