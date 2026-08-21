"""The two generators that were left out, and the assumption one of them broke.

The nuclear plant did not need a building added: it needed a sentence removed. The
model held that ``PRODUCER_KINDS`` excludes generators because "what it produces is
power, and power does not travel on a line" -- true of the three generators the
catalogue knew, and false of the one it did not. A plant returns fifty uranium
waste per rod **on a belt**.

The geothermal generator is the mirror image: it takes nothing, returns nothing,
and its output depends on the geyser under it. So it is a node kind of its own, for
the same reason a resource well is not a deposit -- a node whose docstring begins
"a bank of generators burning one chosen fuel" cannot be one that burns nothing.
"""

import pytest

from satisplanner.core import breakdown, engine
from satisplanner.core.graph import (
    SCHEMA_VERSION,
    ExternalSourceNode,
    FactoryGraph,
    GeneratorNode,
    GeothermalNode,
    OutputNode,
    node_output_items,
)
from satisplanner.core.models import GameData, Purity
from satisplanner.core.results import NodeSolution, Severity
from satisplanner.data import conversions, db, docs_parser

PLANT = "Build_GeneratorNuclear_C"
GEYSER = "Build_GeneratorGeoThermal_C"
COAL = "Build_GeneratorCoal_C"

URANIUM_ROD = "Desc_NuclearFuelRod_C"
PLUTONIUM_ROD = "Desc_PlutoniumFuelRod_C"
FICSONIUM_ROD = "Desc_FicsoniumFuelRod_C"
URANIUM_WASTE = "Desc_NuclearWaste_C"
PLUTONIUM_WASTE = "Desc_PlutoniumWaste_C"
WATER = "Desc_Water_C"

BELT = "Build_ConveyorBeltMk5_C"
PIPE = "Build_PipelineMK2_C"


@pytest.fixture(scope="module")
def catalogue() -> GameData:
    return db.load_game_data_from_file(db.default_database_path())


def plant(catalogue: GameData, *, fuel: str = URANIUM_ROD, flare: bool = True) -> FactoryGraph:
    """One nuclear plant, fed and watered, with or without a way out for its waste."""
    graph = FactoryGraph()
    graph.add_node(GeneratorNode(id="centrale", generator_class=PLANT, fuel_class=fuel, count=1))
    for item, rate, transport in ((fuel, 1.0, BELT), (WATER, 240.0, PIPE)):
        graph.add_node(ExternalSourceNode(id=f"e{item}", item_class=item, rate_per_minute=rate))
        graph.connect(f"e{item}", "centrale", item, transport, game_data=catalogue)
    waste = catalogue.generators[PLANT].byproducts(fuel)
    if flare and waste:
        item = next(iter(waste))
        graph.add_node(OutputNode(id="torchere", item_class=item, is_sink=True))
        graph.connect("centrale", "torchere", item, BELT, game_data=catalogue)
    return graph


def solved(
    graph: FactoryGraph, catalogue: GameData, node_id: str = "centrale"
) -> NodeSolution:
    report = engine.solve(graph, catalogue)
    return next(node for node in report.nodes if node.node_id == node_id)


# --------------------------------------------------------------------------- #
# What the game files say
# --------------------------------------------------------------------------- #


def test_every_generator_of_the_game_is_in_the_catalogue(catalogue: GameData) -> None:
    """The end of the series: nothing is left out, and the exclusion list is empty."""
    assert not docs_parser.EXCLUDED_GENERATORS
    assert PLANT in catalogue.generators
    assert GEYSER in catalogue.generators


def test_the_plant_produces_two_and_a_half_gigawatts(catalogue: GameData) -> None:
    assert catalogue.generators[PLANT].power_mw == 2500.0


def test_the_plant_drinks_far_more_than_a_coal_generator(catalogue: GameData) -> None:
    """And **not** at the same ratio, which is what the backlog claimed.

    The two are read from the same field, ``mSupplementalToPowerRatio``, which is
    the part that was already modelled. The ratios themselves differ by a factor of
    six -- 10 against 1,6 -- and it is the power they multiply that makes the plant
    thirstier: 240 m3/min against 45.
    """
    coal = catalogue.generators[COAL].fuel("Desc_Coal_C")
    rod = catalogue.generators[PLANT].fuel(URANIUM_ROD)
    assert coal is not None and rod is not None
    assert coal.supplemental_per_minute == 45.0
    assert rod.supplemental_per_minute == 240.0
    assert rod.supplemental_class == WATER


@pytest.mark.parametrize(
    ("fuel", "waste", "per_minute"),
    [
        (URANIUM_ROD, URANIUM_WASTE, 10.0),
        (PLUTONIUM_ROD, PLUTONIUM_WASTE, 1.0),
        (FICSONIUM_ROD, None, 0.0),
    ],
)
def test_each_rod_leaves_what_the_data_says(
    catalogue: GameData, fuel: str, waste: str | None, per_minute: float
) -> None:
    """Derived from the burn rate and the amount per rod, never written down.

    Fifty waste per uranium rod at a fifth of a rod a minute is ten a minute; ten
    per plutonium rod at a tenth of a rod is one. A ficsonium rod leaves nothing,
    which is the whole point of ficsonium.
    """
    produced = catalogue.generators[PLANT].byproducts(fuel)
    assert produced == ({waste: per_minute} if waste else {})


def test_the_geyser_figures_are_the_ones_the_game_prints(catalogue: GameData) -> None:
    """The strongest control in this lot, and it is the game's own text.

    ``Build_GeneratorGeoThermal_C`` declares constant 0 and factor 200, and prints
    in its own description: "Geyser normal - 100 a 300 MW (200 MW en moyenne)".
    Reading those two fields the way the *consumption* ones work -- mean at the
    middle of ``constant`` to ``constant + factor`` -- gives 100 MW and is half the
    truth. Production and consumption do not use them the same way.
    """
    geyser = catalogue.generators[GEYSER]
    assert (geyser.power_min_mw, geyser.power_mw, geyser.power_max_mw) == (100.0, 200.0, 300.0)
    assert geyser.has_purity
    assert conversions.variable_production_mw(0.0, 200.0) == (200.0, 100.0, 300.0)


def test_a_geyser_of_each_purity_matches_the_printed_averages(catalogue: GameData) -> None:
    """100, 200 and 400 MW -- and the multipliers are the catalogue's own, not new ones."""
    geyser = catalogue.generators[GEYSER]
    averages = {purity: geyser.production_at(purity) for purity in Purity}
    assert averages == {Purity.IMPURE: 100.0, Purity.NORMAL: 200.0, Purity.PURE: 400.0}


def test_only_the_geyser_has_a_swing_or_a_purity(catalogue: GameData) -> None:
    """Every other generator holds still and cares nothing for where it stands."""
    for generator in catalogue.generators.values():
        if generator.class_name == GEYSER:
            continue
        assert not generator.is_variable, generator.class_name
        assert not generator.has_purity, generator.class_name


# --------------------------------------------------------------------------- #
# The assumption that had to go
# --------------------------------------------------------------------------- #


def test_a_generator_can_now_put_something_on_a_line(catalogue: GameData) -> None:
    node = GeneratorNode(id="c", generator_class=PLANT, fuel_class=URANIUM_ROD)
    assert node_output_items(node, catalogue) == {URANIUM_WASTE}


def test_the_generators_that_return_nothing_still_return_nothing(catalogue: GameData) -> None:
    """The old comment was right about three of the four; only the fourth broke it."""
    node = GeneratorNode(id="c", generator_class=COAL, fuel_class="Desc_Coal_C")
    assert node_output_items(node, catalogue) == set()


def test_a_geyser_emits_nothing_at_all(catalogue: GameData) -> None:
    node = GeothermalNode(id="g", generator_class=GEYSER)
    assert node_output_items(node, catalogue) == set()


def test_the_plant_runs_and_puts_its_waste_on_the_belt(catalogue: GameData) -> None:
    solution = solved(plant(catalogue), catalogue)
    assert solution.ratio == 1.0
    assert solution.power_produced_mw == 2500.0
    assert solution.outputs == {URANIUM_WASTE: 10.0}


# --------------------------------------------------------------------------- #
# The byproduct rule, on a building that is not a machine
# --------------------------------------------------------------------------- #


def test_a_plant_with_nowhere_to_put_its_waste_stops(catalogue: GameData) -> None:
    """Asked for explicitly: it stops, exactly as a refinery does.

    The game's own description says the plant "shuts down if the fuel supply is
    insufficient" and says nothing about the waste, so the files do not settle it.
    What they do carry is a ``mWasteLeftFromCurrentFuel`` counter and a
    ``GNW_`` warning enum on the class, which is a building that tracks a
    waste-full condition. Modelling it as a blocked byproduct is the reading
    consistent with every other byproduct here, and it stops the plant dead rather
    than letting it produce power out of a full waste buffer.
    """
    report = engine.solve(plant(catalogue, flare=False), catalogue)
    solution = next(node for node in report.nodes if node.node_id == "centrale")
    assert solution.ratio == 0.0
    assert solution.blocked_products == (URANIUM_WASTE,)
    assert solution.power_produced_mw == 0.0


def test_the_stopped_plant_says_why_and_names_itself(catalogue: GameData) -> None:
    """Without this the only findings were two surplus warnings on the sources."""
    report = engine.solve(plant(catalogue, flare=False), catalogue)
    errors = [
        diagnostic
        for diagnostic in report.diagnostics
        if diagnostic.severity is Severity.ERROR and diagnostic.node_id == "centrale"
    ]
    assert len(errors) == 1
    assert "la centrale est totalement bloquée" in errors[0].message


def test_a_ficsonium_rod_needs_no_flare(catalogue: GameData) -> None:
    """It leaves nothing behind, so there is nothing to block it."""
    solution = solved(plant(catalogue, fuel=FICSONIUM_ROD, flare=False), catalogue)
    assert solution.ratio == 1.0
    assert solution.blocked_products == ()
    assert solution.power_produced_mw == 2500.0


def test_a_coal_generator_is_not_blocked_by_anything(catalogue: GameData) -> None:
    """The rule reaches generators now, and must not have caught the other three."""
    graph = FactoryGraph()
    graph.add_node(GeneratorNode(id="g", generator_class=COAL, fuel_class="Desc_Coal_C"))
    for item, rate, transport in (("Desc_Coal_C", 15.0, BELT), (WATER, 45.0, PIPE)):
        graph.add_node(ExternalSourceNode(id=f"e{item}", item_class=item, rate_per_minute=rate))
        graph.connect(f"e{item}", "g", item, transport, game_data=catalogue)
    solution = solved(graph, catalogue, "g")
    assert solution.ratio == 1.0
    assert solution.blocked_products == ()


# --------------------------------------------------------------------------- #
# The geyser as a node
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("purity", "produced"),
    [(Purity.IMPURE, 100.0), (Purity.NORMAL, 200.0), (Purity.PURE, 400.0)],
)
def test_a_geyser_node_produces_its_purity(
    catalogue: GameData, purity: Purity, produced: float
) -> None:
    graph = FactoryGraph()
    graph.add_node(GeothermalNode(id="geyser", generator_class=GEYSER, purity=purity))
    solution = solved(graph, catalogue, "geyser")
    assert solution.power_produced_mw == produced


def test_a_geyser_is_never_starved(catalogue: GameData) -> None:
    """Nothing feeds it, so nothing can hold it back: it runs or it is not there."""
    graph = FactoryGraph()
    graph.add_node(GeothermalNode(id="geyser", generator_class=GEYSER, count=3))
    solution = solved(graph, catalogue, "geyser")
    assert solution.ratio == 1.0
    assert solution.power_produced_mw == 600.0


def test_a_lone_geyser_is_not_called_unconnected(catalogue: GameData) -> None:
    """It never will be wired to anything, so the usual finding would always be wrong.

    "This node is connected to nothing: it takes no part in the calculation" is
    exactly false of a geyser -- it is the two hundred megawatts holding the
    factory up.
    """
    graph = FactoryGraph()
    graph.add_node(GeothermalNode(id="geyser", generator_class=GEYSER))
    report = engine.solve(graph, catalogue)
    assert report.diagnostics == ()
    assert report.power_production_mw == 200.0


def test_a_geyser_costs_nothing_to_run(catalogue: GameData) -> None:
    graph = FactoryGraph()
    graph.add_node(GeothermalNode(id="geyser", generator_class=GEYSER))
    report = engine.solve(graph, catalogue)
    assert report.power_total_mw == 0.0
    assert report.shopping_list.buildings == {GEYSER: 1}


def test_the_geyser_is_offered_apart_from_the_generators(catalogue: GameData) -> None:
    """It burns nothing, so it is not a generator entry: it has a purity, not a fuel."""
    from satisplanner.ui.catalogue import EntryKind, build_entries

    entries = build_entries(catalogue)
    geothermal = [entry for entry in entries if entry.kind is EntryKind.GEOTHERMAL]
    assert [entry.class_name for entry in geothermal] == [GEYSER]
    assert geothermal[0].fuel_class is None
    burners = {entry.class_name for entry in entries if entry.kind is EntryKind.GENERATOR}
    assert GEYSER not in burners
    assert PLANT in burners


def test_a_geyser_purity_is_set_by_the_door_the_interface_uses(catalogue: GameData) -> None:
    from satisplanner.ui import edits
    from satisplanner.ui.document import FactoryDocument

    document = FactoryDocument(catalogue)
    document.graph.add_node(GeothermalNode(id="geyser", generator_class=GEYSER))
    assert edits.set_purity(document, "geyser", Purity.PURE) is None
    node = document.graph.node("geyser")
    assert isinstance(node, GeothermalNode)
    assert node.purity is Purity.PURE
    document.undo_stack.undo()
    node = document.graph.node("geyser")
    assert isinstance(node, GeothermalNode)
    assert node.purity is Purity.NORMAL


# --------------------------------------------------------------------------- #
# The frozen label, and the document
# --------------------------------------------------------------------------- #


def test_the_card_reads_the_plant_instead_of_a_written_label(catalogue: GameData) -> None:
    """``byproduct_of_fr`` was filled from the exclusion list; the list is empty now."""
    assert catalogue.items[URANIUM_WASTE].byproduct_of_fr == ""
    sources = breakdown.generator_sources(catalogue, URANIUM_WASTE)
    assert [(generator.class_name, fuel) for generator, fuel in sources] == [
        (PLANT, URANIUM_ROD)
    ]


def test_the_document_schema_moved_for_the_geyser() -> None:
    """A build that cannot draw one must refuse the file rather than lose 200 MW."""
    assert SCHEMA_VERSION == 9


def test_a_geyser_survives_the_share_code(catalogue: GameData) -> None:
    from satisplanner.data import factory_file

    graph = FactoryGraph()
    graph.add_node(GeothermalNode(id="geyser", generator_class=GEYSER, purity=Purity.PURE, count=2))
    loaded = factory_file.decode_share_code(factory_file.encode_share_code(graph))
    assert loaded.is_clean
    node = loaded.graph.node("geyser")
    assert isinstance(node, GeothermalNode)
    assert (node.purity, node.count) == (Purity.PURE, 2.0)
